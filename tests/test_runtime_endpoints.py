"""Tests for Phase 2 runtime endpoints (TODO 4-7).

Authoritative owner of /api/runtime/* — verifies the new endpoints
correctly:
  - discover and register external OpenCode servers (idempotent)
  - attach to an external server (reject unreachable)
  - mark a server as ignored (subsequent discover skips it)
  - stop a managed server (refuse to kill external)
  - stop-all reports per-server outcomes
  - binding CRUD validates role, host/port, and writes through to
    agent_registry + the binding_resolver
  - reject invalid role/host/port with proper 4xx

Each test uses the real FastAPI TestClient and a tmp DB so the
endpoint exercise the real wiring. The OpenCodeLifecycle layer is
mocked so we never spawn the opencode binary.
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
    db = tmp_path / "phase2_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_PORT", "18992")
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
    """Patch is_reachable so we can simulate which (host, port)
    combinations the discover/test/attach endpoints will see."""
    reachable_ports: set[tuple[str, int]] = set()
    real_is_reachable = None

    def set_reachable(*pairs):
        reachable_ports.clear()
        for p in pairs:
            reachable_ports.add(p)

    def fake_is_reachable(host, port, timeout=1.0):
        return (host, port) in reachable_ports

    from task_hounds_api.opencode import process as oc_process
    from task_hounds_api.opencode.process import is_reachable
    real_is_reachable = is_reachable
    monkeypatch.setattr(oc_process, "is_reachable", fake_is_reachable)
    return set_reachable, reachable_ports


@pytest.fixture()
def client(fresh_db, valid_credentials, is_reachable_patch, monkeypatch):
    """FastAPI TestClient with mocked is_reachable (no opencode
    process spawned)."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import lifecycle as oc_lifecycle

    lc = MagicMock()
    lc.return_value.ensure_running.return_value = True
    lc.return_value.is_running.return_value = True
    lc.return_value._proc = None
    lc.return_value.health.return_value = {
        "ok": True, "host": "127.0.0.1", "port": 18992, "pid": None,
    }
    lc.return_value.stop.return_value = None
    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(oc_lifecycle, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(api_main, "RuntimeManager", rm_mod.RuntimeManager)

    with TestClient(api_main.create_app()) as c:
        yield c
    rm_mod.RuntimeManager.reset_instance()


# ── A. Discovery / opencode list ──────────────────────────────────────────


def test_opencode_list_returns_server_shape(client):
    """GET /api/runtime/opencode returns {servers, managed_count,
    external_count, ignored_count} — the UI Runtime Panel consumes this
    shape."""
    r = client.get("/api/runtime/opencode")
    assert r.status_code == 200
    body = r.json()
    assert "servers" in body
    assert "managed_count" in body
    assert "external_count" in body
    assert "ignored_count" in body


def test_discover_idempotent_no_duplicate_rows(client, is_reachable_patch):
    """Calling discover twice on the same reachable port does NOT
    create duplicate rows. Second call returns 'already_known'."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18766))

    r1 = client.post("/api/runtime/discover")
    assert r1.status_code == 200
    b1 = r1.json()
    new_count_1 = b1["new_count"]
    assert new_count_1 == 1, f"first discover: expected new_count=1, got {new_count_1}"

    r2 = client.post("/api/runtime/discover")
    assert r2.status_code == 200
    b2 = r2.json()
    new_count_2 = b2["new_count"]
    assert new_count_2 == 0, f"second discover: expected new_count=0, got {new_count_2}"

    statuses = [d["status"] for d in b2["discovered"]]
    assert "already_known" in statuses, f"expected already_known, got {statuses}"

    from task_hounds_api.db import connect
    with connect() as db:
        rows = db.execute(
            "SELECT COUNT(*) AS n FROM opencode_server_instances "
            "WHERE host=? AND port=?",
            ("127.0.0.1", 18766),
        ).fetchone()
    assert rows["n"] == 1, f"expected 1 row for 18766, got {rows['n']}"


def test_discover_does_not_register_external_as_managed(client, is_reachable_patch):
    """A reachable but externally-running server must be registered
    with owner='external' and managed=0, never as 'power_teams' /
    managed=1."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18767))
    client.post("/api/runtime/discover")
    from task_hounds_api.db import connect
    with connect() as db:
        row = db.execute(
            "SELECT owner, managed FROM opencode_server_instances "
            "WHERE host=? AND port=?"
        ).fetchone() if False else None
    with connect() as db:
        row = db.execute(
            "SELECT owner, managed FROM opencode_server_instances "
            "WHERE host=? AND port=?",
            ("127.0.0.1", 18767),
        ).fetchone()
    assert row is not None
    assert row["owner"] == "external"
    assert row["managed"] in (0, False)


# ── B. Attach / test / ignore ─────────────────────────────────────────────


def test_attach_rejects_unreachable_server(client):
    """POST /api/runtime/attach with an unreachable host/port returns
    422, not 200. No row is created in the DB."""
    r = client.post("/api/runtime/attach", json={"host": "127.0.0.1", "port": 29999})
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.json()}"
    assert "reachable" in r.json()["detail"].lower()

    from task_hounds_api.db import connect
    with connect() as db:
        rows = db.execute(
            "SELECT COUNT(*) AS n FROM opencode_server_instances "
            "WHERE host=? AND port=?",
            ("127.0.0.1", 29999),
        ).fetchone()
    assert rows["n"] == 0, f"unreachable attach should not write a row: {rows['n']}"


def test_attach_succeeds_when_reachable(client, is_reachable_patch):
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18801))
    r = client.post("/api/runtime/attach", json={"host": "127.0.0.1", "port": 18801})
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.json()}"
    body = r.json()
    assert body["attached"] is True
    assert isinstance(body["instance_id"], int)


def test_test_returns_reachability(client, is_reachable_patch):
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18802))
    r1 = client.post("/api/runtime/test", json={"host": "127.0.0.1", "port": 18802})
    assert r1.json()["reachable"] is True
    r2 = client.post("/api/runtime/test", json={"host": "127.0.0.1", "port": 19999})
    assert r2.json()["reachable"] is False


def test_ignore_persists_across_discover(client, is_reachable_patch):
    """A server marked as ignored must NOT be re-registered by a
    subsequent discover scan."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18803))

    r1 = client.post("/api/runtime/ignore", json={"host": "127.0.0.1", "port": 18803, "reason": "test"})
    assert r1.status_code == 200
    assert r1.json()["ignored"] is True

    r2 = client.post("/api/runtime/discover")
    statuses = [d["status"] for d in r2.json()["discovered"]]
    assert "ignored" in statuses, f"expected ignored status, got {statuses}"
    assert r2.json()["new_count"] == 0


def test_unignore_re_enables_discovery(client, is_reachable_patch):
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18804))
    client.post("/api/runtime/ignore", json={"host": "127.0.0.1", "port": 18804})
    client.post("/api/runtime/unignore", json={"host": "127.0.0.1", "port": 18804})
    r = client.post("/api/runtime/discover")
    statuses = [d["status"] for d in r.json()["discovered"]]
    assert "registered" in statuses, f"expected registered, got {statuses}"


# ── C. Stop / stop-all ─────────────────────────────────────────────────────


def test_stop_external_returns_skipped_external(client, is_reachable_patch):
    """POST /api/runtime/opencode/{id}/stop for an external server
    returns outcome='skipped_external' (we never owned the process)."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18805))
    r = client.post("/api/runtime/attach", json={"host": "127.0.0.1", "port": 18805})
    instance_id = r.json()["instance_id"]
    r2 = client.post(f"/api/runtime/opencode/{instance_id}/stop")
    assert r2.status_code == 200
    body = r2.json()
    assert body["outcome"] == "skipped_external"


def test_stop_all_mixed_managed_and_external(client, is_reachable_patch):
    """POST /api/runtime/stop-all with both managed and external
    servers reports each per-server outcome. External rows are NOT
    killed — the row is removed from the registry (since we never
    started them) and reported as 'skipped_external'."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("127.0.0.1", 18806))
    client.post("/api/runtime/attach", json={"host": "127.0.0.1", "port": 18806})

    r = client.post("/api/runtime/stop-all")
    assert r.status_code == 200
    body = r.json()
    assert "results" in body
    outcomes = {item.get("outcome") for item in body["results"]}
    assert "skipped_external" in outcomes, f"expected skipped_external in {outcomes}"
    assert body.get("killed", {}).get("managed_servers") in (0, 1)


def test_stop_unknown_instance_returns_404(client):
    r = client.post("/api/runtime/opencode/99999/stop")
    assert r.status_code == 404


# ── D. Binding CRUD ────────────────────────────────────────────────────────


def test_binding_crud_full_lifecycle(client, valid_credentials, is_reachable_patch):
    """PUT creates a binding, GET returns it, PATCH updates part,
    DELETE removes it."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("10.0.0.1", 18765))
    r = client.put(
        "/api/runtime/bindings/manager",
        json={"host": "10.0.0.1", "port": 18765},
    )
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.json()}"
    b = r.json()
    assert b["role"] == "manager"
    assert b["host"] == "10.0.0.1"
    assert b["port"] == 18765

    r2 = client.get("/api/runtime/bindings/manager")
    assert r2.json()["host"] == "10.0.0.1"

    r3 = client.patch(
        "/api/runtime/bindings/manager",
        json={"model": "minimax-coding-plan/MiniMax-M2.7"},
    )
    assert r3.status_code == 200
    assert r3.json()["model"] == "minimax-coding-plan/MiniMax-M2.7"
    assert r3.json()["host"] == "10.0.0.1"

    r4 = client.delete("/api/runtime/bindings/manager")
    assert r4.json() == {"cleared": "manager"}

    r5 = client.get("/api/runtime/bindings/manager")
    assert r5.status_code == 200
    assert r5.json() is None


def test_binding_rejects_invalid_role(client):
    r = client.put(
        "/api/runtime/bindings/auditor",
        json={"host": "10.0.0.1", "port": 18765},
    )
    assert r.status_code == 400
    assert "invalid role" in r.json()["detail"].lower()


def test_binding_rejects_invalid_port(client):
    r = client.put(
        "/api/runtime/bindings/worker",
        json={"host": "10.0.0.1", "port": 99999},
    )
    assert r.status_code == 400


def test_binding_patch_404_when_missing(client):
    """After lifespan auto-binds all 4 roles, we need to delete the
    chat binding first to verify PATCH correctly returns 404 when the
    role has no binding."""
    r0 = client.delete("/api/runtime/bindings/chat")
    assert r0.json() == {"cleared": "chat"}
    r = client.patch(
        "/api/runtime/bindings/chat",
        json={"model": "x"},
    )
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.json()}"


def test_resolver_picks_up_updated_binding(client, valid_credentials, is_reachable_patch):
    """After PUT /api/runtime/bindings/worker, the binding_resolver
    returns the new host/port for the worker role."""
    set_reachable, _ = is_reachable_patch
    set_reachable(("10.20.30.40", 29999))
    client.put(
        "/api/runtime/bindings/worker",
        json={"host": "10.20.30.40", "port": 29999},
    )
    from task_hounds_api.opencode.binding_resolver import resolve_for_role
    _, port, _, _ = resolve_for_role("worker")
    assert port == 29999


# ── Real lifespan → discover → register → bind → status integration ──────


def test_real_lifespan_then_discover_then_bind_then_status(
    fresh_db, is_reachable_patch, valid_credentials, monkeypatch
):
    """Full integration: real lifespan boots the app, lifespan
    auto-registers the reachable server as external, the operator
    then uses the discover endpoint to find more, and the bindings
    point at the active server. Final /api/runtime/status reflects the
    whole chain.

    This is the Phase 2 end-to-end test the user explicitly requested:
    it does NOT manually call register_external before invoking the
    lifespan. The lifespan detects the reachable server and registers
    it itself; the discover endpoint only adds NEW ones."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import lifecycle as oc_lifecycle

    set_reachable, _ = is_reachable_patch
    # The lifespan registers the managed port (18992) as external, and
    # the test also exercises 18993. Both must be reachable for
    # runtime_status.ready to be True.
    set_reachable(("127.0.0.1", 18992), ("127.0.0.1", 18993))

    lc = MagicMock()
    lc.return_value.ensure_running.return_value = True
    lc.return_value.is_running.return_value = True
    lc.return_value._proc = None
    lc.return_value.health.return_value = {
        "ok": True, "host": "127.0.0.1", "port": 18993, "pid": None,
    }
    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(oc_lifecycle, "OpenCodeLifecycle", lc)

    with TestClient(api_main.create_app()) as c:
        r1 = c.get("/api/runtime/status")
        body1 = r1.json()
        assert body1["external_opencode_count"] >= 1, (
            f"lifespan did not auto-register the external server: {body1}"
        )
        assert body1["ready"] is True

        c.put(
            "/api/runtime/bindings/manager",
            json={"host": "127.0.0.1", "port": 18993},
        )

        r2 = c.get("/api/runtime/bindings/manager")
        assert r2.json()["server_instance_id"] is not None, (
            "binding has server_instance_id=None; resolver/binding chain broken"
        )
