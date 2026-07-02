"""workflow.signals — DB-based signal emitter.

Replaces the old FastApiServiceSignalAdapter and stream files.
Every signal is now a DB write that the API can poll.

Tables used:
  manager_messages   — manager log + output
  session_todos      — todo updates
  worker_reports     — worker output
  reviewer_sessions  — reviewer output
  agent_registry     — agent state changes

The UI / API can read these directly. No more stream files.
"""
from __future__ import annotations

from datetime import datetime, timezone
from task_hounds_api.db import connect
from task_hounds_api.db.ops import workflow as db_wf
from task_hounds_api.db.ops import agent as db_agent
from task_hounds_api.db.ops import execution as db_execution


def agent_state(role: str) -> str:
    """Read current agent state from agent_registry."""
    a = db_agent.get_agent(role)
    return a["state"] if a else "unknown"


def set_agent_state(
    role: str,
    state: str,
    current_step: str | None = None,
    *,
    project_session_id: str | None = None,
    role_session_id: str | None = None,
    workflow_run_id: int | None = None,
) -> None:
    """Write agent state to agent_registry. Visible to UI via API.

    ``project_session_id`` and ``role_session_id`` scope the row to a
    specific project and a specific role-scoped OpenCode session. They
    are required when state="busy" so the UI can distinguish "manager
    is busy in project A" from "manager is busy in project B" without
    flapping both indicators. They are cleared when state returns to
    a non-busy value because the busy row is logically gone.
    """
    fields = {"state": state}
    if current_step:
        fields["current_step"] = current_step
        fields["current_step_started_at"] = datetime.now(timezone.utc).isoformat()
    elif state != "busy":
        fields["current_step"] = None
        fields["current_step_started_at"] = None

    if state == "busy":
        if project_session_id is not None:
            fields["project_session_id"] = project_session_id
        if role_session_id is not None:
            fields["role_session_id"] = role_session_id
    else:
        # Leaving busy: clear the scope so a stale row from a previous
        # project/session can't keep the UI locked. update_agent() does
        # an UPDATE … SET, so an explicit None is needed.
        fields["project_session_id"] = None
        fields["role_session_id"] = None

    db_agent.update_agent(role, **fields)
    if project_session_id and role in db_execution.ROLES:
        eid = db_execution.execution_id(project_session_id, workflow_run_id, role)
        db_execution.upsert_execution(
            execution_id=eid,
            project_session_id=project_session_id,
            workflow_run_id=workflow_run_id,
            role=role,
            agent_registry_name=role,
            opencode_session_id=role_session_id,
            status=state,
            current_step=current_step,
        )


def clear_runtime_agent_states() -> None:
    """Reset runtime role state after a loop stops, fails, or completes."""
    for role in ("manager", "worker", "reviewer"):
        set_agent_state(role, "idle")
