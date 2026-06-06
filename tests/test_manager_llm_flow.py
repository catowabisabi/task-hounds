"""Regression tests for the Manager LLM handoff.

The rebuilt workflow accidentally kept Worker/Reviewer OpenCode calls but
turned Manager into a deterministic scaffold. That also meant no suggestion
was released, so Worker often had no active task to execute.
"""
from __future__ import annotations

import gc
import json
import os
import sys
import tempfile
import time as time_mod
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def temp_db(monkeypatch):
    fd, db_path = tempfile.mkstemp(prefix="task_hounds_manager_flow_", suffix=".db")
    os.close(fd)
    monkeypatch.setenv("POWER_TEAMS_DB", db_path)

    for name in list(sys.modules):
        if name == "task_hounds_api" or name.startswith("task_hounds_api."):
            sys.modules.pop(name, None)

    yield Path(db_path)

    for name in list(sys.modules):
        if name == "task_hounds_api" or name.startswith("task_hounds_api."):
            sys.modules.pop(name, None)
    gc.collect()

    for suffix in ("", "-wal", "-shm"):
        target = Path(db_path + suffix)
        for _ in range(5):
            try:
                target.unlink()
                break
            except FileNotFoundError:
                break
            except PermissionError:
                time_mod.sleep(0.1)


def _manager_json() -> str:
    payload = {
        "input_digest": "User wants the dashboard working again.",
        "decision": {"next": "restore dashboard render path"},
        "manager_message": "I will first restore the dashboard render path.",
        "plan": "1. Inspect the dashboard route\n2. Patch the render bug\n3. Verify the UI loads",
        "todo_list": [
            {
                "content": "Restore the dashboard render path",
                "status": "pending",
                "priority": "high",
                "owner": "worker",
            }
        ],
        "suggestion_content": "Restore the dashboard render path",
        "suggestion_verification": "Dashboard route opens without a blank screen.",
        "handoff_update": {
            "current_task": "Restore the dashboard render path",
            "working_direction": "Fix the highest-impact visible failure first.",
            "completion_criteria": ["Dashboard route opens without a blank screen."],
        },
    }
    return "```json\n" + json.dumps(payload) + "\n```"


def test_full_graph_calls_manager_worker_reviewer_and_persists_handoff(temp_db, monkeypatch, tmp_path, valid_credentials):
    from task_hounds_api.db import connect, init_db
    from task_hounds_api.db.ops import project as db_project
    from task_hounds_api.workflow import graph, models as M

    init_db()
    session_id = "ps_manager_llm"
    db_project.create_session(session_id, str(tmp_path), "Manager LLM test")
    monkeypatch.setenv("TASK_HOUNDS_MANAGER_OPENCODE_AGENT", "manager")
    monkeypatch.setenv("TASK_HOUNDS_WORKER_OPENCODE_AGENT", "worker")
    monkeypatch.setenv("TASK_HOUNDS_REVIEWER_OPENCODE_AGENT", "reviewer")

    calls: list[str] = []

    def fake_run(**kwargs):
        calls.append(kwargs["agent"])
        if kwargs["agent"] == "manager":
            return {"ok": True, "output": {"text": _manager_json()}}
        if kwargs["agent"] == "worker":
            return {"ok": True, "output": {"text": "Worker executed the dashboard task."}}
        if kwargs["agent"] == "reviewer":
            return {
                "ok": True,
                "output": {
                    "text": (
                        "```json\n"
                        + json.dumps({
                            "reviewer_feedback": "The worker report is acceptable.",
                            "qa_result": "pass",
                            "bugs": [],
                            "uiux_suggestions": [],
                        })
                        + "\n```"
                    )
                },
            }
        raise AssertionError(f"unexpected agent: {kwargs['agent']}")

    monkeypatch.setattr("task_hounds_api.workflow.executor.oc_client.run", fake_run)

    result = graph.run_loop(
        M.FlowInput(
            power_team_project_id="pt_test",
            project_session_id=session_id,
            human_directive="Fix the app dashboard",
            workspace_path=str(tmp_path),
            manager_opencode_session_id="mgr-session",
            worker_opencode_session_id="wrk-session",
            reviewer_opencode_session_id="rev-session",
        )
    )

    assert calls == ["manager", "worker", "reviewer"]
    assert result["status"] == "completed"
    assert result["suggestion_content"] == "Restore the dashboard render path"
    assert result["worker_report"] == "Worker executed the dashboard task."
    assert result["reviewer_qa_result"] == "pass"

    with connect() as db:
        plan = db.execute(
            "SELECT content FROM session_plan WHERE session_id=?", (session_id,)
        ).fetchone()
        todo = db.execute(
            "SELECT content, priority FROM session_todos WHERE session_id=?", (session_id,)
        ).fetchone()
        suggestion = db.execute(
            "SELECT content, status, verification FROM suggestion_queue WHERE session_id=?",
            (session_id,),
        ).fetchone()
        worker_report = db.execute(
            "SELECT report FROM worker_reports WHERE session_id=?", (session_id,)
        ).fetchone()

    assert "Patch the render bug" in plan["content"]
    assert todo["content"] == "Restore the dashboard render path"
    assert todo["priority"] == "high"
    assert suggestion["content"] == "Restore the dashboard render path"
    assert suggestion["status"] == "done"
    assert suggestion["verification"] == "Dashboard route opens without a blank screen."
    assert worker_report["report"] == "Worker executed the dashboard task."


def test_run_once_processes_pending_directive_with_manager_llm(temp_db, monkeypatch, tmp_path, valid_credentials):
    from task_hounds_api.db import connect, init_db
    from task_hounds_api.db.ops import agent as db_agent
    from task_hounds_api.db.ops import chat as db_chat
    from task_hounds_api.db.ops import project as db_project
    from task_hounds_api.workflow import loop as loop_mod

    init_db()
    db_agent.seed_default_agents()
    session_id = "ps_directive_manager_llm"
    db_project.create_session(session_id, str(tmp_path), "Directive Manager LLM test")
    directive_id = db_chat.create_directive(session_id, "Fix the app dashboard")
    monkeypatch.setenv("TASK_HOUNDS_MANAGER_OPENCODE_AGENT", "manager")
    monkeypatch.setenv("TASK_HOUNDS_WORKER_OPENCODE_AGENT", "worker")
    monkeypatch.setenv("TASK_HOUNDS_REVIEWER_OPENCODE_AGENT", "reviewer")

    monkeypatch.setattr(
        loop_mod.oc_lifecycle.OpenCodeLifecycle,
        "ensure_running",
        lambda self: True,
    )

    calls: list[str] = []

    def fake_run(**kwargs):
        calls.append(kwargs["agent"])
        if kwargs["agent"] == "manager":
            return {"ok": True, "output": {"text": _manager_json()}}
        if kwargs["agent"] == "worker":
            return {"ok": True, "output": {"text": "Worker executed the dashboard task."}}
        if kwargs["agent"] == "reviewer":
            return {
                "ok": True,
                "output": {
                    "text": (
                        "```json\n"
                        + json.dumps({
                            "reviewer_feedback": "The worker report is acceptable.",
                            "qa_result": "pass",
                        })
                        + "\n```"
                    )
                },
            }
        raise AssertionError(f"unexpected agent: {kwargs['agent']}")

    monkeypatch.setattr("task_hounds_api.workflow.executor.oc_client.run", fake_run)

    result = loop_mod.run_once()

    assert calls == ["manager", "worker", "reviewer"]
    assert result is not None
    assert result["status"] == "completed"

    with connect() as db:
        directive = db.execute(
            "SELECT status, error FROM user_directives WHERE id=?", (directive_id,)
        ).fetchone()

    assert directive["status"] == "processed"
    assert directive["error"] is None
    for role in ("manager", "worker", "reviewer"):
        agent = db_agent.get_agent(role)
        assert agent["state"] == "idle"
        assert agent["current_step"] is None
        assert agent["current_step_started_at"] is None


def test_default_runtime_uses_sisyphus_with_minimax_model(monkeypatch):
    from task_hounds_api.workflow import executor as ex

    for key in (
        "TASK_HOUNDS_MANAGER_OPENCODE_AGENT",
        "TASK_HOUNDS_WORKER_OPENCODE_AGENT",
        "TASK_HOUNDS_REVIEWER_OPENCODE_AGENT",
        "TASK_HOUNDS_OPENCODE_MODEL",
        "TASK_HOUNDS_MANAGER_OPENCODE_MODEL",
        "TASK_HOUNDS_WORKER_OPENCODE_MODEL",
        "TASK_HOUNDS_REVIEWER_OPENCODE_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    assert ex._manager_agent_name() == "Sisyphus - ultraworker"
    assert ex._worker_agent_name() == "Sisyphus - ultraworker"
    assert ex._reviewer_agent_name() == "Sisyphus - ultraworker"
    assert ex._opencode_model("manager") == "minimax-coding-plan/MiniMax-M2.7"
    assert ex._opencode_model("worker") == "minimax-coding-plan/MiniMax-M2.7"
    assert ex._opencode_model("reviewer") == "minimax-coding-plan/MiniMax-M2.7"


def test_startup_clears_stale_busy_agent_when_no_directive_running(temp_db):
    from task_hounds_api.db import connect, init_db
    from task_hounds_api.db.ops import agent as db_agent

    init_db()
    db_agent.seed_default_agents()
    db_agent.update_agent(
        "manager",
        state="busy",
        current_step="digest",
        current_step_started_at="2026-01-01T00:00:00+00:00",
    )

    from task_hounds_api.api.main import create_app

    create_app()

    with connect() as db:
        row = db.execute(
            "SELECT state, current_step, current_step_started_at FROM agent_registry WHERE name='manager'"
        ).fetchone()

    assert row["state"] == "idle"
    assert row["current_step"] is None
    assert row["current_step_started_at"] is None
