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


def agent_state(role: str) -> str:
    """Read current agent state from agent_registry."""
    a = db_agent.get_agent(role)
    return a["state"] if a else "unknown"


def set_agent_state(role: str, state: str, current_step: str | None = None) -> None:
    """Write agent state to agent_registry. Visible to UI via API."""
    fields = {"state": state}
    if current_step:
        fields["current_step"] = current_step
        fields["current_step_started_at"] = datetime.now(timezone.utc).isoformat()
    elif state != "busy":
        fields["current_step"] = None
        fields["current_step_started_at"] = None
    db_agent.update_agent(role, **fields)


def clear_runtime_agent_states() -> None:
    """Reset runtime role state after a loop stops, fails, or completes."""
    for role in ("manager", "worker", "reviewer"):
        set_agent_state(role, "idle")
