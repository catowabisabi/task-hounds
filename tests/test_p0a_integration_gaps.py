"""Tests for the remaining P0-A integration gaps.

Issue 1: Credential validation. Without OPENCODE_API_KEY_MINIMAX and
  OPENCODE_API_KEY_BAILIAN set, the runtime config expansion leaves
  empty apiKey values, and the opencode CLI fails with exit code 1
  when it tries to call the LLM. Surface this clearly via
  RuntimeManager and the /api/runtime/status endpoint.

Issue 3: External server registration. register_external must mark
  rows with owner='external' / managed=0, and auto_bind must bind
  roles to the external server's id (not null) when no managed
  server exists.
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
    db = tmp_path / "int_gaps_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_PORT", "18980")
    from task_hounds_api.opencode import runtime_manager as rm_mod
    rm_mod.RuntimeManager.reset_instance()
    from task_hounds_api.opencode import config as oc_config
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    return db


@pytest.fixture()
def real_lifecycle_mock(monkeypatch):
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.workflow import executor as exec_mod
    from task_hounds_api.workflow import chat_agent as chat_mod

    class _FakeProc:
        def __init__(self, pid):
            self.pid = pid
        def poll(self): return None
        def kill(self): pass

    proc = _FakeProc(pid=66600)
    lc = MagicMock()
    lc.return_value.ensure_running.return_value = True
    lc.return_value.is_running.return_value = True
    lc.return_value._proc = proc
    lc.return_value.health.return_value = {
        "ok": True, "host": "127.0.0.1", "port": 18980, "pid": 66600,
    }
    lc.return_value.stop.return_value = None

    monkeypatch.setattr(rm_mod, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(loop_mod.oc_lifecycle, "OpenCodeLifecycle", lc)
    monkeypatch.setattr(exec_mod, "oc_client", MagicMock())
    monkeypatch.setattr(chat_mod, "oc_client", MagicMock())
    return {"lc": lc, "proc": proc}


# ── Issue 1: credential validation ────────────────────────────────────────


def test_validate_credentials_catches_missing_api_keys(fresh_db, monkeypatch):
    """When the env vars that expand the apiKey placeholders are unset,
    validate_credentials must return a list of issues (one per provider
    with empty apiKey) so the UI can show a clear 'runtime unavailable'
    message instead of letting the opencode subprocess crash with
    exit code 1."""
    monkeypatch.delenv("OPENCODE_API_KEY_MINIMAX", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY_BAILIAN", raising=False)

    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    rm = RuntimeManager.instance()
    issues = rm.validate_credentials()

    assert isinstance(issues, list)
    assert any("minimax" in i.lower() for i in issues), (
        f"expected issue mentioning minimax, got: {issues}"
    )
    assert any("bailian" in i.lower() for i in issues), (
        f"expected issue mentioning bailian, got: {issues}"
    )


def test_validate_credentials_empty_when_keys_present(fresh_db, monkeypatch):
    """When the env vars are set, validate_credentials returns no issues."""
    monkeypatch.setenv("OPENCODE_API_KEY_MINIMAX", "sk-test-1")
    monkeypatch.setenv("OPENCODE_API_KEY_BAILIAN", "sk-test-2")

    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    rm = RuntimeManager.instance()
    assert rm.validate_credentials() == []


def test_get_managed_health_includes_credential_warnings(fresh_db, monkeypatch):
    """get_managed_health must include credential_warnings so the UI
    can show the user why the runtime is unavailable."""
    monkeypatch.delenv("OPENCODE_API_KEY_MINIMAX", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY_BAILIAN", raising=False)

    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    rm = RuntimeManager.instance()
    health = rm.get_managed_health()
    assert "credential_warnings" in health
    assert isinstance(health["credential_warnings"], list)
    assert len(health["credential_warnings"]) >= 1


# ── Issue 3: external server registration ────────────────────────────────


def test_register_external_writes_owner_and_managed(real_lifecycle_mock, fresh_db):
    """register_external must set owner='external' and managed=0 on the
    inserted row, not the default 'power_teams' / 1."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    from task_hounds_api.db.ops import runtime as db_rt

    rm = RuntimeManager.instance()
    new_id = rm.register_external("10.0.0.99", 18900)

    servers = db_rt.list_servers()
    target = next((s for s in servers if s["id"] == new_id), None)
    assert target is not None
    assert target.get("owner") == "external", (
        f"expected owner='external', got {target.get('owner')!r}"
    )
    assert target.get("managed") in (0, False), (
        f"expected managed=0, got {target.get('managed')!r}"
    )


def test_auto_bind_falls_back_to_external_when_no_managed(
    real_lifecycle_mock, fresh_db
):
    """When no managed server row exists, auto_bind must still write
    server_instance_id (pointing at the registered external server),
    not null. Otherwise the runtime status says 'runtime servers:
    empty' while bindings point at no one."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    from task_hounds_api.db.ops import runtime as db_rt

    rm = RuntimeManager.instance()
    external_id = rm.register_external("10.0.0.99", 18900)
    rm.auto_bind_four_roles()

    bindings = {b["role"]: b for b in db_rt.list_bindings()}
    for role in ("manager", "worker", "reviewer", "chat"):
        assert bindings[role].get("server_instance_id") == external_id, (
            f"{role} binding has server_instance_id="
            f"{bindings[role].get('server_instance_id')!r}, expected {external_id}"
        )
