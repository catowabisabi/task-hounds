"""Phase 2: BackgroundLoop startup-failure integration test.

Regression for the "start_loop returns started:true but the thread
silently dies" bug. After the Phase 2 refactor, start() BLOCKS on
the ensure_managed_running handshake and reports the real outcome.

The state machine must:
  1. Transition stopped -> starting at the start of start()
  2. Set state=failed and last_start_error when the handshake fails
  3. NEVER report started=true when the handshake failed
  4. Surface last_start_error + last_error_at in /api/workflow/status
     so the UI can show a retry button

These tests use the real BackgroundLoop class with a mocked
RuntimeManager that returns False (or raises) on
ensure_managed_running. They do NOT touch the network.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "loop_startup_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_PORT", "18977")
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    return db


@pytest.fixture()
def rm_unreachable(monkeypatch):
    """Mock the RuntimeManager so ensure_managed_running returns False.

    The loop calls `RuntimeManager.instance()` (a classmethod) inside
    the thread body. We must patch the `instance` classmethod to
    return our configured mock, not the constructor."""
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.workflow import loop as loop_mod

    rm = MagicMock()
    rm.ensure_managed_running.return_value = False
    rm.get_managed_health.return_value = {
        "ok": False, "host": "127.0.0.1", "port": 18977, "pid": None,
    }
    monkeypatch.setattr(loop_mod.RuntimeManager, "instance", MagicMock(return_value=rm))
    monkeypatch.setattr(rm_mod.RuntimeManager, "instance", MagicMock(return_value=rm))
    return rm


def test_start_returns_started_false_when_handshake_fails(fresh_db, rm_unreachable):
    """start() must NOT return started:true when ensure_managed_running
    returns False. This is the core Phase 2 regression."""
    from task_hounds_api.workflow.loop import BackgroundLoop
    bg = BackgroundLoop(interval=1)
    result = bg.start()
    assert result["started"] is False, f"start() must report started=False on handshake failure, got {result}"
    assert result["ok"] is False
    assert result["state"] == "failed"
    assert result["error"] is not None
    assert "reachable" in result["error"].lower() or "not" in result["error"].lower(), (
        f"error message should mention the unreachable opencode, got {result['error']!r}"
    )


def test_start_state_transitions_through_starting_to_failed(fresh_db, rm_unreachable):
    """The state machine must end at 'failed' after a failed handshake."""
    from task_hounds_api.workflow.loop import BackgroundLoop
    bg = BackgroundLoop(interval=1)
    assert bg.get_state() == "stopped"
    bg.start()
    assert bg.get_state() == "failed"
    assert bg.get_last_start_error() is not None
    assert bg.get_last_error_at() is not None


def test_start_loop_route_returns_error_not_started_true(
    fresh_db, rm_unreachable, monkeypatch
):
    """The HTTP route /api/workflow/start-loop must surface the
    handshake failure. UI uses this to show a retry banner."""
    from task_hounds_api.api.routes import workflow as workflow_route
    from task_hounds_api.workflow.loop import BackgroundLoop
    from task_hounds_api.workflow import loop as loop_mod
    fresh_bg = BackgroundLoop(interval=1)
    monkeypatch.setattr(workflow_route, "_bg", fresh_bg)
    loop_mod.RuntimeManager.instance = MagicMock(return_value=rm_unreachable)

    from task_hounds_api.api import main as api_main
    with TestClient(api_main.create_app()) as c:
        r = c.post("/api/workflow/start-loop")
        body = r.json()
    assert r.status_code == 200
    assert body["started"] is False, f"start-loop must return started=False, got {body}"
    assert body["state"] == "failed"
    assert body.get("error"), f"start-loop must surface error, got {body}"


def test_status_route_surfaces_loop_state_and_last_start_error(
    fresh_db, rm_unreachable, monkeypatch
):
    """/api/workflow/status must include loop_state, last_start_error,
    last_error_at so the UI can render the retry button."""
    from task_hounds_api.api.routes import workflow as workflow_route
    from task_hounds_api.workflow.loop import BackgroundLoop
    fresh_bg = BackgroundLoop(interval=1)
    monkeypatch.setattr(workflow_route, "_bg", fresh_bg)

    from task_hounds_api.api import main as api_main
    with TestClient(api_main.create_app()) as c:
        # Trigger a failed start
        c.post("/api/workflow/start-loop")
        s = c.get("/api/workflow/status").json()
    assert "loop_state" in s, f"status must include loop_state, got keys={list(s.keys())}"
    assert s["loop_state"] == "failed", f"loop_state must be 'failed', got {s['loop_state']!r}"
    assert "last_start_error" in s
    assert "last_error_at" in s
    assert s["last_start_error"], "last_start_error must be populated after a failed start"
    assert s["last_error_at"], "last_error_at must be populated after a failed start"


def test_loop_does_not_silently_succeed_on_ensure_managed_running_exception(
    fresh_db, monkeypatch
):
    """If ensure_managed_running RAISES (not just returns False), the
    state machine must still transition to 'failed' with the
    exception captured in last_start_error."""
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.workflow import loop as loop_mod

    rm = MagicMock()
    rm.ensure_managed_running.side_effect = RuntimeError("binary not found")
    monkeypatch.setattr(loop_mod.RuntimeManager, "instance", MagicMock(return_value=rm))
    monkeypatch.setattr(rm_mod.RuntimeManager, "instance", MagicMock(return_value=rm))

    from task_hounds_api.workflow.loop import BackgroundLoop
    bg = BackgroundLoop(interval=1)
    result = bg.start()
    assert result["started"] is False
    assert result["state"] == "failed"
    assert "binary not found" in (result["error"] or "")


def test_failed_loop_can_be_retried(fresh_db, monkeypatch):
    """After a failed start, a subsequent start() should be able to
    transition the state machine back to starting (and then to
    running or failed depending on the handshake result)."""
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.workflow import loop as loop_mod

    rm = MagicMock()
    rm.ensure_managed_running.return_value = False
    rm.get_managed_health.return_value = {
        "ok": False, "host": "127.0.0.1", "port": 18977, "pid": None,
    }
    monkeypatch.setattr(loop_mod.RuntimeManager, "instance", MagicMock(return_value=rm))
    monkeypatch.setattr(rm_mod.RuntimeManager, "instance", MagicMock(return_value=rm))

    from task_hounds_api.workflow.loop import BackgroundLoop
    bg = BackgroundLoop(interval=1)
    bg.start()
    assert bg.get_state() == "failed"

    # Now flip the mock to succeed and retry
    rm.ensure_managed_running.return_value = True
    rm.get_managed_health.return_value = {
        "ok": True, "host": "127.0.0.1", "port": 18977, "pid": 99999,
    }
    result = bg.start()
    assert result["started"] is True, f"retry should succeed, got {result}"
    assert result["state"] == "running"
    assert bg.get_state() == "running"
    bg.stop()
