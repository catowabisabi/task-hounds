"""workflow.models — dataclasses for the Manager/Worker/Reviewer workflow.

The DB is the whiteboard. These dataclasses are passed between
graph nodes and (de)serialized to/from DB rows.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

RoleName = Literal["manager", "worker", "reviewer", "chat"]
LoopStatus = Literal["pending", "running", "completed", "blocked", "failed"]
TodoStatus = Literal["pending", "in_progress", "completed", "blocked"]
TodoPriority = Literal["high", "medium", "low"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Input ────────────────────────────────────────────────────────────────────

@dataclass
class FlowInput:
    """What a human or chat agent sends to start a loop."""
    power_team_project_id: str
    project_session_id: str
    human_directive: str
    human_new_thought_and_suggestion: str = ""
    human_suggested_new_task_or_item: str = ""
    manager_message: str = ""
    todo_items: list[str] = field(default_factory=list)
    workspace_id: str = "default"
    workspace_path: str = ""
    manager_opencode_session_id: str | None = None
    worker_opencode_session_id: str | None = None
    reviewer_opencode_session_id: str | None = None
    chat_opencode_session_id: str | None = None
    server_instance_id: int | None = None


@dataclass
class FlowLoopInput:
    """Per-loop runtime input from previous roles."""
    loop_index: int = 0
    worker_report: str = ""
    files_changed: list[str] = field(default_factory=list)
    test_result: str = ""
    known_issues: list[str] = field(default_factory=list)
    reviewer_feedback: str = ""


# ── State (in-memory) ────────────────────────────────────────────────────────

@dataclass
class FlowState:
    """State object passed between graph nodes.

    Every step:
      1. Reads DB to build this state
      2. Runs the step function (which mutates state)
      3. Writes relevant fields back to DB
    """
    flow_input: FlowInput
    loop_input: FlowLoopInput = field(default_factory=FlowLoopInput)
    status: LoopStatus = "pending"
    input_digest: str = ""
    decision: dict = field(default_factory=dict)
    manager_message: str = ""
    plan: str = ""
    todo_list: list[dict] = field(default_factory=list)
    todo_update_json: dict = field(default_factory=dict)
    suggestion_content: str = ""
    suggestion_verification: str = ""
    handoff_update: dict = field(default_factory=dict)
    existing_context: dict = field(default_factory=dict)

    # Worker output
    worker_report: str = ""
    worker_files_changed: list[str] = field(default_factory=list)
    worker_test_result: str = ""
    worker_known_issues: list[str] = field(default_factory=list)

    # Reviewer output
    reviewer_feedback: str = ""
    reviewer_qa_result: str = "needs_review"
    reviewer_bugs: list[str] = field(default_factory=list)
    reviewer_uiux: list[str] = field(default_factory=list)
    reviewer_risks: list[str] = field(default_factory=list)

    # The Worker records the suggestion_id it executed on so the
    # Reviewer can review the SAME row even if the Manager has
    # released new work in the meantime.
    suggestion_id: int | None = None

    __digest_retry__: int = 0


# ── Output (per role) ───────────────────────────────────────────────────────

@dataclass
class FlowRoleOutput:
    role: RoleName
    loop_index: int
    content: str
    payload: dict = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)


@dataclass
class FlowOutput:
    project_session_id: str
    power_team_project_id: str
    loop_index: int
    status: LoopStatus
    plan: str
    todo_list: list[dict]
    suggestion_content: str
    manager_message: str
    manager: FlowRoleOutput
    worker: FlowRoleOutput
    reviewer: FlowRoleOutput
    todo_update_json: dict
    handoff_update: dict


# ── Validation ──────────────────────────────────────────────────────────────

class FlowValidationError(ValueError):
    pass


def validate_flow_input(fi: FlowInput) -> None:
    if not fi.project_session_id.strip():
        raise FlowValidationError("project_session_id is required")
    if not fi.power_team_project_id.strip():
        raise FlowValidationError("power_team_project_id is required")
    if not fi.human_directive.strip():
        raise FlowValidationError("human_directive is required")
    if len(fi.human_directive) > 8000:
        raise FlowValidationError("human_directive too long (max 8000 chars)")
    if len(fi.todo_items) > 100:
        raise FlowValidationError("too many todo items (max 100)")


# ── DB <-> State ────────────────────────────────────────────────────────────

def state_to_dict(state: FlowState) -> dict:
    return asdict(state)


def state_from_dict(raw: dict) -> FlowState:
    fi = FlowInput(**raw["flow_input"]) if isinstance(raw.get("flow_input"), dict) else raw["flow_input"]
    li_raw = raw.get("loop_input") or {}
    li = FlowLoopInput(**li_raw) if isinstance(li_raw, dict) else li_raw
    return FlowState(flow_input=fi, loop_input=li, **{
        k: raw.get(k, getattr(FlowState(flow_input=fi, loop_input=li), k))
        for k in (
            "status", "input_digest", "decision", "manager_message",
            "plan", "todo_list", "todo_update_json", "suggestion_content",
            "suggestion_verification", "handoff_update", "existing_context",
            "worker_report", "worker_files_changed", "worker_test_result",
            "worker_known_issues", "reviewer_feedback", "reviewer_qa_result",
            "reviewer_bugs", "reviewer_uiux", "reviewer_risks",
            "suggestion_id",
            "__digest_retry__",
        )
    })
