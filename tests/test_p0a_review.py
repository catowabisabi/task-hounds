"""Regression tests for the P0-A review findings.

Issues addressed:
  1. Manager and Chat still bypass resolve_for_role()
  4. Already-running external 18765 server is misclassified as managed
  5. auto_bind_four_roles() is called even when ensure_managed_running fails
  6. loop.py must not access RuntimeManager._managed_lifecycle (public API
     required)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "review_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.db import init_db
    init_db()
    return db


# ── Issue 1: Manager + Chat use resolve_for_role ───────────────────────────


def test_manager_caller_uses_resolve_for_role(fresh_db, monkeypatch, valid_credentials):
    """_call_manager must read host/port/agent/model via resolve_for_role,
    not via env-var-only helpers (which would ignore the DB binding)."""
    from task_hounds_api.db.ops import runtime as db_rt
    db_rt.upsert_binding("manager", "10.20.30.40", 29999)

    from task_hounds_api.workflow import executor as exec_mod
    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "output": {"text": '{"input_digest":"x","decision":{},"manager_message":"x","plan":"x","todo_list":[],"suggestion_content":"x","suggestion_verification":"x","handoff_update":{}}'},
            "error": {},
        }

    monkeypatch.setattr(exec_mod.oc_client, "run", fake_run)

    state = exec_mod.M.FlowState(
        flow_input=exec_mod.M.FlowInput(
            power_team_project_id="pt_test",
            project_session_id="ps_test",
            human_directive="hello",
            workspace_path=".",
        ),
        loop_input=exec_mod.M.FlowLoopInput(loop_index=1),
    )
    exec_mod._call_manager(state)

    assert captured.get("host") == "10.20.30.40", f"manager should read binding, got {captured}"
    assert captured.get("port") == 29999


def test_chat_send_uses_resolve_for_role(fresh_db, monkeypatch):
    """chat_agent.send must use resolve_for_role for host/port/model/agent."""
    from task_hounds_api.db.ops import runtime as db_rt
    db_rt.upsert_binding("chat", "10.20.30.50", 30001)

    from task_hounds_api.workflow import chat_agent as chat_mod
    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "output": {"text": "hi"}, "error": {}}

    monkeypatch.setattr(chat_mod.oc_client, "run", fake_run)
    monkeypatch.setattr(chat_mod, "set_agent_state", lambda *a, **kw: None)
    monkeypatch.setattr(chat_mod, "_chat_agent_name", lambda: "fake-agent")

    from task_hounds_api.db import connect
    from task_hounds_api.db.ops import project as db_project
    db_project.create_session("ps_chat", workspace_path=".", name="chat-test")
    db_project.activate_session("ps_chat")

    chat_mod.send("ps_chat", "hello")

    assert captured.get("host") == "10.20.30.50", f"chat should read binding, got {captured}"
    assert captured.get("port") == 30001


# ── Issue 5: only auto-bind when ensure_managed_running succeeds ────────────


def test_lifespan_skips_auto_bind_when_ensure_fails(fresh_db, monkeypatch):
    """When ensure_managed_running returns False, auto_bind_four_roles
    must NOT be called. Otherwise we get bindings pointing at a dead server."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.db.ops import runtime as db_rt
    from fastapi.testclient import TestClient

    rm = MagicMock()
    rm.reconcile_servers.return_value = 0
    rm.ensure_managed_running.return_value = False  # <-- the gate condition
    rm.get_managed_health.return_value = {"ok": False, "host": "127.0.0.1", "port": 18955, "pid": None}
    rm.auto_bind_four_roles.return_value = 4
    rm.stop_all.return_value = {"ok": True, "stopped": True, "killed": {}}
    rm.instance.return_value = rm
    monkeypatch.setattr(api_main, "RuntimeManager", rm)

    with TestClient(api_main.create_app()):
        pass

    rm.auto_bind_four_roles.assert_not_called()


def test_lifespan_calls_auto_bind_when_ensure_succeeds(fresh_db, monkeypatch):
    """When ensure_managed_running returns True and credentials are present,
    auto_bind runs."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.api import create_app
    from fastapi.testclient import TestClient

    rm = MagicMock()
    rm.reconcile_servers.return_value = 0
    rm.ensure_managed_running.return_value = True
    rm.validate_credentials.return_value = []
    rm.get_managed_health.return_value = {"ok": True, "host": "127.0.0.1", "port": 18955, "pid": 88888}
    rm.auto_bind_four_roles.return_value = 4
    rm.stop_all.return_value = {"ok": True, "stopped": True, "killed": {}}
    rm.instance.return_value = rm
    monkeypatch.setattr(api_main, "RuntimeManager", rm)

    with TestClient(create_app()):
        pass

    rm.auto_bind_four_roles.assert_called_once()


# ── Issue 4: don't classify already-running external 18765 as managed ───────


def test_sync_managed_server_row_skips_when_no_pid(fresh_db, monkeypatch):
    """If the lifecycle is reachable but we did not start it (no proc handle),
    do NOT write a row claiming it is 'managed'."""
    from task_hounds_api.opencode import runtime_manager as rm_mod

    fake_proc = None  # we did not start it
    lifecycle = MagicMock()
    lifecycle.return_value.ensure_running.return_value = True
    lifecycle.return_value._proc = fake_proc  # no process handle
    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lifecycle)

    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    rm = RuntimeManager.instance()
    rm.ensure_managed_running()

    from task_hounds_api.db.ops import runtime as db_rt
    rows = [r for r in db_rt.list_servers() if r.get("agent_role") == "managed"]
    assert rows == [], f"managed row written despite no proc handle: {rows}"


# ── Issue 6: public lifecycle API ──────────────────────────────────────────


def test_runtime_manager_has_public_get_managed_lifecycle():
    """RuntimeManager must expose a public get_managed_lifecycle() so callers
    do not need to touch the private _managed_lifecycle attribute."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    assert hasattr(RuntimeManager, "get_managed_lifecycle")
    assert callable(RuntimeManager.instance().get_managed_lifecycle)


def test_loop_does_not_access_private_lifecycle_attr(fresh_db, monkeypatch):
    import re
    loop_path = Path(_CORE) / "task_hounds_api" / "workflow" / "loop.py"
    source = loop_path.read_text(encoding="utf-8")
    stripped = source.replace("get_managed_lifecycle", "")
    bad = re.search(r"\.\_managed_lifecycle\b", stripped)
    assert not bad, (
        f"loop.py still touches ._managed_lifecycle at offset {bad.start()}; "
        "use RuntimeManager.get_managed_lifecycle() instead"
    )
