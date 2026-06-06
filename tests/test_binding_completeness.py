"""Tests for binding completeness (Issues 2 + 3 from P0-A review).

Issue 2: binding_resolver only reads DB host/port; DB opencode_agent/model
  are ignored.
Issue 3: auto_bind_four_roles must write valid agent/model/server_instance_id
  and sync agent_registry.
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
    db = tmp_path / "binding_complete_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.db import init_db
    init_db()
    return db


# ── Issue 2: resolver reads opencode_agent + model from binding ─────────────


def test_resolver_reads_opencode_agent_from_binding(fresh_db, monkeypatch):
    """When a binding row has opencode_agent set, resolver returns that,
    not the env-var default."""
    from task_hounds_api.db.ops import runtime as db_rt
    db_rt.upsert_binding(
        "manager", "127.0.0.1", 18765,
        opencode_agent="Custom Agent From DB",
    )
    monkeypatch.setenv("TASK_HOUNDS_MANAGER_OPENCODE_AGENT", "env-agent")

    from task_hounds_api.opencode.binding_resolver import resolve_for_role
    _, _, agent, _ = resolve_for_role("manager")
    assert agent == "Custom Agent From DB"


def test_resolver_reads_model_from_binding(fresh_db, monkeypatch):
    """When a binding row has model set, resolver returns that,
    not the env-var default."""
    from task_hounds_api.db.ops import runtime as db_rt
    db_rt.upsert_binding(
        "worker", "127.0.0.1", 18765,
        model="custom/model-from-db",
    )
    monkeypatch.setenv("TASK_HOUNDS_WORKER_OPENCODE_MODEL", "env-model")

    from task_hounds_api.opencode.binding_resolver import resolve_for_role
    _, _, _, model = resolve_for_role("worker")
    assert model == "custom/model-from-db"


# ── Issue 3: auto_bind writes full binding + syncs agent_registry ───────────


def test_auto_bind_writes_full_binding(fresh_db, monkeypatch):
    """auto_bind_four_roles must populate opencode_agent, model, and
    server_instance_id, not just host/port."""
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    rm = RuntimeManager.instance()
    rm._managed_host = "127.0.0.1"
    rm._managed_port = 18955
    rm._managed_lifecycle = MagicMock()
    rm._managed_lifecycle._proc = MagicMock(pid=12345)
    monkeypatch.setattr(rm, "_sync_managed_server_row", lambda: None)

    rm.auto_bind_four_roles()

    from task_hounds_api.db.ops import runtime as db_rt
    bindings = {b["role"]: b for b in db_rt.list_bindings()}
    for role in ("manager", "worker", "reviewer", "chat"):
        b = bindings[role]
        assert b.get("host") == "127.0.0.1", f"{role} host not set: {b}"
        assert b.get("port") == 18955, f"{role} port not set: {b}"
        assert b.get("opencode_agent"), f"{role} opencode_agent not set: {b}"
        assert b.get("model"), f"{role} model not set: {b}"


def test_auto_bind_syncs_agent_registry_model(fresh_db, monkeypatch):
    """auto_bind must also update the corresponding agent_registry row's
    model field so the UI and dashboard show the same model the executor
    will actually use."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    from task_hounds_api.db.ops import agent as db_agent

    db_agent.seed_default_agents()

    rm = RuntimeManager.instance()
    rm._managed_host = "127.0.0.1"
    rm._managed_port = 18955
    rm._managed_lifecycle = MagicMock()
    rm._managed_lifecycle._proc = MagicMock(pid=12345)
    monkeypatch.setattr(rm, "_sync_managed_server_row", lambda: None)

    rm.auto_bind_four_roles()

    from task_hounds_api.db.ops import runtime as db_rt
    bindings = {b["role"]: b for b in db_rt.list_bindings()}
    for role, agent_name in [
        ("manager", "manager"),
        ("worker", "worker"),
        ("reviewer", "reviewer"),
        ("chat", "chat"),
    ]:
        a = db_agent.get_agent(agent_name)
        b = bindings[role]
        assert a is not None, f"agent_registry row for {role} missing"
        assert a.get("model") == b.get("model"), (
            f"agent_registry.model {a.get('model')} != binding.model {b.get('model')}"
        )
