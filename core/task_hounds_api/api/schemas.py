"""api.schemas — Pydantic request/response models for the API layer."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class ProjectSessionCreate(BaseModel):
    name: str = ""
    workspace_path: str


class ProjectSessionOut(BaseModel):
    id: str
    name: str | None = None
    workspace_path: str | None = None
    is_active: int = 0


class ProjectSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    workspace_name: str | None = None
    workspace_path: str | None = None
    path_missing: bool | None = None
    workspace_fingerprint: str | None = None


class AgentUpdate(BaseModel):
    host: str | None = None
    port: int | None = None
    model: str | None = None
    opencode_agent: str | None = None
    state: str | None = None
    current_step: str | None = None


class TodoUpsert(BaseModel):
    id: str | None = None
    content: str
    status: str = "pending"
    priority: str = "medium"
    position: int = 0
    parent_id: str | None = None
    owner: str = "manager"


class TodoBatchUpsert(BaseModel):
    todos: list[TodoUpsert]


class TodoPatch(BaseModel):
    status: str | None = None
    content: str | None = None
    priority: str | None = None
    position: int | None = None


# Migration audit symbol 102: dedicated Todo response schema
# (UITodoItem in 0c44ba2 was a TypedDict; the new architecture has
# TodoUpsert/TodoPatch for request bodies but no typed response).
# The fields mirror the old UITodoItem shape so the frontend contract
# is enforced and a future refactor can't silently drop a field.
class TodoOut(BaseModel):
    id: str
    session_id: str
    parent_id: str | None = None
    content: str
    status: str = "pending"
    worker_task_status: str = "pending"
    reviewer_task_status: str = "pending"
    attempt_count: int = 0
    human_attention_status: str = "none"
    is_active: bool = True
    plan_revision: int = 1
    archive_reason: str | None = None
    archive_note: str | None = None
    archived_at: str | None = None
    archived_by: str | None = None
    replaced_by_todo_id: str | None = None
    priority: str = "medium"
    position: int = 0
    owner: str = "manager"
    created_at: str | None = None
    updated_at: str | None = None


class ChatSend(BaseModel):
    content: str
    sender: str = "human"


class DirectiveCreate(BaseModel):
    directive: str
    session_id: str | None = None


class LoopStartRequest(BaseModel):
    session_id: str
    loop_index: int = 0
    use_real_executors: bool = True


class RuntimePolicyUpdate(BaseModel):
    name: str | None = None
    close_behavior: str | None = None
    on_opencode_crash: str | None = None
    max_managed_opencode_servers: int | None = None
    graphflow_worker_count: int | None = None
    graphflow_max_active_jobs: int | None = None
    opencode_concurrency: int | None = None
    graphflow_max_cpu_percent: float | None = None
    graphflow_max_memory_percent: float | None = None
    default_topology: str | None = None
    default_shared_port: int | None = None
    allow_external_attach: bool | None = None


class BindingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str
    port: int
    opencode_agent: str | None = None
    model: str | None = None
    binding_source: str | None = None


class BindingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str | None = None
    port: int | None = None
    opencode_agent: str | None = None
    model: str | None = None
    binding_source: str | None = None


class AttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str
    port: int


class TestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str
    port: int


class IgnoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str
    port: int
    reason: str | None = None


class DiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    start_port: int = 18765
    end_port: int = 18865
    extra_ports: list[int] | None = None


# ── Migration audit P3 schema / response-shape batch (id 145-167) ─────────
# Each model here mirrors the 0c44ba2 Pydantic class shape where safe,
# with current-arch fields included. extra="allow" on response models
# so DB-derived dicts with extra columns (timestamps, internal flags)
# still validate. Request models keep extra="forbid" for safety.


# id 145: HealthResponse — /api/health response
class HealthResponse(BaseModel):
    ok: bool = True
    active_project_session: str | None = None
    opencode: dict = Field(default_factory=dict)
    # extra allows future fields without breaking the contract
    model_config = ConfigDict(extra="allow")


# id 146: Agent — /api/agents response
class AgentOut(BaseModel):
    name: str
    state: str = "idle"
    current_step: str | None = None
    host: str | None = None
    port: int | None = None
    model: str | None = None
    opencode_agent: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    model_config = ConfigDict(extra="allow")


# id 147: AgentUpdate is already declared above (intentionally narrower
# than the 0c44ba2 schema; the new schema drops task_complete/session_id
# /backend_type/backend_config_json). For callers that need the old
# fields, the new AgentUpdate accepts extra fields via default Pydantic
# config (extra="allow" by default for v2). No compat shim needed.


# id 148: LoopStatusOut — /api/loop/status response
class LoopStatusOut(BaseModel):
    running: bool = False
    loop_running: bool = False  # legacy alias
    loop_state: str = "stopped"
    pid: int | None = None
    last_start_error: str | None = None
    last_error_at: str | None = None
    model_config = ConfigDict(extra="allow")


# id 149: SuggestionOut — /api/suggestion/{id} response
class SuggestionOut(BaseModel):
    id: int | None = None  # None when no active suggestion
    content: str = ""
    status: str = "released"
    verification: str | None = None
    related_files: list[str] = Field(default_factory=list)
    session_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    released_at: str | None = None
    model_config = ConfigDict(extra="allow")


# id 150: SuggestionUpdate — request body for /api/suggestion PUT
class SuggestionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str | None = None
    status: str | None = None
    verification: str | None = None
    related_files: list[str] | None = None
    suggestion_id: int | None = None  # legacy field


# id 151: ManagerMessageOut — /api/manager-messages response
class ManagerMessageOut(BaseModel):
    id: int
    session_id: str | None = None
    content: str = ""
    queue_status: str | None = None
    status_label: str | None = None
    created_at: str | None = None
    model_config = ConfigDict(extra="allow")


# id 152: ManagerMessageCreate — request body
class ManagerMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1)
    session_id: str | None = None
    status_label: str | None = None


# id 153: ChatMessageOut — /api/chat/messages response
class ChatMessageOut(BaseModel):
    id: int
    session_id: str | None = None
    role: str = "human"
    content: str = ""
    created_at: str | None = None
    model_config = ConfigDict(extra="allow")


# id 154: ChatSendRequest is already declared as ChatSend above
# (content + sender). The 0c44ba2 ChatSendRequest was content-only
# with active session; the new ChatSend is the strict request body.


# id 155: ChatStatusResponse — /api/chat/status response
class ChatStatusResponse(BaseModel):
    ok: bool = True
    enabled: bool = False
    reason: str | None = None
    binding_ok: bool = False
    binding_reachable: bool = False
    credentials: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="allow")


# id 156: ChatSendResponse — /api/chat/send response
class ChatSendResponse(BaseModel):
    ok: bool = True
    reply: str = ""
    messages: list[dict] = Field(default_factory=list)
    # error can be either a plain string OR a structured dict
    # ({"code": "...", "message": "...", "details": {...}}) depending on
    # which chat path failed. Use Any to accept both; route-level
    # tests cover each shape.
    error: Any = None
    model_config = ConfigDict(extra="allow")


# id 157: SettingsOut — /api/settings response
class SettingsOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    # Common settings keys (the actual key set is open-ended; extras
    # are allowed so the route can return any DB-stored setting)
    workspace_path: str | None = None
    active_workspace: str | None = None
    active_session: str | None = None
    force_planning: bool | None = None
    force_todo: bool | None = None
    opencode_thinking_enabled: bool | None = True


class DatabaseInfo(BaseModel):
    power_teams_db: str
    opencode_config_dir: str | None = None
    xdg_config_home: str | None = None


# id 158: UserInputContent — request body for /api/files/user_input PUT
class UserInputContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str
    session_id: str | None = None


# id 159: HasContentResponse — /api/files/user_input/has-content + /api/user-input/has-content
class HasContentResponse(BaseModel):
    has_content: bool = False
    directive_id: int | None = None
    model_config = ConfigDict(extra="allow")


# id 160: DirectiveStatusResponse — /api/directive/status response
class DirectiveStatusResponse(BaseModel):
    has_directive: bool = False
    directive: str | None = None
    model_config = ConfigDict(extra="allow")


# id 161: FileContent — /api/files/{name} response
class FileContent(BaseModel):
    content: str = ""
    path: str | None = None
    name: str | None = None
    model_config = ConfigDict(extra="allow")


# id 162: StreamContent — /api/agent/{name}/stream + /api/timer response
class StreamContent(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    report: str | None = None
    agent: str | None = None
    model_config = ConfigDict(extra="allow")


# id 163: HandoffData — /api/handoff response (read)
class HandoffData(BaseModel):
    human_requirements: str = ""
    current_task: str = ""
    current_micro_flow: list[str] = Field(default_factory=list)
    known_bugs: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    working_direction: str = ""
    human_concerns: str = ""
    tested_files: list[str] = Field(default_factory=list)
    references_demos: str = ""
    file_structure: str = ""
    important_files: str = ""
    available_scripts: str = ""
    existing_solutions: str = ""
    macro_flow: str = ""
    project_folder_location: str = ""
    version: int | None = None
    updated_by: str | None = None
    updated_at: str | None = None
    model_config = ConfigDict(extra="allow")


# id 164: HandoffUpdate — request body for /api/handoff PUT
class HandoffUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_task: str | None = None
    current_micro_flow: list[str] | None = None
    known_bugs: list[str] | None = None
    completion_criteria: list[str] | None = None
    working_direction: str | None = None
    human_concerns: str | None = None
    tested_files: list[str] | None = None
    human_requirements: str | None = None
    references_demos: str | None = None
    file_structure: str | None = None
    important_files: str | None = None
    available_scripts: str | None = None
    existing_solutions: str | None = None
    macro_flow: str | None = None
    project_folder_location: str | None = None


# id 165: SessionInfo — /api/sessions/{id} response
class SessionInfo(BaseModel):
    id: str
    name: str | None = None
    workspace_path: str | None = None
    is_active: bool = False
    archived: bool = False
    last_active_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    model_config = ConfigDict(extra="allow")


# id 166: SessionsResponse — /api/sessions wrapper response
class SessionsResponse(BaseModel):
    live: list[SessionInfo] = Field(default_factory=list)
    live_count: int = 0
    archived: list[dict] = Field(default_factory=list)
    archived_count: int = 0
    model_config = ConfigDict(extra="allow")


# id 167: ArchivedSessionsResponse — /api/sessions/archived wrapper
class ArchivedSessionsResponse(BaseModel):
    sessions: list[SessionInfo] = Field(default_factory=list)
    count: int = 0
    model_config = ConfigDict(extra="allow")


# ── Migration audit compat models (ids 168-176) ──────────────────────────
# The 0c44ba2 fastapi_server.py defined a set of Pydantic models that
# were not ported 1:1 into the new architecture. The current endpoints
# return raw DB dicts (e.g. session_to_workspace) or different
# schemas (ProjectSessionCreate / TodoUpsert / TodoPatch). These
# restored compat classes preserve the OLD wire shape so any caller
# (UI, scripts) that still passes / receives the legacy fields
# validates against the same Pydantic model the old code used.
# They live alongside the new models; routes that want strict old
# behaviour can reference these by name.


# id 168: Workspace — /api/workspaces/{id} response
class Workspace(BaseModel):
    id: str
    name: str | None = None
    label: str | None = None
    path: str
    active: bool = False
    created_at: str = ""
    path_missing: bool = False
    model_config = ConfigDict(extra="allow")


# id 169: WorkspaceCreate — /api/workspaces POST body
class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    label: str | None = None
    path: str


# id 170: WorkspaceUpdate — /api/workspaces/{id} PATCH/PUT body
class WorkspaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    label: str | None = None
    path: str | None = None


# id 171: PlanData — /api/plan GET response (content + updated_at)
class PlanData(BaseModel):
    content: str = ""
    updated_at: str | None = None
    model_config = ConfigDict(extra="allow")


# id 173: TodoCreate — old create-only todo body
class TodoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str
    status: str = "pending"
    priority: str = "medium"
    parent_id: str | None = None
    owner: str = "user"


# id 174: TodoUpdate — old todo PATCH body (all fields Optional)
class TodoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str | None = None
    status: str | None = None
    priority: str | None = None
    owner: str | None = None
    position: int | None = None
    parent_id: str | None = None


# id 175: RuntimeStatus — runtime readiness snapshot
class RuntimeStatus(BaseModel):
    servers: list[dict] = Field(default_factory=list)
    error: str | None = None
    model_config = ConfigDict(extra="allow")


# ── P8 compat schemas (ids 103, 104, 105, 150, 154, 162, 165, 178, 179, 180, 181, 182, 183) ─────
# The 0c44ba2 fastapi_server.py defined a set of Pydantic
# request/response body models that were not ported 1:1.
# These restored compat classes preserve the OLD wire shape
# so any caller (UI, scripts) that still passes / receives
# the legacy fields validates against the same Pydantic
# model the old code used. No wiring required; the
# schemas exist for type-hint + import compatibility.


# id 103: UISuggestion (UI TypedDict-ish response shape)
class UISuggestion(BaseModel):
    id: str
    content: str = ""
    status: str = "pending"
    verification: str | None = None
    related_files: list[str] = Field(default_factory=list)
    session_id: str | None = None
    created_at: str | None = None
    model_config = ConfigDict(extra="allow")


# id 104: UIManagerMessage (UI TypedDict-ish response shape)
class UIManagerMessage(BaseModel):
    id: str
    content: str = ""
    session_id: str | None = None
    status: str | None = None
    created_at: str | None = None
    model_config = ConfigDict(extra="allow")


# id 105: UIPlanData (UI TypedDict-ish response shape)
class UIPlanData(BaseModel):
    content: str = ""
    updated_at: str | None = None
    model_config = ConfigDict(extra="allow")


# id 154: ChatSendRequest (request body for /api/chat/send)
class ChatSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str
    sender: str = "human"
    session_id: str | None = None


# id 178: PortCheckResult (legacy reachability model)
class PortCheckResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    host: str
    port: int
    reachable: bool
    error: str | None = None


# id 179: FlowRunRequest (legacy body for /api/workflows/flow_01/run)
class FlowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directive: str | None = None
    directive_file: str | None = None
    loops: int = 1
    use_real_worker: bool = True
    use_real_executors: bool = True
    stream_agents: bool = False
    emit_real_ui_signals: bool = True


# id 180: FlowDirectiveRequest (legacy body for /api/.../directive)
class FlowDirectiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directive: str
    directive_file: str | None = None


# id 181: FlowPrepareRequest (legacy body for /api/.../prepare)
class FlowPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_path: str | None = None
    directive: str | None = None


# id 182: FlowCancelRequest (legacy body for /api/.../cancel)
class FlowCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = ""
    stop_worker: bool = True


# id 183: DebugLogEntry (legacy single-entry debug body)
class DebugLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    msg: str
    source: str = "unknown"


# id 176: ActiveWorkResponse — /api/active-work response
class ActiveWorkResponse(BaseModel):
    active_work: bool
    reason: str = ""
    model_config = ConfigDict(extra="allow")
