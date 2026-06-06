"""Tests for RuntimeManager singleton — the process-wide owner of the
managed OpenCode serve process and the registry of external servers.

Regression for the "every request creates a new OpenCodeLifecycle() so
stop() never finds the original process handle" bug. RuntimeManager is
the single source of truth; all endpoints / loop / executor MUST go
through RuntimeManager.instance().
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


# ── Fixtures ────────────────────────────────────────────────────────────────


class FakeProc:
    """Stand-in for subprocess.Popen that reports alive until killed."""

    def __init__(self, pid: int = 99999) -> None:
        self.pid = pid
        self._alive = True
        self.kill_calls = 0

    def poll(self):
        return None if self._alive else 0

    def kill(self) -> None:
        self.kill_calls += 1
        self._alive = False

    def terminate(self) -> None:
        self.kill_calls += 1
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return 0


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    """Fresh SQLite DB for this test."""
    db_path = tmp_path / "rt_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db_path))
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_PORT", "18955")

    from task_hounds_api.db import init_db
    init_db()
    return db_path


@pytest.fixture()
def fake_oc_lifecycle(monkeypatch):
    """Patch OpenCodeLifecycle inside the runtime_manager module so the
    manager can be tested without spawning a real `opencode serve` process.
    Returns a dict of mock objects for assertions.
    """
    from task_hounds_api.opencode import runtime_manager as rm_mod

    fake_proc = FakeProc(pid=88888)
    lifecycle_mock = MagicMock()
    lifecycle_mock.return_value.health.return_value = {
        "ok": True,
        "host": "127.0.0.1",
        "port": 18955,
        "pid": 88888,
    }
    lifecycle_mock.return_value.ensure_running.return_value = True
    lifecycle_mock.return_value.is_running.return_value = True
    lifecycle_mock.return_value._proc = fake_proc
    # stop() must actually kill the proc — RuntimeManager.stop_managed
    # now checks proc.poll() after stop to detect lingering processes.
    lifecycle_mock.return_value.stop.side_effect = lambda: fake_proc.kill()

    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lifecycle_mock)
    return {"lifecycle": lifecycle_mock, "proc": fake_proc}


@pytest.fixture()
def reset_rm_singleton():
    """Each test gets a fresh RuntimeManager singleton."""
    from task_hounds_api.opencode import runtime_manager as rm_mod

    rm_mod.RuntimeManager.reset_instance()
    yield
    rm_mod.RuntimeManager.reset_instance()


# ── Tests ──────────────────────────────────────────────────────────────────


def test_singleton_returns_same_instance(reset_rm_singleton, tmp_db, fake_oc_lifecycle):
    """RuntimeManager.instance() is idempotent — same object on repeat calls."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    a = RuntimeManager.instance()
    b = RuntimeManager.instance()
    assert a is b


def test_ensure_managed_running_is_idempotent(reset_rm_singleton, tmp_db, fake_oc_lifecycle):
    """Calling ensure_managed_running() twice only constructs OpenCodeLifecycle once."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    rm = RuntimeManager.instance()
    rm.ensure_managed_running()
    rm.ensure_managed_running()
    lifecycle_mock = fake_oc_lifecycle["lifecycle"]
    assert lifecycle_mock.call_count == 1, "OpenCodeLifecycle was constructed twice"


def test_get_managed_health_shape(reset_rm_singleton, tmp_db, fake_oc_lifecycle):
    """get_managed_health() returns {ok, host, port, pid}."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    rm = RuntimeManager.instance()
    rm.ensure_managed_running()
    health = rm.get_managed_health()
    assert set(health.keys()) >= {"ok", "host", "port", "pid"}
    assert health["ok"] is True
    assert health["pid"] == 88888


def test_stop_managed_kills_proc(reset_rm_singleton, tmp_db, fake_oc_lifecycle):
    """stop_managed() invokes lifecycle.stop() and reports (True, '')
    on success (proc.poll() returns 0 after stop)."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    rm = RuntimeManager.instance()
    rm.ensure_managed_running()
    ok, err = rm.stop_managed()
    fake_oc_lifecycle["lifecycle"].return_value.stop.assert_called_once()
    assert ok is True
    assert err == ""


def test_register_external_writes_db_row(reset_rm_singleton, tmp_db, fake_oc_lifecycle):
    """register_external(host, port) inserts a row in opencode_server_instances
    and returns the new id."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    from task_hounds_api.db.ops import runtime as db_rt

    rm = RuntimeManager.instance()
    new_id = rm.register_external("10.0.0.5", 18800)
    assert isinstance(new_id, int)
    assert new_id > 0

    rows = db_rt.list_servers()
    assert any(
        r.get("host") == "10.0.0.5" and r.get("port") == 18800
        for r in rows
    ), f"registered row not found in list_servers: {rows}"


def test_list_servers_includes_managed_and_external(reset_rm_singleton, tmp_db, fake_oc_lifecycle):
    """After ensure_managed_running() + register_external(), list_servers()
    returns both rows."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    rm = RuntimeManager.instance()
    rm.ensure_managed_running()
    rm.register_external("10.0.0.5", 18800)

    servers = rm.list_servers()
    assert len(servers) >= 2, f"expected 2+ servers, got {len(servers)}"


def test_stop_all_returns_kill_counts(reset_rm_singleton, tmp_db, fake_oc_lifecycle):
    """stop_all() returns dict with killed counts for runs and managed servers."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    rm = RuntimeManager.instance()
    rm.ensure_managed_running()
    result = rm.stop_all()
    assert "killed" in result
    killed = result["killed"]
    assert "opencode_runs" in killed
    assert "managed_servers" in killed
    assert killed["managed_servers"] == 1


def test_reconcile_servers_removes_dead_pids(reset_rm_singleton, tmp_db, fake_oc_lifecycle):
    """reconcile_servers() deletes rows whose pid is no longer alive."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    from task_hounds_api.db.ops import runtime as db_rt
    from task_hounds_api.db import connect

    rm = RuntimeManager.instance()
    with connect() as db:
        cur = db.execute(
            """INSERT INTO opencode_server_instances
               (power_teams_session_id, agent_role, host, port, pid, started_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            ("orphan", "orphan-18800", "127.0.0.1", 18800, 999999),
        )
        db.commit()
        dead_id = int(cur.lastrowid)

    removed = rm.reconcile_servers()
    assert removed >= 1
    survivors = {r.get("id") for r in db_rt.list_servers()}
    assert dead_id not in survivors


def test_auto_bind_four_roles_creates_four_rows(reset_rm_singleton, tmp_db, fake_oc_lifecycle):
    """auto_bind_four_roles() upserts manager/worker/reviewer/chat bindings."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    from task_hounds_api.db.ops import runtime as db_rt

    rm = RuntimeManager.instance()
    rm.ensure_managed_running()
    n = rm.auto_bind_four_roles()
    assert n == 4

    bindings = {b["role"]: b for b in db_rt.list_bindings()}
    assert set(bindings.keys()) == {"manager", "worker", "reviewer", "chat"}
    for role in ("manager", "worker", "reviewer", "chat"):
        assert bindings[role]["port"] == 18955
