"""Tests for the second-round P0-A integration gaps.

Issue 2: missing credentials policy — block auto-bind, expose
  runtime_available + unavailable_reason in /api/runtime/status.

Issue 3: auto-register external server in the real lifespan when
  ensure_managed_running detects a reachable server with no proc
  handle. The test must NOT manually call register_external — it
  exercises the real lifespan path.

Issue 4: Manager OpenCode call with missing credentials must fail
  fast (well under the 30s e2e budget) without spawning the
  opencode subprocess. The directive must transition to 'failed'
  promptly.

Issue 5: truly delete the compat duplicate /api/runtime/status route.
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


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "p0a_v2_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_PORT", "18990")
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    return db


@pytest.fixture()
def real_lifecycle_reachable(monkeypatch):
    """Patch OpenCodeLifecycle so ensure_running returns True and the
    process is reachable (health.ok=True) but we never actually started
    it — simulates an externally-running OpenCode server with no
    handle to the PID."""
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.workflow import executor as exec_mod
    from task_hounds_api.workflow import chat_agent as chat_mod

    lc = MagicMock()
    lc.return_value.ensure_running.return_value = True
    lc.return_value.is_running.return_value = True
    lc.return_value._proc = None
    lc.return_value.health.return_value = {
        "ok": True, "host": "127.0.0.1", "port": 18990, "pid": None,
    }
    lc.return_value.stop.return_value = None

    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(loop_mod.oc_lifecycle, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(exec_mod, "oc_client", MagicMock())
    monkeypatch.setattr(chat_mod, "oc_client", MagicMock())
    return {"lc": lc}


@pytest.fixture()
def valid_credentials(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY_MINIMAX", "sk-test-minimax")
    monkeypatch.setenv("OPENCODE_API_KEY_BAILIAN", "sk-test-bailian")
    from task_hounds_api.opencode import config as oc_config
    oc_config.reset_cache()
    return monkeypatch


# ── Issue 2: missing credentials policy ────────────────────────────────────


def test_lifespan_blocks_auto_bind_when_credentials_missing(
    fresh_db, real_lifecycle_reachable, monkeypatch
):
    """When the apiKey placeholders expand to empty strings, the real
    lifespan must NOT call auto_bind_four_roles — otherwise bindings
    point at a server that cannot serve real LLM calls."""
    monkeypatch.delenv("OPENCODE_API_KEY_MINIMAX", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY_BAILIAN", raising=False)

    from task_hounds_api.api import main as api_main
    from task_hounds_api.db.ops import runtime as db_rt

    with TestClient(api_main.create_app()):
        pass

    bindings = db_rt.list_bindings()
    assert bindings == [], (
        f"lifespan wrote bindings despite missing credentials: {bindings}"
    )


def test_runtime_status_shows_unavailable_when_credentials_missing(
    fresh_db, real_lifecycle_reachable, monkeypatch
):
    """When credentials are missing, /api/runtime/status must report
    ready=false with unavailable_reason='missing_credentials' so the
    dashboard shows 'runtime unavailable' instead of 'ready'."""
    monkeypatch.delenv("OPENCODE_API_KEY_MINIMAX", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY_BAILIAN", raising=False)

    from task_hounds_api.api import main as api_main

    with TestClient(api_main.create_app()) as c:
        body = c.get("/api/runtime/status").json()
        assert body.get("ready") is False, f"expected ready=False, got {body.get('ready')}"
        assert body.get("runtime_available") is False
        assert body.get("unavailable_reason") == "missing_credentials", (
            f"expected unavailable_reason='missing_credentials', got {body.get('unavailable_reason')!r}"
        )
        warnings = body.get("managed_health", {}).get("credential_warnings") or []
        assert len(warnings) >= 1, f"credential_warnings empty: {body}"


# ── Issue 3: auto-register external server in real lifespan ────────────────


def test_lifespan_auto_registers_external_when_reachable_no_proc(
    fresh_db, real_lifecycle_reachable
):
    """When ensure_managed_running finds the port reachable but we
    never spawned a process (no proc handle), the real lifespan must
    auto-register that as an external server. The test must NOT call
    register_external manually — that is exactly what was wrong with
    the previous tests."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.db import connect

    with TestClient(api_main.create_app()):
        pass

    with connect() as db:
        ext_rows = db.execute(
            "SELECT * FROM opencode_server_instances WHERE owner='external'"
        ).fetchall()
    assert len(ext_rows) >= 1, (
        f"lifespan did not auto-register external server: {ext_rows}"
    )
    ext = dict(ext_rows[0])
    assert ext["managed"] in (0, False)
    assert ext["port"] == 18990


def test_lifespan_bindings_have_non_null_server_instance_id(
    fresh_db, real_lifecycle_reachable, valid_credentials
):
    """After the real lifespan runs, all 4 bindings must have a
    non-null server_instance_id (pointing at the auto-registered
    external row)."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.db.ops import runtime as db_rt

    with TestClient(api_main.create_app()):
        pass

    bindings = db_rt.list_bindings()
    assert len(bindings) == 4, f"expected 4 bindings, got {bindings}"
    for b in bindings:
        assert b.get("server_instance_id") is not None, (
            f"{b['role']} binding has server_instance_id=None"
        )


# ── Issue 4: Manager call fails fast on missing credentials ──────────────


def test_manager_call_returns_credential_error_without_spawning(
    fresh_db, real_lifecycle_reachable, monkeypatch
):
    """When the opencode subprocess would fail (missing apiKey), the
    Manager call must return a credential error in <2s WITHOUT
    spawning the opencode binary. The test verifies no Popen was
    created with the opencode binary path."""
    monkeypatch.delenv("OPENCODE_API_KEY_MINIMAX", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY_BAILIAN", raising=False)

    from task_hounds_api.workflow import executor as exec_mod
    from task_hounds_api.opencode import client as oc_client_mod
    from task_hounds_api.workflow import models as M
    import time

    spawn_count = [0]

    real_popen = oc_client_mod.subprocess.Popen

    def counting_popen(*args, **kwargs):
        if args and "opencode" in str(args[0]).lower():
            spawn_count[0] += 1
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(oc_client_mod.subprocess, "Popen", counting_popen)

    state = M.FlowState(
        flow_input=M.FlowInput(
            power_team_project_id="pt_test",
            project_session_id="ps_test",
            human_directive="hello",
            workspace_path=".",
        ),
        loop_input=M.FlowLoopInput(loop_index=1),
    )
    t0 = time.monotonic()
    with pytest.raises(RuntimeError) as exc_info:
        exec_mod._call_manager(state)
    elapsed = time.monotonic() - t0

    assert elapsed < 2.0, f"manager call took {elapsed:.2f}s; expected <2s"
    assert "credential" in str(exc_info.value).lower() or "api_key" in str(exc_info.value).lower(), (
        f"error should mention credentials, got: {exc_info.value}"
    )
    assert spawn_count[0] == 0, (
        f"opencode subprocess was spawned {spawn_count[0]} time(s); should be 0"
    )


# ── Issue 5: truly delete the compat duplicate route ──────────────────────


def test_compat_route_fully_removed():
    """The compat.py duplicate /api/runtime/status route must be
    removed entirely (not just stubbed out). The only /api/runtime/status
    handler should be in api.routes.runtime."""
    import inspect
    import task_hounds_api.api.routes.compat as compat_mod
    compat_source = inspect.getsource(compat_mod)
    assert "@router.get(\"/api/runtime/status\"" not in compat_source, (
        "compat.py still has a /api/runtime/status route; remove it"
    )
