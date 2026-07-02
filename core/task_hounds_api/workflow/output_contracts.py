"""Strict LLM output contracts for GraphFlow roles."""
from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TodoStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"


class Priority(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class QaResult(str, Enum):
    pass_ = "pass"
    fail = "fail"
    needs_review = "needs_review"


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class IssueType(str, Enum):
    bug = "bug"
    uiux = "uiux"
    consistency = "consistency"
    safety = "safety"
    security = "security"
    test = "test"
    other = "other"


class ManagerAction(str, Enum):
    execute = "execute"
    retry = "retry"
    split = "split"
    complete = "complete"
    request_human = "request_human"
    stop = "stop"


class ArchiveReason(str, Enum):
    replaced = "replaced"
    merged = "merged"
    split = "split"
    duplicate = "duplicate"
    already_implemented = "already_implemented"
    out_of_scope = "out_of_scope"
    wrong_project = "wrong_project"
    human_removed = "human_removed"
    other = "other"


class ArchiveDirective(StrictModel):
    todo_id: str = Field(min_length=1, max_length=200)
    reason: ArchiveReason
    note: str = Field(min_length=1, max_length=2000)
    replaced_by_todo_id: str | None = Field(default=None, max_length=200)


class ReopenDirective(StrictModel):
    todo_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[str] = Field(min_length=1, max_length=20)


class ManagerDecision(StrictModel):
    action: ManagerAction
    summary: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(default="", max_length=4000)
    evidence: list[str] = Field(default_factory=list, max_length=20)


class AgentIssue(StrictModel):
    type: IssueType
    severity: Severity
    description: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(default="", max_length=2000)


class ManagerTodo(StrictModel):
    id: str | None = Field(default=None, max_length=200)
    content: str = Field(min_length=1, max_length=2000)
    status: TodoStatus
    priority: Priority
    owner: str = Field(default="manager", min_length=1, max_length=100)


class ManagerHandoff(StrictModel):
    human_requirements: str = Field(default="", max_length=8000)
    current_task: str = Field(default="", max_length=2000)
    working_direction: str = Field(default="", max_length=4000)
    completion_criteria: list[str] = Field(default_factory=list, max_length=20)
    current_micro_flow: list[str] = Field(default_factory=list, max_length=20)
    human_concerns: str = Field(default="", max_length=4000)
    tested_files: list[str] = Field(default_factory=list, max_length=50)
    known_bugs: list[AgentIssue] = Field(default_factory=list, max_length=20)
    references_demos: str = Field(default="", max_length=4000)
    file_structure: str = Field(default="", max_length=8000)
    important_files: str = Field(default="", max_length=8000)
    available_scripts: str = Field(default="", max_length=4000)
    existing_solutions: str = Field(default="", max_length=8000)
    macro_flow: str = Field(default="", max_length=8000)
    project_folder_location: str = Field(default="", max_length=2000)


class ManagerOutput(StrictModel):
    input_digest: str = Field(min_length=1, max_length=4000)
    decision: ManagerDecision
    manager_message: str = Field(min_length=1, max_length=4000)
    plan: str = Field(min_length=1, max_length=8000)
    todo_list: list[ManagerTodo] = Field(max_length=100)
    suggestion_content: str = Field(max_length=2000)
    suggestion_verification: str = Field(max_length=4000)
    handoff_update: ManagerHandoff
    archive_updates: list[ArchiveDirective] = Field(default_factory=list, max_length=100)
    reopen_todos: list[ReopenDirective] = Field(default_factory=list, max_length=100)
    stop_signal: str | None = Field(default=None, pattern=r"^(TASK_HOUNDS_STOP_LOOP|DIRECTIVE_COMPLETE)$")


class WorkerOutput(StrictModel):
    files_changed: list[str] = Field(default_factory=list, max_length=50)
    test_result: str = Field(min_length=1, max_length=4000)
    test_command: str = Field(default="", max_length=2000)
    stdout: str = Field(default="", max_length=8000)
    stderr: str = Field(default="", max_length=8000)
    acceptance_check: str = Field(default="", max_length=4000)
    known_issues: list[AgentIssue] = Field(default_factory=list, max_length=20)


class ReviewerOutput(StrictModel):
    reviewer_feedback: str = Field(min_length=1, max_length=8000)
    qa_result: QaResult
    bugs: list[AgentIssue] = Field(default_factory=list, max_length=50)
    uiux_suggestions: list[AgentIssue] = Field(default_factory=list, max_length=50)
    possible_problems: list[AgentIssue] = Field(default_factory=list, max_length=50)
    safety_security_risks: list[AgentIssue] = Field(default_factory=list, max_length=50)


class ManagerChatAmendmentType(str, Enum):
    suggestion = "suggestion"
    todo_amendment = "todo-amendment"
    user_directive_amend = "user-directive-amend"
    handoff_amend = "handoff-amend"


class ManagerChatAmendment(StrictModel):
    type: ManagerChatAmendmentType
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    payload: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payload_for_type(self):
        if self.type == ManagerChatAmendmentType.user_directive_amend:
            if not str(self.payload.get("directive") or "").strip():
                raise ValueError("user-directive-amend payload requires a non-empty directive")
        elif self.type == ManagerChatAmendmentType.todo_amendment:
            if not isinstance(self.payload.get("todos"), list):
                raise ValueError("todo-amendment payload requires a todos list")
        elif self.type == ManagerChatAmendmentType.handoff_amend:
            if not self.payload:
                raise ValueError("handoff-amend payload cannot be empty")
        return self


class ManagerChatOutput(StrictModel):
    reply: str = Field(min_length=1, max_length=8000)
    amendments: list[ManagerChatAmendment] = Field(default_factory=list, max_length=30)


class ChatDirectiveOutput(StrictModel):
    reply: str = Field(min_length=1, max_length=4000)
    directive_proposal: str = Field(min_length=1, max_length=8000)


def issue_text(issue: dict[str, Any]) -> str:
    evidence = str(issue.get("evidence") or "").strip()
    base = f"[{issue['severity']}/{issue['type']}] {issue['description']}"
    return f"{base} Evidence: {evidence}" if evidence else base
