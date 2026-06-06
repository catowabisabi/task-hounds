"""Tests for binding_resolver — the fallback chain that picks (host, port,
agent, model) for each role.

Priority order:
  1. agent_runtime_bindings row for the role (DB) — wins
  2. env var TASK_HOUNDS_OPENCODE_PORT for port; role-specific *_AGENT /
     *_MODEL env vars for agent and model
  3. default 127.0.0.1:18765 / "Sisyphus - ultraworker" / "minimax-coding-plan/MiniMax-M2.7"

Regression for the "worker hardcoded 127.0.0.1:18765" bug — the binding
table existed for UI display only; the Worker/Reviewer executors did
not actually read it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    """Fresh SQLite DB for this test."""
    db = tmp_path / "resolver_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.db import init_db
    init_db()
    return db


def test_binding_wins_over_env(fresh_db, monkeypatch):
    """When a DB binding exists for the role, it overrides the env var."""
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_PORT", "18999")
    from task_hounds_api.db.ops import runtime as db_rt
    db_rt.upsert_binding("worker", "10.0.0.5", 19999)

    from task_hounds_api.opencode.binding_resolver import resolve_for_role
    host, port, agent, model = resolve_for_role("worker")
    assert host == "10.0.0.5"
    assert port == 19999


def test_env_wins_when_no_binding(fresh_db, monkeypatch):
    """When no DB binding exists, the env var TASK_HOUNDS_OPENCODE_PORT is used."""
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_PORT", "18888")
    monkeypatch.delenv("TASK_HOUNDS_WORKER_OPENCODE_MODEL", raising=False)

    from task_hounds_api.opencode.binding_resolver import resolve_for_role
    host, port, agent, model = resolve_for_role("worker")
    assert host == "127.0.0.1"
    assert port == 18888


def test_default_when_no_binding_no_env(fresh_db, monkeypatch):
    """When neither binding nor env var is present, default 127.0.0.1:18765."""
    monkeypatch.delenv("TASK_HOUNDS_OPENCODE_PORT", raising=False)
    monkeypatch.delenv("TASK_HOUNDS_OPENCODE_MODEL", raising=False)

    from task_hounds_api.opencode.binding_resolver import resolve_for_role
    host, port, agent, model = resolve_for_role("reviewer")
    assert host == "127.0.0.1"
    assert port == 18765


def test_role_specific_model_env_overrides_global(fresh_db, monkeypatch):
    """TASK_HOUNDS_WORKER_OPENCODE_MODEL overrides TASK_HOUNDS_OPENCODE_MODEL."""
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_MODEL", "global-model")
    monkeypatch.setenv("TASK_HOUNDS_WORKER_OPENCODE_MODEL", "worker-specific-model")
    monkeypatch.delenv("TASK_HOUNDS_REVIEWER_OPENCODE_MODEL", raising=False)

    from task_hounds_api.opencode.binding_resolver import resolve_for_role
    _, _, _, worker_model = resolve_for_role("worker")
    _, _, _, reviewer_model = resolve_for_role("reviewer")
    assert worker_model == "worker-specific-model"
    assert reviewer_model == "global-model"


def test_executor_worker_uses_binding_not_hardcoded(fresh_db, monkeypatch, valid_credentials):
    """Regression: worker_execute() must NOT default to 127.0.0.1:18765.
    It should resolve via binding_resolver so a per-role binding takes effect."""
    from task_hounds_api.db.ops import runtime as db_rt
    db_rt.upsert_binding("worker", "192.168.1.50", 19500)
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_PORT", "18765")

    from task_hounds_api.workflow import executor as exec_mod
    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "output": {"text": ""}, "error": {}}

    monkeypatch.setattr(exec_mod.oc_client, "run", fake_run)
    monkeypatch.setattr(exec_mod, "_opencode_model", lambda role: "fake-model")

    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"stdout": "", "returncode": 0})(),
    )

    from task_hounds_api.db.ops import workflow as db_wf
    db_wf.create_suggestion(
        "ps_test", "test task", verification="ok", status="released"
    )

    state = exec_mod.M.FlowState(
        flow_input=exec_mod.M.FlowInput(
            power_team_project_id="pt_test",
            project_session_id="ps_test",
            human_directive="do thing",
            workspace_path=str(fresh_db.parent),
        ),
        loop_input=exec_mod.M.FlowLoopInput(loop_index=1),
    )
    exec_mod.worker_execute(state)

    assert captured.get("host") == "192.168.1.50"
    assert captured.get("port") == 19500
