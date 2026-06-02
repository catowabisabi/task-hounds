"""
Interface and fake DB contract for flow_01.

This file is intentionally copied per flow. Each flow can evolve its own input
shape, output shape, limits, and DB-write behavior without touching the real
Task Hounds database.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


FLOW_DIR = Path(__file__).resolve().parent
DB_PATH = FLOW_DIR / "temp-power-teams.db"
WORKFLOW_TEST_DIR = FLOW_DIR / "workflow-test"

RoleName = Literal["human", "manager", "worker", "reviewer"]
LoopStatus = Literal["pending", "running", "completed", "blocked", "failed"]
TodoStatus = Literal["pending", "in_progress", "completed", "blocked"]
TodoPriority = Literal["high", "medium", "low"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class FlowLimits:
    """Input limits for the fake flow contract."""

    directive_max_chars: int = 4000
    manager_message_max_chars: int = 6000
    user_input_max_chars: int = 6000
    todo_max_items: int = 50
    loop_max_iterations: int = 5


@dataclass
class FlowIdentity:
    """Project/session/runtime identity shared by every flow row."""

    power_team_project_id: str
    project_session_id: str
    workspace_id: str = "workflow-test"
    workspace_path: str = ""
    manager_opencode_session_id: str | None = None
    worker_opencode_session_id: str | None = None
    reviewer_opencode_session_id: str | None = None
    chat_opencode_session_id: str | None = None
    server_instance_id: int | None = None


@dataclass
class FlowInput:
    """Human/project input that starts a flow loop."""

    power_team_project_id: str
    project_session_id: str
    human_directive: str
    human_new_thought_and_suggestion: str = ""
    human_suggested_new_task_or_item: str = ""
    manager_message: str = ""
    todo_items: list[str] = field(default_factory=list)
    workspace_id: str = "workflow-test"
    workspace_path: str = ""
    manager_opencode_session_id: str | None = None
    worker_opencode_session_id: str | None = None
    reviewer_opencode_session_id: str | None = None
    chat_opencode_session_id: str | None = None
    server_instance_id: int | None = None

    def identity(self) -> FlowIdentity:
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
        )


@dataclass
class FlowLoopInput:
    """Per-loop runtime input from previous roles."""

    loop_index: int
    worker_report: str = ""
    files_changed: list[str] = field(default_factory=list)
    test_result: str = ""
    known_issues: list[str] = field(default_factory=list)
    reviewer_feedback: str = ""


@dataclass
class FlowState:
    """State object passed between workflow steps."""

    flow_input: FlowInput
    loop_input: FlowLoopInput
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


@dataclass
class UITodoItem:
    """Matches `/api/todos` / PlanningTodoRail Todo shape."""

    id: str
    session_id: str
    content: str
    status: TodoStatus = "pending"
    priority: TodoPriority = "medium"
    position: int = 0
    parent_id: str | None = None
    owner: str | None = "manager"
    updated_at: str | None = None


@dataclass
class UISuggestion:
    """Matches the frontend Suggestion interface."""

    id: int | None = None
    content: str | None = None
    status: str | None = "released"
    queue_status: str | None = None
    status_label: str | None = None
    verification: str | None = None
    related_files: list[str] | None = None
    created_at: str | None = None


@dataclass
class UIManagerMessage:
    """Matches the frontend ManagerMessage interface."""

    id: int
    content: str
    created_at: str
    is_human: bool | None = None
    queue_status: str | None = None
    status_label: str | None = None


@dataclass
class UIPlanData:
    """Matches `/api/plan` response shape used by the UI."""

    content: str = ""
    updated_at: str | None = None
    updated_by: str | None = None
    session_id: str | None = None


@dataclass
class FlowRoleOutput:
    """One role output row written to the fake DB."""

    role: RoleName
    loop_index: int
    content: str
    payload: dict = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)


@dataclass
class FlowOutput:
    """Final result for one loop iteration."""

    project_session_id: str
    power_team_project_id: str
    loop_index: int
    status: LoopStatus
    plan: UIPlanData
    todos: list[UITodoItem]
    suggestion: UISuggestion
    manager_message: UIManagerMessage
    manager: FlowRoleOutput
    worker: FlowRoleOutput
    reviewer: FlowRoleOutput
    todo_update_json: dict
    handoff_update: dict


class FlowValidationError(ValueError):
    """Raised when flow input exceeds the declared interface contract."""


class FlowStorage:
    """SQLite adapter for the fake flow DB."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with closing(self.connect()) as conn:
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS project_sessions (
                        id TEXT PRIMARY KEY,
                        power_team_project_id TEXT NOT NULL,
                        workspace_id TEXT,
                        name TEXT,
                        manager_session_id TEXT,
                        worker_session_id TEXT,
                        reviewer_session_id TEXT,
                        chat_session_id TEXT,
                        is_active INTEGER DEFAULT 1,
                        workspace_path TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS project_session_role_sessions (
                        project_session_id TEXT NOT NULL,
                        power_team_project_id TEXT NOT NULL,
                        role TEXT NOT NULL CHECK(role IN ('manager', 'worker', 'reviewer', 'chat')),
                        opencode_session_id TEXT,
                        server_instance_id INTEGER,
                        workspace_path TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (project_session_id, role)
                    );

                    CREATE TABLE IF NOT EXISTS workflow_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        power_team_project_id TEXT NOT NULL,
                        project_session_id TEXT NOT NULL,
                        loop_index INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        manager_opencode_session_id TEXT,
                        worker_opencode_session_id TEXT,
                        reviewer_opencode_session_id TEXT,
                        server_instance_id INTEGER,
                        input_json TEXT NOT NULL,
                        output_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS user_directives (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        power_team_project_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        directive TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS manager_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        power_team_project_id TEXT NOT NULL,
                        content TEXT NOT NULL,
                        session_id TEXT,
                        queue_status TEXT,
                        status_label TEXT,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS session_plan (
                        session_id TEXT PRIMARY KEY,
                        power_team_project_id TEXT NOT NULL,
                        content TEXT NOT NULL,
                        updated_by TEXT DEFAULT 'manager',
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS session_todos (
                        id TEXT PRIMARY KEY,
                        power_team_project_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        parent_id TEXT,
                        content TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        priority TEXT DEFAULT 'medium',
                        position INTEGER DEFAULT 0,
                        owner TEXT DEFAULT 'manager',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS suggestion_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        power_team_project_id TEXT NOT NULL,
                        content TEXT NOT NULL,
                        status TEXT DEFAULT 'pending',
                        human_comment TEXT,
                        verification TEXT,
                        related_files TEXT,
                        handoff_version INTEGER,
                        session_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        released_at TEXT,
                        done_at TEXT
                    );

                    CREATE TABLE IF NOT EXISTS worker_reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        power_team_project_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        worker_opencode_session_id TEXT,
                        report TEXT NOT NULL,
                        files_changed_json TEXT,
                        test_result TEXT,
                        known_issues_json TEXT,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS reviewer_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        power_team_project_id TEXT NOT NULL,
                        project_session_id TEXT NOT NULL,
                        reviewer_opencode_session_id TEXT,
                        suggestion_id INTEGER NOT NULL,
                        status TEXT DEFAULT 'pending',
                        screenshot_paths TEXT,
                        review_notes TEXT,
                        usability_issues TEXT,
                        style_feedback TEXT,
                        scripts_documented TEXT,
                        started_at TEXT NOT NULL,
                        completed_at TEXT,
                        timeout_at TEXT,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS project_handoff (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        version INTEGER NOT NULL DEFAULT 1,
                        power_team_project_id TEXT NOT NULL,
                        human_requirements TEXT,
                        working_direction TEXT,
                        current_task TEXT,
                        current_micro_flow TEXT,
                        human_concerns TEXT,
                        tested_files TEXT,
                        known_bugs TEXT,
                        completion_criteria TEXT,
                        session_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        updated_by TEXT DEFAULT 'manager'
                    );
                    """
                )

    def reset_db(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        self.init_db()

    def write_output(self, flow_input: FlowInput, output: FlowOutput) -> None:
        self.init_db()
        now = utc_now()
        identity = flow_input.identity()
        workspace_path = identity.workspace_path or str(WORKFLOW_TEST_DIR)
        with closing(self.connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO project_sessions
                      (id, power_team_project_id, workspace_id, name,
                       manager_session_id, worker_session_id, reviewer_session_id, chat_session_id,
                       is_active, workspace_path, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        identity.project_session_id,
                        identity.power_team_project_id,
                        identity.workspace_id,
                        f"Flow 01 {identity.project_session_id}",
                        identity.manager_opencode_session_id,
                        identity.worker_opencode_session_id,
                        identity.reviewer_opencode_session_id,
                        identity.chat_opencode_session_id,
                        workspace_path,
                        now,
                        now,
                    ),
                )
                for role, opencode_session_id in (
                    ("manager", identity.manager_opencode_session_id),
                    ("worker", identity.worker_opencode_session_id),
                    ("reviewer", identity.reviewer_opencode_session_id),
                    ("chat", identity.chat_opencode_session_id),
                ):
                    conn.execute(
                        """
                        INSERT INTO project_session_role_sessions
                          (project_session_id, power_team_project_id, role, opencode_session_id,
                           server_instance_id, workspace_path, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(project_session_id, role) DO UPDATE SET
                          power_team_project_id=excluded.power_team_project_id,
                          opencode_session_id=excluded.opencode_session_id,
                          server_instance_id=excluded.server_instance_id,
                          workspace_path=excluded.workspace_path,
                          updated_at=excluded.updated_at
                        """,
                        (
                            identity.project_session_id,
                            identity.power_team_project_id,
                            role,
                            opencode_session_id,
                            identity.server_instance_id,
                            workspace_path,
                            now,
                            now,
                        ),
                    )
                conn.execute(
                    """
                    INSERT INTO workflow_runs
                      (power_team_project_id, project_session_id, loop_index, status,
                       manager_opencode_session_id, worker_opencode_session_id,
                       reviewer_opencode_session_id, server_instance_id,
                       input_json, output_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identity.power_team_project_id,
                        identity.project_session_id,
                        output.loop_index,
                        output.status,
                        identity.manager_opencode_session_id,
                        identity.worker_opencode_session_id,
                        identity.reviewer_opencode_session_id,
                        identity.server_instance_id,
                        json.dumps(asdict(flow_input), ensure_ascii=False),
                        json.dumps(asdict(output), ensure_ascii=False),
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO user_directives
                      (power_team_project_id, session_id, directive, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'processed', ?, ?)
                    """,
                    (
                        identity.power_team_project_id,
                        identity.project_session_id,
                        flow_input.human_directive,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO manager_messages
                      (power_team_project_id, content, session_id, queue_status, status_label, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identity.power_team_project_id,
                        output.manager_message.content,
                        identity.project_session_id,
                        output.manager_message.queue_status,
                        output.manager_message.status_label,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO session_plan (session_id, power_team_project_id, content, updated_by, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                      power_team_project_id=excluded.power_team_project_id,
                      content=excluded.content,
                      updated_by=excluded.updated_by,
                      updated_at=excluded.updated_at
                    """,
                    (
                        identity.project_session_id,
                        identity.power_team_project_id,
                        output.plan.content,
                        output.plan.updated_by or "manager",
                        now,
                    ),
                )
                for todo in output.todos:
                    conn.execute(
                        """
                        INSERT INTO session_todos
                          (id, power_team_project_id, session_id, parent_id, content, status, priority, position, owner, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                          power_team_project_id=excluded.power_team_project_id,
                          session_id=excluded.session_id,
                          content=excluded.content,
                          status=excluded.status,
                          priority=excluded.priority,
                          position=excluded.position,
                          owner=excluded.owner,
                          updated_at=excluded.updated_at
                        """,
                        (
                            todo.id,
                            identity.power_team_project_id,
                            todo.session_id,
                            todo.parent_id,
                            todo.content,
                            todo.status,
                            todo.priority,
                            todo.position,
                            todo.owner,
                            now,
                            now,
                        ),
                    )
                suggestion_cursor = conn.execute(
                    """
                    INSERT INTO suggestion_queue
                      (power_team_project_id, content, status, verification, related_files, session_id, created_at, updated_at, released_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identity.power_team_project_id,
                        output.suggestion.content or "",
                        output.suggestion.status or "released",
                        output.suggestion.verification,
                        json.dumps(output.suggestion.related_files or [], ensure_ascii=False),
                        identity.project_session_id,
                        now,
                        now,
                        now,
                    ),
                )
                suggestion_id = int(suggestion_cursor.lastrowid)
                conn.execute(
                    """
                    INSERT INTO worker_reports
                      (power_team_project_id, session_id, worker_opencode_session_id, report,
                       files_changed_json, test_result, known_issues_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identity.power_team_project_id,
                        identity.project_session_id,
                        identity.worker_opencode_session_id,
                        output.worker.content,
                        json.dumps(output.worker.payload.get("files_changed", []), ensure_ascii=False),
                        output.worker.payload.get("test_result", ""),
                        json.dumps(output.worker.payload.get("known_issues", []), ensure_ascii=False),
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO reviewer_sessions
                      (power_team_project_id, project_session_id, reviewer_opencode_session_id,
                       suggestion_id, status, review_notes, usability_issues, style_feedback,
                       scripts_documented, started_at, completed_at, created_at)
                    VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identity.power_team_project_id,
                        identity.project_session_id,
                        identity.reviewer_opencode_session_id,
                        suggestion_id,
                        output.reviewer.content,
                        json.dumps(output.reviewer.payload.get("bugs", []), ensure_ascii=False),
                        json.dumps(output.reviewer.payload.get("uiux_suggestions", []), ensure_ascii=False),
                        output.worker.payload.get("test_result", ""),
                        now,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO project_handoff
                      (version, power_team_project_id, human_requirements, working_direction, current_task,
                       current_micro_flow, known_bugs, completion_criteria, session_id,
                       created_at, updated_at, updated_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manager')
                    """,
                    (
                        output.loop_index,
                        identity.power_team_project_id,
                        flow_input.human_directive,
                        flow_input.manager_message,
                        output.handoff_update.get("current_task", ""),
                        json.dumps(output.handoff_update.get("current_micro_flow", []), ensure_ascii=False),
                        json.dumps(output.reviewer.payload.get("bugs", []), ensure_ascii=False),
                        json.dumps(output.handoff_update.get("completion_criteria", []), ensure_ascii=False),
                        identity.project_session_id,
                        now,
                        now,
                    ),
                )

    def count(self, table: str) -> int:
        allowed = {
            "project_sessions",
            "project_session_role_sessions",
            "workflow_runs",
            "user_directives",
            "manager_messages",
            "session_plan",
            "session_todos",
            "suggestion_queue",
            "worker_reports",
            "reviewer_sessions",
            "project_handoff",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        self.init_db()
        with closing(self.connect()) as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def validate_flow_input(flow_input: FlowInput, limits: FlowLimits | None = None) -> None:
    limits = limits or FlowLimits()
    if not flow_input.project_session_id.strip():
        raise FlowValidationError("project_session_id is required")
    if not flow_input.power_team_project_id.strip():
        raise FlowValidationError("power_team_project_id is required")
    if not flow_input.human_directive.strip():
        raise FlowValidationError("human_directive is required")
    if len(flow_input.human_directive) > limits.directive_max_chars:
        raise FlowValidationError("human_directive exceeds directive_max_chars")
    if len(flow_input.manager_message) > limits.manager_message_max_chars:
        raise FlowValidationError("manager_message exceeds manager_message_max_chars")
    if len(flow_input.human_new_thought_and_suggestion) > limits.user_input_max_chars:
        raise FlowValidationError("human_new_thought_and_suggestion exceeds user_input_max_chars")
    if len(flow_input.human_suggested_new_task_or_item) > limits.user_input_max_chars:
        raise FlowValidationError("human_suggested_new_task_or_item exceeds user_input_max_chars")
    if len(flow_input.todo_items) > limits.todo_max_items:
        raise FlowValidationError("todo_items exceeds todo_max_items")
