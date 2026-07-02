"""workflow.models — dataclasses for the Manager/Worker/Reviewer workflow.

The DB is the whiteboard. These dataclasses are passed between
graph nodes and (de)serialized to/from DB rows.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

RoleName = Literal["manager", "worker", "reviewer", "chat"]
LoopStatus = Literal[
    "pending",
    "running",
    "paused",
    "completed",
    "completed_with_unresolved_evidence",
    "failed",
    "needs_review",
    "cancelled",
    "stopped",
    "technical_error",
]
TodoStatus = Literal["pending", "in_progress", "completed"]
TodoPriority = Literal["high", "medium", "low"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Input ────────────────────────────────────────────────────────────────────

@dataclass
class FlowLimits:
    """Typed cap contract for all size/length limits in the workflow.

    Migration audit symbol 96: the old codebase had a FlowLimits
    dataclass that was replaced with hard-coded validation constants.
    This restores FlowLimits as a typed cap contract so callers can
    read the limits without duplicating magic numbers.
    """
    directive_max_chars: int = 8000
    manager_message_max_chars: int = 4000
    user_input_max_chars: int = 2000
    todo_max_items: int = 100
    loop_max_iterations: int = 50
    worker_report_max_chars: int = 8000
    files_changed_max: int = 50
    known_issues_max: int = 20


# ── Default instance used by validate_flow_input ──────────────────────────────
FLOW_LIMITS = FlowLimits()


@dataclass
class FlowIdentity:
    """Lightweight identity view returned by FlowInput.identity().

    Migration audit symbol 97 + 99: the old codebase had a separate
    FlowIdentity class. The new architecture inlines the identity
    fields into FlowInput. This dataclass gives callers a stable
    identity object to read without changing FlowInput's schema.
    """
    power_team_project_id: str
    project_session_id: str
    workspace_id: str
    workspace_path: str
    manager_opencode_session_id: str | None
    worker_opencode_session_id: str | None
    reviewer_opencode_session_id: str | None
    chat_opencode_session_id: str | None
    server_instance_id: int | None
    run_id: int | None


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
    run_id: int | None = None

    def identity(self) -> FlowIdentity:
        """Return a FlowIdentity snapshot of the identity fields.

        Migration audit symbol 99: restores the old `flow_input.identity()`
        call style without breaking the inlined schema. Pure, safe.
        """
        return FlowIdentity(
            power_team_project_id=self.power_team_project_id,
            project_session_id=self.project_session_id,
            workspace_id=self.workspace_id,
            workspace_path=self.workspace_path,
            manager_opencode_session_id=self.manager_opencode_session_id,
            worker_opencode_session_id=self.worker_opencode_session_id,
            reviewer_opencode_session_id=self.reviewer_opencode_session_id,
            chat_opencode_session_id=self.chat_opencode_session_id,
            server_instance_id=self.server_instance_id,
            run_id=self.run_id,
        )


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
    current_todo_id: str = ""
    archive_updates: list[dict] = field(default_factory=list)
    reopen_todos: list[dict] = field(default_factory=list)
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
    reviewer_possible_problems: list[str] = field(default_factory=list)
    reviewer_safety_security_risks: list[str] = field(default_factory=list)

    # The Worker records the suggestion_id it executed on so the
    # Reviewer can review the SAME row even if the Manager has
    # released new work in the meantime.
    suggestion_id: int | None = None

    # Checkpoint cursor (tA2c). Bumped by each graph node before
    # save_checkpoint() is called, so a resume can rebuild FlowState
    # from the last persisted step.
    step_index: int = 0
    step_name: str = ""

    __digest_retry__: int = 0
    __done_signal__: bool = False


# Migration audit symbol 57: explicit TypedDict for the LangGraph state
# so static checkers can see the contract. Marked total=False so nodes
# can populate a subset (the actual FlowState is the source of truth;
# this is the LangGraph wire shape only).
class GraphState(TypedDict, total=False):
    flow_input: dict[str, Any]
    loop_input: dict[str, Any]
    status: str
    input_digest: str
    decision: dict[str, Any]
    manager_message: str
    plan: str
    todo_list: list[dict[str, Any]]
    todo_update_json: dict[str, Any]
    suggestion_content: str
    suggestion_verification: str
    handoff_update: dict[str, Any]
    existing_context: dict[str, Any]
    worker_report: str
    worker_files_changed: list[str]
    worker_test_result: str
    worker_known_issues: list[str]
    reviewer_feedback: str
    reviewer_qa_result: str
    reviewer_bugs: list[str]
    reviewer_uiux: list[str]
    reviewer_risks: list[str]
    reviewer_possible_problems: list[str]
    reviewer_safety_security_risks: list[str]
    suggestion_id: int | None
    step_index: int
    step_name: str
    __digest_retry__: int
    # tA2d: __resume_state__ seeds the resumed graph with the last
    # checkpoint payload; __route__ is the conditional edge target.
    __resume_state__: dict[str, Any]
    __route__: str
    __done_signal__: bool


# ── Output (per role) ───────────────────────────────────────────────────────

@dataclass
class FlowRoleOutput:
    role: RoleName
    loop_index: int
    content: str
    payload: dict = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)


# ── P8 compat executor result dataclasses (ids 2, 3, 4) ─────────────
# The 0c44ba2 flow_01/workflow.py defined three @dataclass
# types that the executors returned. The new architecture
# mutates a single FlowState instead (graph nodes write
# directly to state.input_digest / state.plan / etc.).
# These compat dataclasses preserve the OLD return-type
# shape so any caller (tests, legacy UI) that still type-
# hinted ManagerExecutionResult etc. gets a real class.


@dataclass
class ManagerExecutionResult:
    input_digest: str = ""
    decision: dict = field(default_factory=dict)
    plan: str = ""
    todo_items: list[str] = field(default_factory=list)
    suggestion_content: str = ""
    suggestion_verification: str = ""
    handoff_update: dict = field(default_factory=dict)
    manager_message: str = ""


@dataclass
class WorkerExecutionResult:
    report: str = ""
    files_changed: list[str] = field(default_factory=list)
    test_result: str = ""
    known_issues: list[str] = field(default_factory=list)


@dataclass
class ReviewerExecutionResult:
    feedback: str = ""
    qa_result: str = ""
    bugs: list[str] = field(default_factory=list)
    uiux_suggestions: list[str] = field(default_factory=list)
    possible_problems: list[str] = field(default_factory=list)
    safety_security_risks: list[str] = field(default_factory=list)


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

    @classmethod
    def from_state(cls, state: FlowState, *, manager_role: FlowRoleOutput | None = None,
                   worker_role: FlowRoleOutput | None = None,
                   reviewer_role: FlowRoleOutput | None = None) -> "FlowOutput":
        """Build a FlowOutput from a FlowState.

        Migration audit symbol 106 + 107: a small adapter that lets
        callers (tests, legacy UI) reconstruct the old FlowOutput
        shape from the new FlowState without re-architecting the graph.
        """
        return cls(
            project_session_id=state.flow_input.project_session_id,
            power_team_project_id=state.flow_input.power_team_project_id,
            loop_index=state.loop_input.loop_index,
            status=state.status,
            plan=state.plan,
            todo_list=state.todo_list,
            suggestion_content=state.suggestion_content,
            manager_message=state.manager_message,
            manager=manager_role or FlowRoleOutput(
                role="manager", loop_index=state.loop_input.loop_index, content=state.manager_message,
            ),
            worker=worker_role or FlowRoleOutput(
                role="worker", loop_index=state.loop_input.loop_index,
                content=state.worker_report,
                payload={
                    "files_changed": list(state.worker_files_changed),
                    "test_result": state.worker_test_result,
                    "known_issues": list(state.worker_known_issues),
                },
            ),
            reviewer=reviewer_role or FlowRoleOutput(
                role="reviewer", loop_index=state.loop_input.loop_index,
                content=state.reviewer_feedback,
                payload={
                    "qa_result": state.reviewer_qa_result,
                    "bugs": list(state.reviewer_bugs),
                    "uiux_suggestions": list(state.reviewer_uiux),
                    "risks": list(state.reviewer_risks),
                    "possible_problems": list(state.reviewer_possible_problems),
                    "safety_security_risks": list(state.reviewer_safety_security_risks),
                },
            ),
            todo_update_json=state.todo_update_json,
            handoff_update=state.handoff_update,
        )


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
    if len(fi.human_directive) > FLOW_LIMITS.directive_max_chars:
        raise FlowValidationError(f"human_directive too long (max {FLOW_LIMITS.directive_max_chars} chars)")
    if len(fi.todo_items) > FLOW_LIMITS.todo_max_items:
        raise FlowValidationError(f"too many todo items (max {FLOW_LIMITS.todo_max_items})")
    if fi.manager_message and len(fi.manager_message) > FLOW_LIMITS.manager_message_max_chars:
        raise FlowValidationError(f"manager_message too long (max {FLOW_LIMITS.manager_message_max_chars} chars)")
    if fi.human_new_thought_and_suggestion and len(fi.human_new_thought_and_suggestion) > FLOW_LIMITS.user_input_max_chars:
        raise FlowValidationError(f"human_new_thought_and_suggestion too long (max {FLOW_LIMITS.user_input_max_chars} chars)")
    if fi.human_suggested_new_task_or_item and len(fi.human_suggested_new_task_or_item) > FLOW_LIMITS.user_input_max_chars:
        raise FlowValidationError(f"human_suggested_new_task_or_item too long (max {FLOW_LIMITS.user_input_max_chars} chars)")


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
            "suggestion_verification", "current_todo_id", "archive_updates", "reopen_todos",
            "handoff_update", "existing_context",
            "worker_report", "worker_files_changed", "worker_test_result",
            "worker_known_issues", "reviewer_feedback", "reviewer_qa_result",
            "reviewer_bugs", "reviewer_uiux", "reviewer_risks",
            "reviewer_possible_problems", "reviewer_safety_security_risks",
            "suggestion_id",
            "step_index", "step_name",
            "__digest_retry__", "__done_signal__",
        )
    })
