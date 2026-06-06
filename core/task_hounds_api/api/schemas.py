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
