"""Tests for the second-round Phase 2-3 fixes (the 7 review items).

Coverage:
  1. RuntimePanel handleAssign: apiPost -> apiPut (verified via
     contract test scan + binding endpoint accepts PUT only for
     upsert in the new contract)
  2. Contract test parser catches template literals + dynamic paths
  3. Binding PUT/PATCH validation:
     - server must exist + reachable
     - server must not be ignored
     - model must be available in the opencode config
     - server_instance_id auto-set from the matching row
     - agent_registry row updated with the new model
  4. stop_all returns per-server outcome; on success the row is
     removed (or status updated); on failure outcome=failed,
     ok=False
  5. runtime/status ready = credentials AND reachable server AND
     all 4 bindings have server_instance_id
  6. RuntimePanel renders unavailable_reason + credential_warnings
     (verified by the build succeeding + grepping for the new JSX)
  7. Regression tests for every reproduce case (this file IS the
     regression suite)
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
    db = tmp_path / "p3v2_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_PORT", "18999")
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    return db


@pytest.fixture()
def valid_credentials(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY_MINIMAX", "sk-test-minimax")
    monkeypatch.setenv("OPENCODE_API_KEY_BAILIAN", "sk-test-bailian")
    from task_hounds_api.opencode import config as oc_config
    oc_config.reset_cache()
    return monkeypatch


@pytest.fixture()
def is_reachable_patch(monkeypatch):
    reachable: set[tuple[str, int]] = set()
    real_is_reachable = None
    from task_hounds_api.opencode import process as oc_process

    def set_reachable(*pairs):
        reachable.clear()
        for p in pairs:
            reachable.add(p)

    def fake_is_reachable(host, port, timeout=1.0):
        return (host, port) in reachable

    monkeypatch.setattr(oc_process, "is_reachable", fake_is_reachable)
    return set_reachable, reachable


@pytest.fixture()
def client(fresh_db, valid_credentials, is_reachable_patch, monkeypatch):
    from task_hounds_api.api import main as api_main
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import lifecycle as oc_lifecycle

    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18999))

    lc = MagicMock()
    lc.return_value.ensure_running.return_value = True
    lc.return_value.is_running.return_value = True
    lc.return_value._proc = None
    lc.return_value.health.return_value = {
        "ok": True, "host": "127.0.0.1", "port": 18999, "pid": None,
    }
    lc.return_value.stop.return_value = None
    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(oc_lifecycle, "OpenCodeLifecycle", lc)

    with TestClient(api_main.create_app()) as c:
        yield c
    rm_mod.RuntimeManager.reset_instance()


# ── Fix 3: Binding PUT validation ────────────────────────────────────────


def test_put_binding_rejects_unreachable_server(client):
    """If no server row exists and the host/port is not reachable,
    PUT /bindings/{role} returns 422 — never silently writes a
    binding pointing at a dead server."""
    r = client.put(
        "/api/runtime/bindings/worker",
        json={"host": "127.0.0.1", "port": 19999},
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.json()}"


def test_put_binding_rejects_ignored_server(client, is_reachable_patch):
    """A server row with status='ignored' must NOT be bindable."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18801))
    client.post("/api/runtime/ignore", json={"host": "127.0.0.1", "port": 18801})
    r = client.put(
        "/api/runtime/bindings/worker",
        json={"host": "127.0.0.1", "port": 18801},
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.json()}"


def test_put_binding_rejects_unknown_model(client, is_reachable_patch):
    """A model id that is not in the opencode config must be rejected
    with 422 so the UI cannot save a binding the executor will fail to
    invoke."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18802))
    client.post("/api/runtime/attach", json={"host": "127.0.0.1", "port": 18802})
    r = client.put(
        "/api/runtime/bindings/worker",
        json={"host": "127.0.0.1", "port": 18802, "model": "totally/unknown-model"},
    )
    assert r.status_code == 422
    assert "model" in r.json()["detail"].lower() or "unknown" in r.json()["detail"].lower()


def test_put_binding_writes_server_instance_id_from_matching_row(
    client, is_reachable_patch
):
    """When a reachable server row exists for the host/port, the
    binding's server_instance_id is set to that row's id."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18803))
    r = client.post("/api/runtime/attach", json={"host": "127.0.0.1", "port": 18803})
    instance_id = r.json()["instance_id"]

    r2 = client.put(
        "/api/runtime/bindings/worker",
        json={"host": "127.0.0.1", "port": 18803},
    )
    assert r2.status_code == 200
    assert r2.json()["server_instance_id"] == instance_id


def test_put_binding_auto_registers_when_reachable_no_row(client, is_reachable_patch):
    """If the host/port is reachable but no row exists, the binding
    endpoint auto-registers the server as external and sets
    server_instance_id to the new row's id."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18804))
    r = client.put(
        "/api/runtime/bindings/worker",
        json={"host": "127.0.0.1", "port": 18804},
    )
    assert r.status_code == 200
    assert r.json()["server_instance_id"] is not None
    assert r.json()["host"] == "127.0.0.1"
    assert r.json()["port"] == 18804


def test_put_binding_syncs_agent_registry_model(client, is_reachable_patch):
    """When PUT /bindings/{role} includes a model, the agent_registry
    row for that role is updated with the same model."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18805))
    client.post("/api/runtime/attach", json={"host": "127.0.0.1", "port": 18805})
    client.put(
        "/api/runtime/bindings/worker",
        json={
            "host": "127.0.0.1",
            "port": 18805,
            "model": "minimax-coding-plan/MiniMax-M2.7",
        },
    )
    from task_hounds_api.db.ops import agent as db_agent
    a = db_agent.get_agent("worker")
    assert a["model"] == "minimax-coding-plan/MiniMax-M2.7"


def test_patch_binding_validates_new_host_port(client, is_reachable_patch):
    """PATCH that changes host/port must go through the same reachability
    validation as PUT."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18806))
    client.post("/api/runtime/attach", json={"host": "127.0.0.1", "port": 18806})
    client.put(
        "/api/runtime/bindings/worker",
        json={"host": "127.0.0.1", "port": 18806},
    )
    r = client.patch(
        "/api/runtime/bindings/worker",
        json={"host": "127.0.0.1", "port": 29999},
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.json()}"


# ── Fix 4: stop_all real results ─────────────────────────────────────────


def test_stop_all_outcome_failed_when_proc_lingers(
    fresh_db, valid_credentials, is_reachable_patch, monkeypatch
):
    """When the managed proc refuses to die, the row's outcome is
    'failed' with ok=False, AND the row is NOT removed (so the
    operator can retry)."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import lifecycle as oc_lifecycle
    from task_hounds_api.db import connect

    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18999))

    class LyingProc:
        pid = 99001
        def poll(self): return None  # still alive
        def kill(self): pass
        def terminate(self): pass
        def wait(self, timeout=None): return 0

    proc = LyingProc()
    lc = MagicMock()
    lc.return_value.ensure_running.return_value = True
    lc.return_value.is_running.return_value = True
    lc.return_value._proc = proc
    lc.return_value.health.return_value = {
        "ok": True, "host": "127.0.0.1", "port": 18999, "pid": 99001,
    }
    lc.return_value.stop.return_value = None
    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(oc_lifecycle, "OpenCodeLifecycle", lc)

    with TestClient(api_main.create_app()) as c:
        c.get("/api/ping")
        r = c.post("/api/runtime/stop-all")
        body = r.json()

    managed_results = [
        r for r in body.get("results", [])
        if r.get("outcome") in ("stopped", "failed", "noop")
        and r.get("instance_id") is not None
    ]
    assert managed_results, f"no managed result: {body}"
    mr = managed_results[0]
    assert mr["outcome"] == "failed", f"expected failed, got {mr}"
    assert mr["ok"] is False, f"expected ok=False, got {mr}"

    with connect() as db:
        row = db.execute(
            "SELECT id FROM opencode_server_instances WHERE owner='power_teams'"
        ).fetchone()
    assert row is not None, "row should remain after failed stop"


def test_stop_all_outcome_stopped_when_proc_dies(
    fresh_db, valid_credentials, is_reachable_patch, monkeypatch
):
    """When the managed proc dies successfully, the row is removed
    and the result entry reports outcome='stopped', ok=True."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import lifecycle as oc_lifecycle
    from task_hounds_api.db import connect

    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18999))

    class DeadProc:
        pid = 99002
        def poll(self): return 0  # dead
        def kill(self): pass
        def terminate(self): pass
        def wait(self, timeout=None): return 0

    proc = DeadProc()
    lc = MagicMock()
    lc.return_value.ensure_running.return_value = True
    lc.return_value.is_running.return_value = True
    lc.return_value._proc = proc
    lc.return_value.health.return_value = {
        "ok": True, "host": "127.0.0.1", "port": 18999, "pid": 99002,
    }
    lc.return_value.stop.return_value = None
    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(oc_lifecycle, "OpenCodeLifecycle", lc)

    with TestClient(api_main.create_app()) as c:
        c.get("/api/ping")
        r = c.post("/api/runtime/stop-all")
        body = r.json()

    managed = [
        r for r in body.get("results", [])
        if r.get("outcome") in ("stopped", "failed", "noop")
        and r.get("instance_id") is not None
    ]
    assert managed, f"no managed result: {body}"
    mr = managed[0]
    assert mr["outcome"] == "stopped", f"expected stopped, got {mr}"
    assert mr["ok"] is True

    with connect() as db:
        row = db.execute(
            "SELECT id FROM opencode_server_instances WHERE owner='power_teams'"
        ).fetchone()
    assert row is None, "row should be removed after successful stop"


# ── Fix 5: runtime/status ready = creds AND server AND bindings ──────────


def test_runtime_status_ready_false_when_no_reachable_server(
    fresh_db, valid_credentials, is_reachable_patch, monkeypatch
):
    """Credentials present but NO server reachable -> ready=False,
    unavailable_reason explains."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import lifecycle as oc_lifecycle

    lc = MagicMock()
    lc.return_value.ensure_running.return_value = False
    lc.return_value.is_running.return_value = False
    lc.return_value._proc = None
    lc.return_value.health.return_value = {
        "ok": False, "host": "127.0.0.1", "port": 18999, "pid": None,
    }
    lc.return_value.stop.return_value = None
    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(oc_lifecycle, "OpenCodeLifecycle", lc)

    set_reachable, _ = is_reachable_patch
    set_reachable()

    with TestClient(api_main.create_app()) as c:
        body = c.get("/api/runtime/status").json()
    assert body["ready"] is False
    assert body["runtime_available"] is False
    assert body["unavailable_reason"] is not None
    assert "server" in body["unavailable_reason"].lower() or "reachable" in body["unavailable_reason"].lower()


def test_runtime_status_ready_false_when_bindings_have_null_server_id(
    fresh_db, valid_credentials, is_reachable_patch, monkeypatch
):
    """Credentials + reachable server but bindings have server_instance_id
    NULL -> ready=False. Forces operators to fix the wiring before
    runtime goes live."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import lifecycle as oc_lifecycle

    lc = MagicMock()
    lc.return_value.ensure_running.return_value = True
    lc.return_value.is_running.return_value = True
    lc.return_value._proc = None
    lc.return_value.health.return_value = {
        "ok": True, "host": "127.0.0.1", "port": 18999, "pid": None,
    }
    lc.return_value.stop.return_value = None
    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(oc_lifecycle, "OpenCodeLifecycle", lc)

    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18999))

    with TestClient(api_main.create_app()) as c:
        c.get("/api/ping")
        from task_hounds_api.db.ops import runtime as db_rt
        # Lifespan auto-binds all 4 roles with server_instance_id=<row>.
        # clear_binding removes those rows so the subsequent upsert is
        # a fresh INSERT (not an UPDATE that would preserve the
        # server_instance_id via COALESCE).
        for role in ("manager", "worker", "reviewer", "chat"):
            db_rt.clear_binding(role)
        for role in ("manager", "worker", "reviewer", "chat"):
            db_rt.upsert_binding(role, "127.0.0.1", 18999, server_instance_id=None)
        body = c.get("/api/runtime/status").json()

    assert body["ready"] is False, f"expected ready=False, got {body['unavailable_reason']}"
    assert "binding" in body["unavailable_reason"].lower() or "server_instance_id" in body["unavailable_reason"].lower()


def test_runtime_status_ready_true_when_all_three_conditions_met(
    client, is_reachable_patch
):
    """Credentials + reachable managed server + 4 bindings with
    server_instance_id -> ready=True."""
    body = client.get("/api/runtime/status").json()
    assert body["ready"] is True
    assert body["unavailable_reason"] is None


# ── Fix 6: UI rendering of unavailable_reason and warnings ─────────────


def test_ui_runtimepanel_renders_unavailable_and_warnings():
    """Static check: the RuntimePanel source must include JSX that
    surfaces unavailable_reason and the credential_warnings list
    when ready is false."""
    ui_src = Path(__file__).resolve().parent.parent / "ui" / "web" / "src" / "components" / "ui" / "RuntimePanel.tsx"
    assert ui_src.exists(), f"UI source not found: {ui_src}"
    text = ui_src.read_text(encoding="utf-8")
    assert "unavailable_reason" in text, "UI must consume unavailable_reason"
    assert "credential_warnings" in text, "UI must consume credential_warnings"
    assert "ready" in text, "UI must check ready flag"
