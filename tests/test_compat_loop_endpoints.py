"""Tests for compat loop endpoints delegating to BackgroundLoop.

Regression for the "UI button reports success but backend does nothing"
bug. Before this commit, /api/loop/start, /api/loop/stop,
/api/loop/status, /api/run-cycle, and /api/runtime/stop-all in
compat.py were all stubs returning static dicts. They never touched
the BackgroundLoop singleton, never killed the OpenCode subprocess,
and stop-all returned a shape the UI couldn't parse.

These tests use FastAPI TestClient (in-process, real app, real
handler chain) and pin the new contract:

  S1: POST /api/loop/start  -> loop is actually running
  S2: POST /api/loop/stop   -> status immediately false (regression for 0349864)
  S3: POST /api/runtime/stop-all -> {ok, results, stopped} UI shape
  S4: POST /api/run-cycle   -> no-op when no pending directive, doesn't crash
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def client(monkeypatch):
    """Fresh BackgroundLoop per test, and mock ensure_running so the
    loop thread doesn't try to spawn the OpenCode binary (which may not
    be installed in the test env, and the 30s wait_for_ready would
    hang the test)."""
    from task_hounds_api.api.routes import workflow as workflow_route
    from task_hounds_api.api import create_app
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.workflow.loop import BackgroundLoop

    fresh = BackgroundLoop()
    monkeypatch.setattr(workflow_route, "_bg", fresh)
    monkeypatch.setattr(loop_mod.oc_lifecycle, "OpenCodeLifecycle", loop_mod.oc_lifecycle.OpenCodeLifecycle)
    monkeypatch.setattr(
        "task_hounds_api.workflow.loop.oc_lifecycle.OpenCodeLifecycle.ensure_running",
        lambda self: True,
    )

    app = create_app()
    with TestClient(app) as c:
        yield c

    try:
        fresh.stop()
    except Exception:
        pass


def test_compat_loop_start_runs_real_loop(client):
    """S1: /api/loop/start actually delegates to BackgroundLoop.start()
    and is_running() returns True."""
    r = client.post("/api/loop/start")
    assert r.status_code == 200
    body = r.json()
    assert body.get("started") is True

    # Give the thread a moment to spin up
    time.sleep(0.2)

    r = client.get("/api/loop/status")
    assert r.status_code == 200
    status = r.json()
    assert status.get("running") is True
    assert status.get("loop_running") is True


def test_compat_loop_stop_immediately_sets_status_false(client):
    """S2: /api/loop/stop sets status to False immediately (regression
    for the is_running() bug fixed in commit 0349864)."""
    client.post("/api/loop/start")
    time.sleep(0.2)
    # Confirm it's running first
    assert client.get("/api/loop/status").json().get("running") is True

    r = client.post("/api/loop/stop")
    assert r.status_code == 200
    body = r.json()
    assert body.get("stopping") is True
    assert body.get("current_run_cancel_requested") is True
    assert isinstance(body.get("current_run_killed"), bool)

    # Status should immediately be False after stop
    r = client.get("/api/loop/status")
    assert r.json().get("running") is False


def test_compat_loop_already_running(client):
    """Starting a loop when one is already running returns
    started=True with reason='already running' (Phase 2 contract:
    the loop IS running, so yes it was 'started' -- the second call
    was a no-op idempotent confirmation, not a failure)."""
    client.post("/api/loop/start")
    time.sleep(0.2)

    r = client.post("/api/loop/start")
    assert r.status_code == 200
    body = r.json()
    assert body.get("started") is True
    assert body.get("reason") == "already running"


def test_runtime_stop_all_returns_ui_shape(client):
    """S3: /api/runtime/stop-all returns the shape RuntimePanel.tsx
    and BackgroundServerModal.tsx consume:
      {ok: bool, results: [{server_id, ok, error?}], stopped: bool}

    Top-level `ok` is True when the request completed without a
    managed-server failure. The `client` fixture does not mock the
    RuntimeManager's OpenCodeLifecycle, so the lifespan's managed
    proc may not exist (binary not installed in test env) — that is
    a no-op, NOT a failure, so ok=True is the correct contract.
    `stopped` reflects whether a managed server was actually killed.
    """
    r = client.post("/api/runtime/stop-all")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert "results" in body
    assert isinstance(body["results"], list)
    for item in body["results"]:
        assert "server_id" in item
        assert "ok" in item
    # `stopped` is a bool that the UI uses to decide whether to
    # celebrate (e.g. flash "Stopped N servers"). It may be False
    # when the managed server was already not running.
    assert isinstance(body.get("stopped"), bool)


def test_run_cycle_no_op_when_no_directive(client):
    """S4: /api/run-cycle with no pending directive returns a valid
    response (no-op shape) and doesn't crash. May be 200 with ok:true
    and no work done, or any non-error response."""
    r = client.post("/api/run-cycle")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)


def test_run_cycle_no_directive_does_not_start_opencode(client, monkeypatch):
    """No pending work should be a cheap no-op.

    Regression for run_once() starting OpenCode before checking whether
    there is any directive to process. That made /api/run-cycle fail in
    installs without OpenCode even when there was no work to run.
    """
    from task_hounds_api.workflow import loop as loop_mod

    monkeypatch.setattr(
        loop_mod.db_project,
        "get_active_session",
        lambda: {"id": "ps_test", "workspace_path": ""},
    )
    monkeypatch.setattr(
        loop_mod.db_chat,
        "claim_pending_directive",
        lambda session_id: None,
    )

    def fail_if_started(self):
        raise AssertionError("OpenCode should not start when no directive is pending")

    monkeypatch.setattr(
        loop_mod.oc_lifecycle.OpenCodeLifecycle,
        "ensure_running",
        fail_if_started,
    )

    r = client.post("/api/run-cycle")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "ran": False, "result": None}


def test_compat_loop_status_shape(client):
    """GET /api/loop/status returns the LoopStatus shape the UI expects:
      {running: bool, pid: number | null, loop_running: bool}"""
    r = client.get("/api/loop/status")
    assert r.status_code == 200
    body = r.json()
    assert "running" in body
    assert isinstance(body["running"], bool)
    # pid is optional but if present must be int or null
    if "pid" in body:
        assert body["pid"] is None or isinstance(body["pid"], int)
    # Backward-compat: loop_running also present
    assert "loop_running" in body
    assert isinstance(body["loop_running"], bool)
