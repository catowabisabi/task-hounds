"""Real integration tests for RuntimeManager + startup + bindings.

These tests use the REAL RuntimeManager class (not a MagicMock) and
mock only at the OpenCodeLifecycle layer (the thing that would spawn
the opencode subprocess). They verify real DB writes happen as a
side effect of real code paths.

Regression for the P0-A review finding that "current startup tests
only verify MagicMock calls" — those tests passed even when the
production wiring was wrong, because they mocked the very thing they
were supposed to be testing.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def kill(self):
        self._alive = False


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "real_int_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_PORT", "18970")
    from task_hounds_api.opencode import runtime_manager as rm_mod
    rm_mod.RuntimeManager.reset_instance()
    from task_hounds_api.db import init_db
    init_db()
    return db


@pytest.fixture()
def real_lifecycle_mock(monkeypatch):
    """Patch only the OpenCodeLifecycle class. RuntimeManager itself
    is the real one — it will exercise its real ensure_managed_running,
    auto_bind_four_roles, get_managed_health, etc."""
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.workflow import executor as exec_mod
    from task_hounds_api.workflow import chat_agent as chat_mod

    fake_proc = _FakeProc(pid=55555)
    lc = MagicMock()
    lc.return_value.ensure_running.return_value = True
    lc.return_value.is_running.return_value = True
    lc.return_value._proc = fake_proc
    lc.return_value.health.return_value = {
        "ok": True,
        "host": "127.0.0.1",
        "port": 18970,
        "pid": 55555,
    }
    lc.return_value.stop.return_value = None

    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(loop_mod.oc_lifecycle, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(exec_mod, "oc_client", MagicMock())
    monkeypatch.setattr(chat_mod, "oc_client", MagicMock())
    return {"lc": lc, "proc": fake_proc}


# ── Issue 7a: real RuntimeManager drives real DB state ─────────────────────


def test_real_runtime_manager_writes_managed_server_row(
    real_lifecycle_mock, fresh_db
):
    """Real RuntimeManager.ensure_managed_running + auto_bind_four_roles
    must produce real DB rows: opencode_server_instances (managed) and
    4 agent_runtime_bindings. No MagicMock for RuntimeManager."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    from task_hounds_api.db.ops import runtime as db_rt
    from task_hounds_api.db import connect

    rm = RuntimeManager.instance()
    rm.ensure_managed_running()
    rm.auto_bind_four_roles()

    with connect() as db:
        managed_rows = db.execute(
            "SELECT * FROM opencode_server_instances "
            "WHERE power_teams_session_id='managed'"
        ).fetchall()
    assert len(managed_rows) == 1, f"expected 1 managed row, got {managed_rows}"
    assert managed_rows[0]["pid"] == 55555

    bindings = db_rt.list_bindings()
    by_role = {b["role"]: b for b in bindings}
    for role in ("manager", "worker", "reviewer", "chat"):
        b = by_role[role]
        assert b["host"] == "127.0.0.1"
        assert b["port"] == 18970
        assert b["model"], f"{role} missing model"
        assert b["opencode_agent"], f"{role} missing opencode_agent"


def test_real_runtime_manager_get_managed_health_after_ensure(
    real_lifecycle_mock, fresh_db
):
    """After ensure_managed_running, get_managed_health must report the
    real pid from the lifecycle (not None)."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    rm = RuntimeManager.instance()
    rm.ensure_managed_running()
    health = rm.get_managed_health()
    assert health["ok"] is True
    assert health["pid"] == 55555
    assert health["port"] == 18970


# ── Issue 7b: real startup lifespan uses real RuntimeManager ───────────────


def test_real_lifespan_creates_bindings_and_managed_row(
    real_lifecycle_mock, fresh_db, valid_credentials
):
    """When the FastAPI app boots with the real RuntimeManager
    (not monkeypatched), lifespan must run reconcile → ensure → auto_bind
    and produce real DB rows. This is the integration test for
    Issue 7: the previous startup tests only verified MagicMock calls."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.db.ops import runtime as db_rt
    from task_hounds_api.db import connect

    app = api_main.create_app()
    with TestClient(app) as c:
        c.get("/api/ping")

    with connect() as db:
        managed_rows = db.execute(
            "SELECT * FROM opencode_server_instances "
            "WHERE power_teams_session_id='managed'"
        ).fetchall()
    assert len(managed_rows) == 1, (
        f"real lifespan did not write a managed row: {managed_rows}"
    )
    bindings = db_rt.list_bindings()
    roles = {b["role"] for b in bindings}
    assert roles == {"manager", "worker", "reviewer", "chat"}, (
        f"real lifespan did not write 4 bindings: {roles}"
    )


def test_real_lifespan_does_not_auto_bind_when_ensure_fails(
    real_lifecycle_mock, fresh_db
):
    """When the real OpenCodeLifecycle.ensure_running returns False,
    the real lifespan must NOT write any binding rows."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.db.ops import runtime as db_rt
    from task_hounds_api.opencode import runtime_manager as rm_mod

    real_lifecycle_mock["lc"].return_value.ensure_running.return_value = False
    real_lifecycle_mock["lc"].return_value._proc = None
    real_lifecycle_mock["lc"].return_value.is_running.return_value = False

    app = api_main.create_app()
    with TestClient(app) as c:
        c.get("/api/ping")

    bindings = db_rt.list_bindings()
    assert bindings == [], (
        f"lifespan wrote bindings despite ensure_running returning False: {bindings}"
    )
