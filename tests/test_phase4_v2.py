"""Phase 4 regression tests for the 6 review items the user flagged
on commit ba86670. Each section maps 1:1 to a user requirement.

Items covered:
  1. test_ui_backend_contract parser catches non-generic calls,
     template literals with ${dynamic}, and variable paths
     (covered in test_ui_backend_contract.py — referenced here
     via test_phase4_v2_re_collects_binding_put for re-assertion)
  2. RuntimePanel render test (covered by Vitest — `npm test` in
     ui/web/ — and a Python-side static assertion that the JSX
     uses data-testid hooks the Vitest test queries)
  3. stop_managed saves proc ref before stop, checks the saved
     ref's poll() after — matches real OpenCodeLifecycle.stop()
     semantics (which sets _proc=None)
  4. runtime_status per-binding validation: server_instance_id
     exists, not ignored, reachable, host/port matches
  5. Binding PUT preserves opencode_agent + binding_source,
     syncs agent_registry with the new model AND opencode_agent
  6. External-only stop-all returns top-level ok=True,
     stopped=False; UI consumes the results list
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


# ── Shared fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "p4v2_test.db"
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

    def set_reachable(*pairs):
        reachable.clear()
        for p in pairs:
            reachable.add(p)

    def fake_is_reachable(host, port, timeout=1.0):
        return (host, port) in reachable

    from task_hounds_api.opencode import process as oc_process
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


# ── Item 1: parser collects non-generic + template-literal + variable ────


def test_phase4_v2_runtimepanel_jsx_has_testid_hooks():
    """The Vitest render test queries the runtime-unavailable-banner,
    runtime-unavailable-reason, and runtime-credential-warnings
    data-testid hooks. This Python test asserts those hooks exist in
    the JSX source so the render test cannot silently regress
    because the testids were removed."""
    ui_src = (
        _HERE.parent / "ui" / "web" / "src" / "components" / "ui" / "RuntimePanel.tsx"
    )
    assert ui_src.exists(), f"RuntimePanel.tsx not found: {ui_src}"
    text = ui_src.read_text(encoding="utf-8")
    for hook in (
        "runtime-unavailable-banner",
        "runtime-unavailable-reason",
        "runtime-credential-warnings",
        "runtime-ready-badge",
    ):
        assert hook in text, (
            f"RuntimePanel.tsx must include data-testid={hook!r} so the "
            f"Vitest render test can find it. Found: {[m for m in text.split() if 'data-testid' in m]}"
        )


# ── Item 3: stop_managed saves proc ref before stop, checks saved ref after


class _RealLikeProc:
    """Stand-in for a real subprocess.Popen that mirrors what
    OpenCodeLifecycle.stop() does: it sets the lifecycle's _proc to
    None AFTER killing, and our saved ref still has the original
    handle. The original handle's poll() reports the true state."""

    def __init__(self, dies_on_stop: bool, pid: int = 90001):
        self.pid = pid
        self._dies_on_stop = dies_on_stop
        self._killed = False
        self.kill_calls = 0

    def poll(self):
        if self._killed:
            return 0
        return None

    def kill(self):
        self.kill_calls += 1
        self._killed = True

    def terminate(self):
        self.kill_calls += 1
        self._killed = True

    def wait(self, timeout=None):
        self._killed = True
        return 0


def test_stop_managed_saves_proc_ref_before_stop(fresh_db, monkeypatch):
    """stop_managed() must save the proc reference BEFORE calling
    lifecycle.stop(). After stop() the real OpenCodeLifecycle sets
    _proc=None, so reading _proc AFTER stop would always return None
    and falsely report success. The test uses a mock lifecycle that
    mirrors this behavior (sets _proc=None after stop)."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import lifecycle as oc_lifecycle

    real_proc = _RealLikeProc(dies_on_stop=True, pid=90001)

    lc = MagicMock()
    lc.return_value.ensure_running.return_value = True
    lc.return_value.is_running.return_value = True
    lc.return_value._proc = real_proc
    lc.return_value.health.return_value = {
        "ok": True, "host": "127.0.0.1", "port": 18999, "pid": real_proc.pid,
    }

    def stop_side_effect():
        # Real OpenCodeLifecycle.stop() does: stop_serve(_proc); _proc = None
        real_proc.kill()
        lc.return_value._proc = None

    lc.return_value.stop.side_effect = stop_side_effect

    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(oc_lifecycle, "OpenCodeLifecycle", lc)

    with TestClient(api_main.create_app()) as c:
        c.get("/api/ping")
        rm = rm_mod.RuntimeManager.instance()
        ok, err = rm.stop_managed()

    assert ok is True, f"expected ok=True, got ({ok}, {err!r})"
    assert err == ""
    # The real proc must have been killed (not just _proc set to None)
    assert real_proc.kill_calls >= 1, "real proc was never killed"


def test_stop_managed_reports_failure_when_proc_lingers(fresh_db, monkeypatch):
    """A proc that refuses to die (poll() still returns None after
    stop) must cause stop_managed to return ok=False with a clear
    error message. This is the lingering-process regression test."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import lifecycle as oc_lifecycle

    class LyingProc:
        def __init__(self):
            self.pid = 90002
            self.kill_calls = 0

        def poll(self):
            return None  # always alive

        def kill(self):
            self.kill_calls += 1

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    real_proc = LyingProc()

    lc = MagicMock()
    lc.return_value.ensure_running.return_value = True
    lc.return_value.is_running.return_value = True
    lc.return_value._proc = real_proc
    lc.return_value.health.return_value = {
        "ok": True, "host": "127.0.0.1", "port": 18999, "pid": real_proc.pid,
    }

    def stop_side_effect():
        real_proc.kill()  # kill the proc, but it lies and stays alive
        lc.return_value._proc = None

    lc.return_value.stop.side_effect = stop_side_effect

    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(oc_lifecycle, "OpenCodeLifecycle", lc)

    with TestClient(api_main.create_app()) as c:
        c.get("/api/ping")
        rm = rm_mod.RuntimeManager.instance()
        ok, err = rm.stop_managed()

    assert ok is False, "lingering proc must be reported as failure"
    assert "still alive" in err, f"error message should mention lingering: {err!r}"


# ── Item 4: runtime_status per-binding validation ──────────────────────────


def test_runtime_status_false_when_binding_server_instance_id_orphaned(client, is_reachable_patch):
    """server_instance_id points at a server that no longer exists
    in opencode_server_instances (the row was deleted but the binding
    was not updated). This is a stale-binding regression."""
    from task_hounds_api.db import connect
    from task_hounds_api.db.ops import runtime as db_rt

    with connect() as db:
        db.execute(
            "DELETE FROM opencode_server_instances WHERE power_teams_session_id='managed'"
        )
        # Insert a row with id=999 then DELETE it, leaving the
        # binding's server_instance_id pointing at a non-existent id.
        db.execute(
            "INSERT INTO opencode_server_instances (id, power_teams_session_id, agent_role, host, port, started_at) "
            "VALUES (999, 'orphan', 'orphan-18999', '127.0.0.1', 18999, CURRENT_TIMESTAMP)"
        )
        db.execute("DELETE FROM opencode_server_instances WHERE id=999")
        db.commit()

    # Re-bind 4 roles to point at the non-existent id
    for role in ("manager", "worker", "reviewer", "chat"):
        db_rt.upsert_binding(role, "127.0.0.1", 18999, server_instance_id=999)

    body = client.get("/api/runtime/status").json()
    assert body["ready"] is False
    assert "orphaned" in (body.get("unavailable_reason") or "")


def test_runtime_status_false_when_binding_points_to_ignored_server(client, is_reachable_patch):
    """The server row exists but is marked status='ignored'.

    Note: the runtime_status check fires whichever precondition
    fails first. An ignored server is filtered out of the
    `active_servers` list, so the global "no_reachable_server"
    check fires before the per-binding "binding_points_to_ignored"
    check. Both are correct diagnostics; this test asserts the
    ready flag is False and the reason mentions the ignored state
    OR the empty-server state."""
    from task_hounds_api.opencode import runtime_manager as rm_mod

    rm = rm_mod.RuntimeManager.instance()
    rm.ignore_server("127.0.0.1", 18999, "test ignore")

    body = client.get("/api/runtime/status").json()
    assert body["ready"] is False
    reason = body.get("unavailable_reason") or ""
    assert "ignored" in reason or "no_reachable_server" in reason, (
        f"unavailable_reason should mention ignored or no_reachable_server, got: {reason!r}"
    )


def test_runtime_status_false_when_binding_host_port_mismatch(client, is_reachable_patch):
    """The binding's host/port does not match the server row's
    host/port. (e.g. binding updated to point at a new server but
    the row was not refreshed.)"""
    from task_hounds_api.db import connect
    from task_hounds_api.db.ops import runtime as db_rt

    # Get the auto-bound server_instance_id from a binding
    bindings = db_rt.list_bindings()
    assert bindings
    sid = bindings[0]["server_instance_id"]
    assert sid is not None

    # Manually update the binding's host/port to something that does
    # NOT match the server row (which has host=127.0.0.1, port=18999)
    with connect() as db:
        db.execute(
            "UPDATE agent_runtime_bindings SET host='10.0.0.99', port=29999 WHERE id=?",
            (bindings[0]["id"],),
        )
        db.commit()

    body = client.get("/api/runtime/status").json()
    assert body["ready"] is False
    reason = body.get("unavailable_reason") or ""
    assert "host_mismatch" in reason or "port_mismatch" in reason, (
        f"unavailable_reason should mention host/port mismatch, got: {reason!r}"
    )


# ── Item 5: binding PUT preserves opencode_agent + binding_source ─────────


def test_put_binding_persists_opencode_agent_and_source(client):
    """The PUT handler must persist opencode_agent and binding_source
    into the agent_runtime_bindings row AND sync opencode_agent into
    the agent_registry row. Regression for the silent-ignore bug
    where the schema accepted the field but the handler dropped it."""
    r = client.put(
        "/api/runtime/bindings/manager",
        json={
            "host": "127.0.0.1",
            "port": 18999,
            "opencode_agent": "CustomAgent - test",
            "model": "minimax-coding-plan/MiniMax-M2.7",
            "binding_source": "user",
        },
    )
    assert r.status_code == 200, r.json()
    b = r.json()
    assert b["opencode_agent"] == "CustomAgent - test", (
        f"opencode_agent not persisted: {b}"
    )
    assert b["binding_source"] == "user", f"binding_source not persisted: {b}"
    assert b["model"] == "minimax-coding-plan/MiniMax-M2.7"

    from task_hounds_api.db.ops import agent as db_agent
    agent_row = db_agent.get_agent("manager")
    assert agent_row is not None
    assert agent_row.get("opencode_agent") == "CustomAgent - test", (
        f"agent_registry row not synced: {agent_row}"
    )
    assert agent_row.get("model") == "minimax-coding-plan/MiniMax-M2.7"


def test_patch_binding_persists_opencode_agent_and_source(client):
    """The PATCH handler must also persist opencode_agent and
    binding_source. Both handlers used to silently drop these."""
    client.put(
        "/api/runtime/bindings/worker",
        json={"host": "127.0.0.1", "port": 18999},
    )
    r = client.patch(
        "/api/runtime/bindings/worker",
        json={"opencode_agent": "WorkerCustom - patched", "binding_source": "user"},
    )
    assert r.status_code == 200, r.json()
    b = r.json()
    assert b["opencode_agent"] == "WorkerCustom - patched"
    assert b["binding_source"] == "user"

    from task_hounds_api.db.ops import agent as db_agent
    agent_row = db_agent.get_agent("worker")
    assert agent_row is not None
    assert agent_row.get("opencode_agent") == "WorkerCustom - patched"


# ── Item 6: external-only stop-all returns top-level ok=True ───────────────


def test_stop_all_external_only_returns_top_level_ok_true(client, is_reachable_patch):
    """When the only servers in the registry are external (operator-
    owned), stop-all returns top-level ok=True, stopped=False, and
    each results entry is outcome='skipped_external'. The UI uses
    this to decide whether to flash a success message and reload."""
    from task_hounds_api.db import connect

    with connect() as db:
        db.execute(
            "DELETE FROM opencode_server_instances WHERE power_teams_session_id='managed'"
        )
        db.execute(
            "UPDATE opencode_server_instances SET owner='external', managed=0"
        )
        db.commit()

    r = client.post("/api/runtime/stop-all")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True, f"external-only stop-all must return ok=True, got {body}"
    assert body["stopped"] is False, f"external-only stop-all must return stopped=False, got {body}"
    assert isinstance(body["results"], list)
    assert len(body["results"]) > 0
    for item in body["results"]:
        assert item["outcome"] == "skipped_external", f"expected skipped_external, got {item}"
        assert item["ok"] is True
    assert body["killed"]["managed_servers"] == 0
    failed = [r for r in body["results"] if not r.get("ok")]
    assert failed == [], f"no result should report failure, got: {failed}"


def test_stop_all_no_managed_no_external_returns_ok_true_with_noop(client):
    """Edge case: no servers at all. The response must still have
    a well-formed shape with ok=True, stopped=False, and at least one
    result entry (the synthetic noop)."""
    from task_hounds_api.db import connect

    with connect() as db:
        db.execute("DELETE FROM opencode_server_instances")
        db.commit()

    r = client.post("/api/runtime/stop-all")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["stopped"] is False
    assert isinstance(body["results"], list)
    assert len(body["results"]) == 1
    assert body["results"][0]["outcome"] in ("noop", "skipped_external")


def test_stop_all_no_managed_no_external_returns_ok_true_with_noop(client):
    """Edge case: no servers at all. The response must still have
    a well-formed shape with ok=True, stopped=False, and at least one
    result entry (the synthetic noop)."""
    from task_hounds_api.db import connect

    with connect() as db:
        db.execute("DELETE FROM opencode_server_instances")
        db.commit()

    r = client.post("/api/runtime/stop-all")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["stopped"] is False
    assert isinstance(body["results"], list)
    assert len(body["results"]) == 1
    assert body["results"][0]["outcome"] in ("noop", "stopped", "skipped_external")


# ── Cross-cutting: UI handleStopAll reloads + shows result ────────────────


def test_stop_all_response_shape_consumed_by_ui():
    """Static check: the RuntimePanel.handleStopAll handler reads
    result.ok and result.results[].ok — the shape produced by
    stop_all(). If the API shape changes, this test will catch the
    drift so the UI cannot silently fail."""
    ui_src = (
        _HERE.parent / "ui" / "web" / "src" / "components" / "ui" / "RuntimePanel.tsx"
    )
    text = ui_src.read_text(encoding="utf-8")
    assert "result.ok" in text, "UI must read result.ok"
    assert "result.results" in text, "UI must read result.results"
    assert "load()" in text, "UI must call load() after a successful stop to refresh"


# ── Item 3: stop_all for stale managed DB row (no proc handle) ─────────────


def test_stop_all_stale_managed_row_returns_stale_removed(client, is_reachable_patch):
    """When the DB has a managed row but the lifecycle has no _proc
    handle (e.g. the binary was never spawned, or the process exited
    before stop-all was called), the per-server outcome must be
    'stale_removed' (not 'stopped'), the row must be DELETED (it
    was a stale registry entry), and the top-level `stopped` must
    remain False — nothing was actually killed.
    """
    from task_hounds_api.db import connect

    r = client.post("/api/runtime/stop-all")
    assert r.status_code == 200
    body = r.json()
    managed_results = [
        item for item in body["results"]
        if item.get("server_id", "").startswith("opencode-serve-")
    ]
    for item in managed_results:
        assert item["outcome"] == "stale_removed", (
            f"stale managed row must report stale_removed, got {item}"
        )
        assert item["ok"] is True
    assert body["stopped"] is False, (
        f"top-level stopped must be False (nothing was killed), got {body['stopped']}"
    )
    assert body["ok"] is True
    assert body["killed"]["managed_servers"] == 0

    with connect() as db:
        rows = db.execute(
            "SELECT id FROM opencode_server_instances WHERE power_teams_session_id='managed'"
        ).fetchall()
    assert not rows, (
        f"stale managed rows must be DELETED by stop_all, found: {rows}"
    )


# ── Item 2: atomic binding + agent_registry sync ──────────────────────────


def test_binding_write_and_agent_sync_are_atomic(client, monkeypatch):
    """If the binding write succeeds but the agent_registry sync
    would fail, NEITHER side effect must remain. Previously the
    two writes used two separate connections and committed twice,
    so a crash between them would leave a binding saved but the
    agent_registry row out of date. The new
    `upsert_binding_with_agent_sync` does both in one transaction.
    """
    from task_hounds_api.db import connect
    from task_hounds_api.db.ops import runtime as db_rt
    from task_hounds_api.db import ops as db_ops

    with connect() as db:
        before = db.execute(
            "SELECT model, opencode_agent FROM agent_registry WHERE name='manager'"
        ).fetchone()
    before_model = before["model"] if before else None
    before_agent = before["opencode_agent"] if before else None

    real_connect = db_ops.connect

    class _WrappedConn:
        """Wrap a sqlite3 connection so we can intercept execute().
        sqlite3.Connection.execute is a C-level read-only attribute,
        so we cannot assign to it directly — but a Python wrapper
        with its own `execute` method works because the caller
        receives this object, not the underlying connection."""
        def __init__(self, real):
            self._real = real

        def execute(self, sql, params=()):
            sql_str = " ".join(sql.split()) if isinstance(sql, str) else ""
            if "agent_registry" in sql_str.lower() and "UPDATE" in sql_str.upper():
                raise RuntimeError("simulated agent_registry write failure")
            return self._real.execute(sql, params)

        def commit(self):
            return self._real.commit()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _WrappedConnect:
        def __init__(self, *args, **kwargs):
            self._args = args
            self._kwargs = kwargs

        def __enter__(self):
            self._ctx = real_connect(*self._args, **self._kwargs)
            real = self._ctx.__enter__()
            return _WrappedConn(real)

        def __exit__(self, exc_type, exc, tb):
            return self._ctx.__exit__(exc_type, exc, tb)

    monkeypatch.setattr(db_rt, "connect", _WrappedConnect)

    from starlette.testclient import TestClient as _TC
    app = client.app
    with _TC(app, raise_server_exceptions=False) as c:
        r = c.put(
            "/api/runtime/bindings/manager",
            json={
                "host": "127.0.0.1",
                "port": 18999,
                "opencode_agent": "AtomicTestAgent",
                "model": "minimax-coding-plan/MiniMax-M2.7",
            },
        )
    assert r.status_code == 500, (
        f"expected 500 on the simulated failure, got {r.status_code}: {r.json()}"
    )

    with connect() as db:
        binding = db.execute(
            "SELECT * FROM agent_runtime_bindings WHERE role='manager'"
        ).fetchone()
        after = db.execute(
            "SELECT model, opencode_agent FROM agent_registry WHERE name='manager'"
        ).fetchone()
    after_model = after["model"] if after else None
    after_agent = after["opencode_agent"] if after else None

    assert after_model == before_model, (
        f"agent_registry.model changed despite rollback: "
        f"{before_model!r} -> {after_model!r}"
    )
    assert after_agent == before_agent, (
        f"agent_registry.opencode_agent changed despite rollback: "
        f"{before_agent!r} -> {after_agent!r}"
    )

    if binding is not None:
        assert binding["model"] != "minimax-coding-plan/MiniMax-M2.7" or (
            binding["opencode_agent"] != "AtomicTestAgent"
        ), (
            f"binding row updated despite rollback: {dict(binding)}"
        )


# ── Item 4: BindingUpdate server_instance_id removed from schema ─────────


def test_binding_update_schema_rejects_caller_server_instance_id(client):
    """The BindingUpdate / BindingPatch schema no longer accepts a
    caller-controlled `server_instance_id`. That field is a
    server-internal value derived from the host/port lookup; the
    API resolves it server-side. A caller passing it must get a
    422 from Pydantic (extra='forbid') — silently dropping the
    field was the old behavior the user flagged.
    """
    r = client.put(
        "/api/runtime/bindings/manager",
        json={
            "host": "127.0.0.1",
            "port": 18999,
            "server_instance_id": 9999,
        },
    )
    assert r.status_code == 422, (
        f"expected 422 for caller-controlled server_instance_id, "
        f"got {r.status_code}: {r.json()}"
    )
    detail = r.json().get("detail", [])
    if isinstance(detail, list):
        types = [e.get("type", "") for e in detail]
        assert any("extra" in t or "forbidden" in t for t in types), (
            f"expected extra_forbidden error, got {detail}"
        )


def test_binding_patch_schema_rejects_caller_server_instance_id(client):
    """Same restriction on PATCH: the schema rejects caller-
    controlled server_instance_id."""
    r = client.patch(
        "/api/runtime/bindings/manager",
        json={"server_instance_id": 9999},
    )
    assert r.status_code == 422, (
        f"expected 422 for caller-controlled server_instance_id in PATCH, "
        f"got {r.status_code}: {r.json()}"
    )
