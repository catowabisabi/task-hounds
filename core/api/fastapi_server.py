"""
Task Hounds FastAPI server.

Run standalone:
    python -m api.fastapi_server --port 8765

Swagger UI:  http://localhost:8765/docs
ReDoc:       http://localhost:8765/redoc
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]


def _load_env_files() -> None:
    """Load repo .env files without overriding explicit process env vars."""
    env_paths = [ROOT / ".env", ROOT / "config" / ".env"]
    try:
        from dotenv import load_dotenv

        for env_path in env_paths:
            if env_path.exists():
                load_dotenv(env_path, override=False, encoding="utf-8-sig")
        return
    except Exception:
        pass

    for env_path in env_paths:
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env_files()

RUNTIME_DIR = Path(os.environ.get("POWER_TEAMS_RUNTIME_DIR", str(ROOT / "core" / "runtime")))
RUNTIME_FILES = RUNTIME_DIR / "agent_files"
DB_PATH = Path(os.environ.get("POWER_TEAMS_DB", str(ROOT / "core" / "db" / "power_teams.db")))
WEB_DIST = ROOT / "ui" / "web" / "dist"

UTC = timezone.utc
PYTHONPATH_ENTRIES = [*(os.environ.get("PYTHONPATH", "").split(os.pathsep) if os.environ.get("PYTHONPATH") else []), str(ROOT / "core"), str(ROOT / "backend")]
for _entry in reversed(PYTHONPATH_ENTRIES):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

RUN_LOG = RUNTIME_DIR / "logs" / "desktop-run-cycle.log"
DEFAULT_STREAM_AGENTS = ("manager", "worker", "reviewer", "chat")

# ── Legacy service facades ────────────────────────────────────────────────────
from api.services.legacy import services as _api_services

get_db_agents = _api_services.agents.list
read_settings = _api_services.settings.read
write_settings = _api_services.settings.write
read_active_runtime_file = _api_services.runtime_files.read_active
read_runtime = _api_services.runtime_files.read
write_runtime = _api_services.runtime_files.write
active_runtime_file = _api_services.runtime_files.active_file
active_agent_stream_path = _api_services.streams.active_path
agent_stream_path = _api_services.streams.legacy_path
agent_timer_path = _api_services.streams.timer_path
get_active_project_session_id = _api_services.settings.active_project_session_id
get_chat_runtime_status = _api_services.chat.runtime_status
get_chat_messages_data = _api_services.chat.messages
render_chat_stream_from_history = _api_services.streams.render_chat_from_history
get_handoff_data = _api_services.handoff.current
get_handoff_versions_data = _api_services.handoff.versions
get_suggestion_data = _api_services.suggestions.current
get_manager_messages_data = _api_services.manager_messages.list
update_agent_state = _api_services.agents.update_state
utc_now = _api_services.utils.utc_now
debug_log = _api_services.utils.debug_log
model_options = _api_services.opencode.model_options
fetch_json = _api_services.opencode.fetch_json
resolve_opencode_agent = _api_services.opencode.resolve_agent
repair_mojibake = _api_services.text.repair_mojibake
extract_reasoning = _api_services.text.extract_reasoning
split_answer_and_thinking = _api_services.text.split_answer_and_thinking
extract_tools = _api_services.text.extract_tools
sse_event = _api_services.utils.sse_event
is_opencode_http_reachable = _api_services.opencode.is_http_reachable
_opencode_enabled = _api_services.opencode.enabled
start_mvp_loop = _api_services.loop.start
stop_mvp_loop = _api_services.loop.stop
run_mvp_cycle = _api_services.loop.run_cycle
stop_mvp_cycle = _api_services.loop.stop_cycle
loop_status = _api_services.loop.status
ensure_opencode_servers = _api_services.opencode.ensure_servers
_DEFAULT_STREAM_AGENTS = _api_services.streams.default_agents
_RUN_LOG = _api_services.loop.run_log
_db = _api_services.db.module

RUNTIME_FILES.mkdir(parents=True, exist_ok=True)
_RUN_LOG.parent.mkdir(parents=True, exist_ok=True)


def ensure_backend_ready() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_FILES.mkdir(parents=True, exist_ok=True)
    _db().init_db(DB_PATH)
    _db().seed_default_agents(DB_PATH)


def ensure_runtime_ready(*, restart_managed: bool = False) -> None:
    if not _opencode_enabled:
        return
    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager, cleanup_orphan_opencode_servers
        from power_teams.runtime.opencode_supervisor import find_free_port
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        cleanup_result = None
        if restart_managed:
            cleanup_result = cleanup_orphan_opencode_servers(db_path=DB_PATH)
            _append_text(_RUN_LOG, f"[{utc_now()}] fastapi orphan cleanup: {cleanup_result}\n")
        result = mgr.reconcile_runtime(start_if_missing=False, restart_unowned=restart_managed)
        if not result.get("selected"):
            started = mgr.start_managed_server(port=find_free_port())
            if "error" not in started:
                result = mgr.reconcile_runtime(start_if_missing=False, restart_unowned=False)
            else:
                result = {**result, "started": started}
                raise RuntimeError(started["error"])
        _append_text(_RUN_LOG, f"[{utc_now()}] fastapi runtime reconcile: {result}\n")
    except Exception as exc:
        _append_text(_RUN_LOG, f"[{utc_now()}] fastapi runtime reconcile failed: {exc}\n")


def write_active_runtime_file(name: str, value: str) -> None:
    path = active_runtime_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    legacy = RUNTIME_FILES / name
    if legacy != path:
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(value, encoding="utf-8")


# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Task Hounds API",
    description="Multi-agent orchestration system API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    ensure_backend_ready()
    ensure_runtime_ready(restart_managed=True)


@app.on_event("shutdown")
def shutdown() -> None:
    if not _opencode_enabled:
        return
    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        OpenCodeLifecycleManager(db_path=DB_PATH).stop_all_managed(reason="backend_exit")
        _append_text(_RUN_LOG, f"[{utc_now()}] fastapi shutdown cleanup done\n")
    except Exception as exc:
        _append_text(_RUN_LOG, f"[{utc_now()}] fastapi shutdown cleanup error: {exc}\n")


# ── Auth ─────────────────────────────────────────────────────────────────────
def get_api_secret_key() -> str:
    return os.environ.get("API_SECRET_KEY", "")


def check_auth(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    secret = get_api_secret_key()
    if not secret:
        return
    if not x_api_key or x_api_key != secret:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Pydantic Models ─────────────────────────────────────────────────────────

class OkResponse(BaseModel):
    ok: bool = True


class HealthResponse(BaseModel):
    status: str = "ok"
    active_project_session: str | None = None
    version: str = "0.1.0"


# -- Agent --
class Agent(BaseModel):
    id: str | int
    name: str
    role: str
    host: str
    port: int
    model: str | None = None
    opencode_agent: str
    state: str
    task_complete: int = 0
    current_step: str | None = None
    current_step_started_at: str | None = None
    last_stream_at: str | None = None
    last_seen: str | None = None
    last_error: str | None = None
    session_id: str | None = None
    backend_type: str = "opencode"
    backend_config_json: str | None = None
    binding_source: str | None = None


class AgentUpdate(BaseModel):
    host: str | None = None
    port: int | None = None
    model: str | None = None
    opencode_agent: str | None = None
    state: str | None = None
    task_complete: int | None = None
    session_id: str | None = None
    backend_type: str | None = None
    backend_config_json: str | None = None


# -- Loop --
class LoopStatus(BaseModel):
    running: bool
    pid: int | None = None


# -- Suggestion --
class Suggestion(BaseModel):
    id: int | None = None
    content: str | None = None
    status: str | None = None
    queue_status: str | None = None
    status_label: str | None = None
    verification: str | None = None
    related_files: list[str] | None = None
    created_at: str | None = None


class SuggestionUpdate(BaseModel):
    status: str | None = None
    verification: str | None = None
    content: str | None = None


# -- Manager Message --
class ManagerMessage(BaseModel):
    id: int
    content: str
    created_at: str
    is_human: bool | None = None
    queue_status: str | None = None
    status_label: str | None = None


class ManagerMessageCreate(BaseModel):
    content: str


# -- Chat --
class ChatMessage(BaseModel):
    id: int
    session_id: str
    sender: str
    content: str
    created_at: str


class ChatSendRequest(BaseModel):
    content: str


class ChatStatusResponse(BaseModel):
    enabled: bool
    reason: str | None = None
    binding: dict | None = None


class ChatSendResponse(BaseModel):
    ok: bool
    reply: str | None = None
    messages: list[ChatMessage] | None = None
    error: str | None = None


# -- Settings --
class Settings(BaseModel):
    language: str = "en"
    auto_release: bool = True
    active_project_session: str | None = None
    custom_languages: list[str] = []


# -- User Input --
class UserInputContent(BaseModel):
    content: str


class HasContentResponse(BaseModel):
    has_content: bool


class DirectiveStatusResponse(BaseModel):
    has_directive: bool
    directive_content: str = ""


# -- Files --
class FileContent(BaseModel):
    content: str


# -- Stream --
class StreamContent(BaseModel):
    content: str = ""


# -- Handoff --
class HandoffData(BaseModel):
    id: int | None = None
    version: int | None = None
    human_requirements: str | None = None
    working_direction: str | None = None
    file_structure: str | None = None
    updated_by: str | None = None
    created_at: str | None = None


class HandoffUpdate(BaseModel):
    human_requirements: str | None = None
    working_direction: str | None = None
    file_structure: str | None = None


# -- Sessions --
class SessionInfo(BaseModel):
    session_key: str
    session_name: str
    agent_name: str | None = None
    folder_relation: str = ""
    created_at: str = ""
    last_active_at: str = ""
    worker_status: str = ""
    token_usage: int = 0
    archived: bool = False


class SessionsResponse(BaseModel):
    live: list[SessionInfo]
    live_count: int
    archived_count: int


class ArchivedSessionsResponse(BaseModel):
    sessions: list[SessionInfo]


# -- Workspaces --
class Workspace(BaseModel):
    id: str
    name: str | None = None
    label: str | None = None
    path: str
    active: bool = False
    created_at: str = ""
    path_missing: bool = False


class WorkspaceCreate(BaseModel):
    name: str | None = None
    label: str | None = None
    path: str


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    label: str | None = None
    path: str | None = None


# -- Plan / Todo --
class PlanData(BaseModel):
    content: str = ""
    updated_at: str | None = None


class TodoItem(BaseModel):
    id: str
    session_id: str | None = None
    content: str
    status: str = "pending"
    priority: str = "medium"
    position: int = 0
    parent_id: str | None = None
    owner: str | None = None
    updated_at: str | None = None


class TodoCreate(BaseModel):
    content: str
    status: str = "pending"
    priority: str = "medium"
    parent_id: str | None = None
    owner: str = "user"


class TodoUpdate(BaseModel):
    content: str | None = None
    status: str | None = None
    priority: str | None = None
    owner: str | None = None
    position: int | None = None
    parent_id: str | None = None


# -- Runtime --
class RuntimeStatus(BaseModel):
    servers: list[dict] = []
    error: str | None = None


class ActiveWorkResponse(BaseModel):
    active_work: bool
    reason: str = ""


class BackendList(BaseModel):
    backends: list[str]
    error: str | None = None


class PortCheckResult(BaseModel):
    reachable: dict[str, bool]


# -- Workflows --
class FlowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loops: int = Field(default=1, ge=1, le=5)
    directive: str | None = None
    directive_file: str | None = None
    suggested_task: str | None = None
    thought: str | None = None
    manager_message: str | None = None
    workspace_path: str | None = None
    emit_real_ui_signals: bool = True
    use_real_worker: bool = True


class FlowDirectiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directive: str
    workspace_path: str | None = None
    directive_file: str | None = None


class FlowPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_path: str | None = None
    directive: str | None = None


class FlowCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = "user_requested"
    stop_worker: bool = True


# -- Debug --
class DebugLogEntry(BaseModel):
    msg: str
    source: str = "frontend"


def _append_text(path: Path, value: str) -> None:
    with path.open("a", encoding="utf-8") as h:
        h.write(value)

def _read_json_body(request: Request) -> dict:
    body = request._body
    if not body:
        return {}
    return json.loads(body)


# ══════════════════════════════════════════════════════════════════════════════
#  HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/ping", tags=["health"])
def ping():
    return {"ok": True}


@app.get("/api/health", tags=["health"])
def health():
    ensure_backend_ready()
    import subprocess
    backend_version = "dev"
    try:
        git_dir = ROOT / ".git"
        if git_dir.exists():
            result = subprocess.run(
                ["git", "describe", "--always"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                backend_version = result.stdout.strip()
    except Exception:
        pass
    active_context = _db().get_active_context(path=DB_PATH)
    manager_row = None
    agents = get_db_agents()
    for agent in agents:
        if isinstance(agent, dict) and agent.get("name") == "manager":
            manager_row = agent
            break
    shared_host = manager_row.get("host") if manager_row else None
    shared_port = manager_row.get("port") if manager_row else None

    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        lc = OpenCodeLifecycleManager(db_path=DB_PATH)
        health_data = lc.get_runtime_health(session_id=active_context.get("project_session_id"))
        managed_count = health_data.get("managed_opencode_count", 0)
        external_count = health_data.get("external_opencode_count", 0)
        active_work = health_data.get("active_work", False)
        last_checkpoint = health_data.get("last_checkpoint")
        role_bindings = health_data.get("role_bindings", [])
        runtime_policy = health_data.get("policy", {})
    except Exception:
        managed_count = 0
        external_count = 0
        active_work = False
        last_checkpoint = None
        role_bindings = []
        runtime_policy = {}

    return {
        "ok": True,
        "timestamp": utc_now(),
        "backend_version": backend_version,
        "db_path": str(DB_PATH),
        "active_workspace_id": active_context.get("workspace_id"),
        "active_project_session": active_context.get("project_session_id"),
        "shared_opencode_host": shared_host,
        "shared_opencode_port": shared_port,
        "opencode_enabled": _opencode_enabled,
        "managed_opencode_count": managed_count,
        "external_opencode_count": external_count,
        "active_work": active_work,
        "last_checkpoint": last_checkpoint,
        "role_bindings": role_bindings,
        "runtime_policy": runtime_policy,
    }


@app.post("/api/health", tags=["health"])
def health_post():
    return health()


@app.get("/api/agents", response_model=list[Agent], tags=["agents"])
def get_agents():
    ensure_backend_ready()
    return get_db_agents()


@app.put("/api/agents/{name}", tags=["agents"])
def update_agent(name: str, body: AgentUpdate):
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        updates = []
        values = []
        if body.host is not None:
            updates.append("host = ?")
            values.append(body.host)
        if body.port is not None:
            updates.append("port = ?")
            values.append(int(body.port))
        if "model" in body.model_dump(exclude_unset=True):
            updates.append("model = ?")
            values.append(body.model)
        if body.opencode_agent is not None:
            updates.append("opencode_agent = ?")
            values.append(body.opencode_agent or "general")
        if body.state is not None:
            updates.append("state = ?")
            values.append(body.state)
        if body.task_complete is not None:
            updates.append("task_complete = ?")
            values.append(int(bool(body.task_complete)))
        if body.session_id is not None:
            updates.append("session_id = ?")
            values.append(body.session_id)
        if body.backend_type is not None:
            updates.append("backend_type = ?")
            values.append(body.backend_type)
        if body.backend_config_json is not None:
            updates.append("backend_config_json = ?")
            values.append(body.backend_config_json)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.append(name)
        if updates:
            query = f"UPDATE agent_registry SET {', '.join(updates)} WHERE name = ?"
            conn.execute(query, values)
            conn.commit()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    session_file = ROOT / "core" / "runtime" / "sessions" / "session_state.json"
    if session_file.exists():
        try:
            sessions = json.loads(session_file.read_text(encoding="utf-8"))
            if name in sessions:
                del sessions[name]
                session_file.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return {"ok": True}


@app.post("/api/agents/{name}/kill", tags=["agents"])
def agent_kill(name: str):
    if name not in _DEFAULT_STREAM_AGENTS:
        raise HTTPException(status_code=404, detail="unknown agent")
    killed = []
    pid_paths = [
        active_agent_stream_path(name).parent / f"{name}_opencode.pid",
        agent_stream_path(name).parent / f"{name}_opencode.pid",
    ]
    for pid_path in pid_paths:
        try:
            if not pid_path.exists():
                continue
            pid = int(pid_path.read_text(encoding="utf-8").strip() or "0")
            if pid <= 0:
                continue
            if os.name == "nt":
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=False,
                )
            else:
                os.kill(pid, 15)
            killed.append(pid)
            pid_path.unlink(missing_ok=True)
        except Exception:
            pass
    try:
        from power_teams.db import update_agent
        update_agent(name, state="idle", last_seen=utc_now())
    except Exception:
        pass
    _append_text(active_agent_stream_path(name), json.dumps({"t": "sys", "msg": "kill requested"}) + "\n")
    legacy_stream = agent_stream_path(name)
    if legacy_stream != active_agent_stream_path(name):
        _append_text(legacy_stream, json.dumps({"t": "sys", "msg": "kill requested"}) + "\n")
    return {"ok": True, "killed": killed}


@app.post("/api/agents/{name}/health", tags=["agents"])
def agent_health(name: str):
    agents = get_db_agents()
    row = next((a for a in agents if a.get("name") == name), None)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    try:
        from power_teams.runtime.backend_registry import get_backend
        adapter = get_backend(row)
        result = adapter.health()
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/agents/{name}/clear-error", tags=["agents"])
def agent_clear_error(name: str):
    try:
        from power_teams.db import update_agent, get_agent
        row = get_agent(name, path=DB_PATH)
        if row and row["state"] == "error":
            update_agent(name, state="idle", last_error=None, last_seen=utc_now())
        else:
            update_agent(name, last_error=None, last_seen=utc_now())
        return {"ok": True, "role": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agents/{name}/retry", tags=["agents"])
def agent_retry(name: str):
    try:
        from power_teams.db import update_agent
        update_agent(name, state="idle", last_error=None, task_complete=0)
        return {"ok": True, "role": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agents/{name}/mark-resolved", tags=["agents"])
def agent_mark_resolved(name: str):
    try:
        from power_teams.db import update_agent, get_agent
        row = get_agent(name, path=DB_PATH)
        if row and row["state"] == "error":
            update_agent(name, state="idle", last_error=None)
        else:
            update_agent(name, last_error=None)
        return {"ok": True, "role": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
#  LOOP
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/loop/status", response_model=LoopStatus, tags=["loop"])
def get_loop_status():
    return loop_status()


@app.post("/api/loop/start", tags=["loop"])
def loop_start():
    try:
        started = start_mvp_loop()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if started is None:
        return {"ok": False, "started": False, "error": "no_directive", "message": "Start Loop needs a Human Directive for this project/session.", **loop_status()}
    return {"ok": True, "started": started, **loop_status()}


@app.post("/api/loop/stop", tags=["loop"])
def loop_stop():
    stopped = stop_mvp_loop()
    return {"ok": True, "stopped": stopped, **loop_status()}


# ══════════════════════════════════════════════════════════════════════════════
#  RUN CYCLE
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/run-cycle", tags=["loop"])
def run_cycle():
    if not _opencode_enabled:
        raise HTTPException(status_code=503, detail="opencode_disabled")
    if not read_active_runtime_file("user_input.txt").lstrip("\ufeff").strip():
        raise HTTPException(status_code=409, detail="no_directive")
    run_mvp_cycle()
    return {"ok": True, "message": "MVP cycle started"}


@app.post("/api/run-cycle/stop", tags=["loop"])
def run_cycle_stop():
    stopped = stop_mvp_cycle()
    return {"ok": True, "stopped": stopped, "message": "MVP cycle stop requested"}


# ══════════════════════════════════════════════════════════════════════════════
#  SESSION
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  SESSION
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/session/reset", tags=["session"])
def session_reset():
    try:
        stop_mvp_cycle()
        stop_mvp_loop()
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("UPDATE agent_registry SET session_id = NULL, state = 'idle', task_complete = 0")
            conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (get_active_project_session_id(),))
            conn.commit()
        finally:
            conn.close()
        session_file = RUNTIME_DIR / "sessions" / "session_state.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text("{}", encoding="utf-8")
        if _opencode_enabled:
            ensure_opencode_servers()
        for agent_name in _DEFAULT_STREAM_AGENTS:
            message = "[system] sessions reset; next run will create fresh OpenCode sessions\n"
            stream_file = active_agent_stream_path(agent_name)
            stream_file.parent.mkdir(parents=True, exist_ok=True)
            stream_file.write_text(message, encoding="utf-8")
            legacy_stream = agent_stream_path(agent_name)
            if legacy_stream != stream_file:
                legacy_stream.parent.mkdir(parents=True, exist_ok=True)
                legacy_stream.write_text(message, encoding="utf-8")
        write_active_runtime_file("work_0001_status.txt", "idle\n")
        _append_text(_RUN_LOG, f"[{utc_now()}] sessions reset by user\n")
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/sessions", response_model=SessionsResponse, tags=["session"])
def get_sessions():
    live = _db().list_live_sessions(path=DB_PATH)
    arch_count = _db().get_sessions_arch_count(path=DB_PATH)
    return {"live": live, "live_count": len(live), "archived_count": arch_count}


@app.get("/api/sessions/archived", response_model=ArchivedSessionsResponse, tags=["session"])
def get_archived_sessions():
    sessions = _db().list_sessions_arch(path=DB_PATH)
    return {"sessions": [dict(s) for s in sessions]}


@app.put("/api/sessions/archive/{session_key}", tags=["session"])
def session_archive_toggle(session_key: str):
    is_archived = _db().list_sessions_arch(path=DB_PATH)
    already = any(s.get("session_key") == session_key for s in is_archived)
    if already:
        return {"ok": True, "state": "archived", "session_key": session_key}
    session = next((s for s in _db().list_live_sessions(path=DB_PATH) if s.get("session_key") == session_key), None)
    if session:
        _db().create_session_arch(
            session_key=session_key,
            session_name=session.get("session_name", session_key),
            agent_name=session.get("agent_name"),
            folder_relation=session.get("folder_relation", ""),
            worker_status=session.get("worker_status", ""),
            token_usage=session.get("token_usage", 0),
            path=DB_PATH
        )
    return {"ok": True, "archived": session_key}


@app.delete("/api/sessions/archive/{session_key}", tags=["session"])
def session_archive_delete(session_key: str):
    _db().create_session_arch(
        session_key=session_key,
        session_name=session_key,
        agent_name=None,
        folder_relation="",
        worker_status="",
        token_usage=0,
        path=DB_PATH
    )
    return {"ok": True, "archived": session_key}


# -- Project Sessions --
@app.post("/api/project-sessions/{session_id}/switch", tags=["session"])
def project_session_switch(session_id: str):
    from power_teams.db import get_project_session, connect
    row = get_project_session(session_id, path=DB_PATH)
    if not row:
        raise HTTPException(status_code=404, detail="session not found")
    ws_id = row["workspace_id"]
    ws_path = row["workspace_path"] or ""
    with connect(DB_PATH) as db:
        db.execute("UPDATE project_sessions SET is_active=0 WHERE workspace_id=?", (ws_id,))
        db.execute("UPDATE project_sessions SET is_active=1 WHERE id=?", (session_id,))
        db.commit()
    new_settings = dict(read_settings())
    new_settings["active_workspace_id"] = ws_id
    new_settings["active_project_session"] = session_id
    new_settings["workspace_id"] = ws_id
    new_settings["workspace_path"] = ws_path
    new_settings["project_session_id"] = session_id
    write_settings(new_settings)
    if row["path_missing"] == 1 or not ws_path:
        return {"ok": True, "session_id": session_id, "workspace_id": ws_id, "warning": "workspace_path_missing"}
    return {"ok": True, "session_id": session_id, "workspace_id": ws_id}


@app.patch("/api/project-sessions/{session_id}", tags=["session"])
async def project_session_patch(session_id: str, request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    name = (payload.get("name") or "").strip()
    if name:
        from power_teams.db import update_project_session
        update_project_session(session_id, path=DB_PATH, name=name)
    return {"ok": True, "session_id": session_id, "updated": True, "name": name}


@app.delete("/api/project-sessions/{session_id}", tags=["session"])
def project_session_delete(session_id: str):
    from power_teams.db import connect
    with connect(DB_PATH) as db:
        row = db.execute("SELECT workspace_id FROM project_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="session not found")
        db.execute("DELETE FROM project_sessions WHERE id=?", (session_id,))
        db.commit()
    return {"ok": True, "session_id": session_id}


# ══════════════════════════════════════════════════════════════════════════════
#  SUGGESTION
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/suggestion", response_model=Suggestion, tags=["suggestion"])
def get_suggestion():
    return get_suggestion_data() or {}


@app.get("/api/suggestions/unscoped", response_model=list[Suggestion], tags=["suggestion"])
def get_unscoped_suggestions():
    try:
        if get_active_project_session_id() != "legacy":
            return []
        rows = _db().list_unscoped_active_suggestions(path=DB_PATH)
        data = []
        for row in rows:
            item = dict(row)
            status = item.get("status") or "pending"
            item["queue_status"] = {
                "pending": "queued_for_manager",
                "released": "queued_for_worker",
                "worker_done": "manager_reviewing",
                "done": "processed",
                "paused": "paused",
            }.get(status, status)
            item["status_label"] = {
                "queued_for_manager": "Queued for manager",
                "queued_for_worker": "Queued for worker",
                "manager_reviewing": "Manager reviewing",
                "processed": "Processed",
                "paused": "Paused",
            }.get(item["queue_status"], status)
            data.append(item)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/suggestion", tags=["suggestion"])
def update_suggestion(body: SuggestionUpdate):
    sid = body.id
    try:
        if sid:
            _db().update_suggestion(int(sid), path=DB_PATH, **{k: v for k, v in body.model_dump().items() if v is not None and k != "id"})
            return {"ok": True}
        else:
            new_id = _db().create_suggestion(
                content=body.content or "",
                verification=body.verification,
                related_files=None,
                path=DB_PATH,
            )
            return {"ok": True, "id": new_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/suggestion/pause", tags=["suggestion"])
def suggestion_pause():
    try:
        db = _db()
        row = db.get_active_suggestion(session_id=get_active_project_session_id(), path=DB_PATH)
        if not row:
            raise HTTPException(status_code=404, detail="no active suggestion")
        db.update_suggestion(row["id"], status="paused", path=DB_PATH)
        return {"ok": True, "status": "paused"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/suggestion/release", tags=["suggestion"])
def suggestion_release():
    try:
        db = _db()
        row = db.get_active_suggestion(session_id=get_active_project_session_id(), path=DB_PATH)
        if not row:
            raise HTTPException(status_code=404, detail="no active suggestion")
        db.update_suggestion(row["id"], status="released", path=DB_PATH)
        return {"ok": True, "status": "released"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/suggestion/done", tags=["suggestion"])
def suggestion_done():
    try:
        db = _db()
        row = db.get_active_suggestion(session_id=get_active_project_session_id(), path=DB_PATH)
        if not row:
            raise HTTPException(status_code=404, detail="no active suggestion")
        db.update_suggestion(row["id"], status="done", path=DB_PATH)
        return {"ok": True, "status": "done"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/suggestion/new", tags=["suggestion"])
async def suggestion_new(request: Request):
    try:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        content = str(payload.get("content") or "").strip()
        verification = payload.get("verification")
        if not content:
            raise HTTPException(status_code=400, detail="content is required")
        db = _db()
        new_id = db.create_suggestion(
            content=content,
            verification=verification,
            related_files=None,
            session_id=get_active_project_session_id(),
            path=DB_PATH,
        )
        db.update_suggestion(new_id, status="pending", path=DB_PATH)
        return {"ok": True, "id": new_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
#  MANAGER MESSAGES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/manager-messages", response_model=list[ManagerMessage], tags=["messages"])
def get_manager_messages():
    return get_manager_messages_data()


@app.post("/api/manager-messages", tags=["messages"])
def create_manager_message(body: ManagerMessageCreate):
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    try:
        new_id = _db().add_manager_message(
            f"Human message to manager: {content}",
            session_id=get_active_project_session_id(),
            path=DB_PATH,
        )
        return {"ok": True, "id": new_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
#  CHAT
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/chat/status", response_model=ChatStatusResponse, tags=["chat"])
def chat_status():
    status = get_chat_runtime_status()
    if not status.get("enabled") and "not reachable" in str(status.get("reason", "")):
        ensure_runtime_ready()
        status = get_chat_runtime_status()
    return status


@app.get("/api/chat/messages", response_model=list[ChatMessage], tags=["chat"])
def chat_messages(limit: int = Query(default=50)):
    return get_chat_messages_data(limit=limit)


@app.post("/api/chat/send", response_model=ChatSendResponse, tags=["chat"])
def chat_send(body: ChatSendRequest):
    runtime_status = get_chat_runtime_status()
    if not runtime_status.get("enabled") and "not reachable" in str(runtime_status.get("reason", "")):
        ensure_runtime_ready()
        runtime_status = get_chat_runtime_status()
    if not runtime_status.get("enabled"):
        raise HTTPException(status_code=503, detail=runtime_status.get("reason") or "chat_runtime_unavailable")
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    session_id = get_active_project_session_id()
    role_session_id = f"{session_id}:chat"
    try:
        _db().init_db(DB_PATH)
        from power_teams.skills.db_skill import write_operation
        from power_teams.agents.base import send_to_agent
        from power_teams.db import update_agent

        active_context = _db().get_active_context(path=DB_PATH)
        workspace_path = active_context.get("workspace_path") or ""
        if active_context.get("path_missing") or not workspace_path or not Path(workspace_path).exists():
            raise HTTPException(status_code=409, detail="workspace_path_missing")

        binding = runtime_status.get("binding")
        if binding:
            update_agent("chat", host=binding.get("host") or "127.0.0.1", port=int(binding.get("port") or 18765), model=binding.get("model"), opencode_agent=binding.get("opencode_agent") or "general")

        user_result = write_operation(session_id, "chat", role_session_id, "append_chat_message", {"content": content, "sender": "user"})
        if not user_result.get("ok"):
            raise HTTPException(status_code=500, detail=user_result.get("error", "write failed"))
        render_chat_stream_from_history()

        history_messages = get_chat_messages_data(limit=50)
        history_lines = []
        for msg in history_messages:
            sender = msg.get("sender", "")
            msg_content = msg.get("content", "")
            if sender == "user":
                history_lines.append(f"You: {msg_content}")
            elif sender == "chat":
                history_lines.append(f"Chat: {msg_content}")
        history_str = "\n".join(history_lines)
        if history_str:
            history_str = f"=== CONVERSATION HISTORY ===\n{history_str}\n=== CURRENT MESSAGE ===\n"
        else:
            history_str = "=== CURRENT MESSAGE ===\n"

        prompt = (
            "You are the Task Hounds Chat agent. You talk directly with the human "
            "about the currently active project session.\n\n"
            "Use the Task Hounds DB Skill when you need project context. Do not read "
            "the SQLite file directly. You may read project context and chat history. "
            "Only create a user directive when the human clearly asks you to turn the "
            "conversation into work for Manager/Worker.\n\n"
            f"Current project_session_id: {session_id}\n"
            f"Your role_session_id: {role_session_id}\n\n"
            f"Current workspace_path: {workspace_path}\n\n"
            "Reply conversationally and concisely. If you create or suggest a directive, "
            "tell the human exactly what you did.\n\n"
            f"Human message:\n{content}"
        )
        prompt = history_str + prompt
        reply = send_to_agent("chat", prompt, max_retries=1, cwd=workspace_path)

        bot_result = write_operation(session_id, "chat", role_session_id, "append_chat_message", {"content": reply, "sender": "chat"})
        if not bot_result.get("ok"):
            raise HTTPException(status_code=500, detail=bot_result.get("error", "write failed"))

        return {"ok": True, "reply": reply, "messages": get_chat_messages_data(limit=50)}
    except HTTPException:
        raise
    except Exception as e:
        try:
            from power_teams.db import update_agent
            update_agent("chat", state="error", last_error=str(e)[:500])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
#  SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/settings", tags=["settings"])
def get_settings():
    return read_settings()


@app.post("/api/settings", tags=["settings"])
async def save_settings(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="settings payload must be an object")
    current = read_settings()
    current.update({k: v for k, v in body.items() if v is not None})
    write_settings(current)
    return {"ok": True}


@app.put("/api/settings", tags=["settings"])
async def put_settings(request: Request):
    return await save_settings(request)


# ══════════════════════════════════════════════════════════════════════════════
#  FILES / USER INPUT
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/files/user_input", response_model=FileContent, tags=["files"])
def get_user_input():
    return {"content": read_active_runtime_file("user_input.txt")}


@app.put("/api/files/user_input", tags=["files"])
def put_user_input(body: UserInputContent):
    write_active_runtime_file("user_input.txt", body.content)
    return {"ok": True}


@app.get("/api/user-input/has-content", response_model=HasContentResponse, tags=["files"])
def user_input_has_content():
    return {"has_content": bool(read_active_runtime_file("user_input.txt").strip())}


@app.get("/api/directive/status", response_model=DirectiveStatusResponse, tags=["files"])
def directive_status():
    directive_content = read_active_runtime_file("user_input.txt")
    return {"has_directive": bool(directive_content.strip()), "directive_content": directive_content}


@app.get("/api/files/tasks", response_model=FileContent, tags=["files"])
def get_tasks():
    return {"content": read_runtime("agent_files/tasks.md")}


@app.get("/api/files/worker_report", response_model=FileContent, tags=["files"])
def get_worker_report():
    return {"content": read_runtime("agent_files/worker_report.md")}


@app.get("/api/files/manager_feedback", response_model=FileContent, tags=["files"])
def get_manager_feedback():
    return {"content": read_runtime("agent_files/manager_feedback.md")}


@app.get("/api/files/manager_msg_user", response_model=FileContent, tags=["files"])
def get_manager_msg_user():
    return {"content": read_runtime("agent_files/manager_msg_user.md")}


@app.get("/api/files/work_status", response_model=FileContent, tags=["files"])
def get_work_status():
    return {"content": read_runtime("agent_files/work_0001_status.txt")}


@app.get("/api/session_state", response_model=FileContent, tags=["files"])
def get_session_state():
    return {"content": read_runtime("sessions/session_state.json")}


# ══════════════════════════════════════════════════════════════════════════════
#  STREAM
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/stream/{agent_name}", response_model=StreamContent, tags=["stream"])
def get_stream(agent_name: str):
    stream_path = active_agent_stream_path(agent_name)
    return {"content": stream_path.read_text(encoding="utf-8") if stream_path.exists() else ""}


@app.put("/api/stream/{agent_name}", tags=["stream"])
async def put_stream(agent_name: str, request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    stream_file = active_agent_stream_path(agent_name)
    stream_file.parent.mkdir(parents=True, exist_ok=True)
    stream_file.write_text("", encoding="utf-8")
    legacy_stream = agent_stream_path(agent_name)
    if legacy_stream != stream_file:
        legacy_stream.write_text("", encoding="utf-8")
    return {"ok": True}


@app.post("/api/stream/{agent_name}/clear", tags=["stream"])
def clear_stream(agent_name: str):
    stream_file = active_agent_stream_path(agent_name)
    stream_file.parent.mkdir(parents=True, exist_ok=True)
    stream_file.write_text("", encoding="utf-8")
    legacy_stream = agent_stream_path(agent_name)
    legacy_stream.write_text("", encoding="utf-8")
    return {"ok": True}


@app.get("/api/timer/{agent_name}", response_model=StreamContent, tags=["stream"])
def get_timer(agent_name: str):
    timer_path = agent_timer_path(agent_name)
    return {"content": timer_path.read_text(encoding="utf-8") if timer_path.exists() else ""}


@app.get("/api/debug/{agent_name}", response_model=StreamContent, tags=["stream"])
def get_debug(agent_name: str):
    return {"content": read_runtime(f"agent_files/{agent_name}_debug.jsonl")}


# ══════════════════════════════════════════════════════════════════════════════
#  HANDOFF
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/handoff", tags=["handoff"])
def get_handoff():
    return get_handoff_data() or {}


@app.get("/api/handoff/versions", tags=["handoff"])
def get_handoff_versions():
    return get_handoff_versions_data()


@app.put("/api/handoff", tags=["handoff"])
def update_handoff(body: HandoffUpdate):
    try:
        session_id = get_active_project_session_id()
        if session_id == "legacy":
            raise HTTPException(status_code=409, detail="handoff update requires an active project session")
        version = _db().upsert_handoff(
            updated_by="human",
            path=DB_PATH,
            session_id=session_id,
            **body.model_dump(exclude_none=True),
        )
        return {"ok": True, "version": version}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
#  WORKSPACES
# ══════════════════════════════════════════════════════════════════════════════

def _workspace_list_data():
    from power_teams.db import connect
    settings = read_settings()
    active_ws = settings.get("active_workspace_id") or settings.get("workspace_id")
    active_session = settings.get("active_project_session") or settings.get("project_session_id")
    with connect(DB_PATH) as db:
        rows = db.execute(
            """SELECT * FROM project_sessions
             WHERE workspace_id IS NOT NULL
             ORDER BY is_active DESC, updated_at DESC, created_at DESC"""
        ).fetchall()
    workspaces = {}
    for row in rows:
        ws_id = row["workspace_id"] or row["id"]
        if ws_id in workspaces:
            continue
        path = row["workspace_path"] or ""
        missing = bool(row["path_missing"]) or bool(path and not Path(path).exists()) or not bool(path)
        label = row["name"] or (Path(path).name if path else ws_id)
        workspaces[ws_id] = {
            "id": ws_id,
            "path": path,
            "label": label,
            "active": ws_id == active_ws or row["id"] == active_session or bool(row["is_active"]),
            "path_missing": missing,
        }
    return list(workspaces.values())


def _workspace_sessions_data(ws_id: str):
    from power_teams.db import connect
    with connect(DB_PATH) as db:
        rows = db.execute(
            """SELECT id, workspace_id, name, is_active, created_at
               FROM project_sessions
              WHERE workspace_id=?
              ORDER BY is_active DESC, updated_at DESC, created_at DESC""",
            (ws_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/workspaces", response_model=list[Workspace], tags=["workspaces"])
def get_workspaces():
    return _workspace_list_data()


@app.post("/api/workspaces", tags=["workspaces"])
def create_workspace(body: WorkspaceCreate):
    from power_teams.db import connect, get_workspace_fingerprint, normalize_workspace_path, is_workspace_path_duplicate
    raw_path = (body.path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="path is required")
    path_obj = Path(raw_path)
    if not path_obj.exists() or not path_obj.is_dir():
        raise HTTPException(status_code=400, detail="workspace_path_missing")
    norm_path = normalize_workspace_path(raw_path)
    if is_workspace_path_duplicate(norm_path, path=DB_PATH):
        raise HTTPException(status_code=409, detail="workspace_path_duplicate")
    ws_id = f"ws_{uuid.uuid4().hex[:8]}"
    session_id = f"ps_{uuid.uuid4().hex[:8]}"
    label = (body.label or body.name or Path(norm_path).name or ws_id).strip()
    fp = get_workspace_fingerprint(norm_path)
    with connect(DB_PATH) as db:
        db.execute("UPDATE project_sessions SET is_active=0")
        db.execute(
            """INSERT INTO project_sessions
                (id, workspace_id, name, workspace_path, path_missing, workspace_fingerprint, is_active)
               VALUES (?, ?, ?, ?, 0, ?, 1)""",
            (session_id, ws_id, label, norm_path, fp),
        )
        db.commit()
        settings = dict(read_settings())
    settings.update({
        "active_workspace_id": ws_id,
        "active_project_session": session_id,
        "workspace_id": ws_id,
        "project_session_id": session_id,
        "workspace_path": norm_path,
    })
    write_settings(settings)
    return {"id": ws_id, "path": norm_path, "label": label, "active": True, "sessions": _workspace_sessions_data(ws_id)}


@app.get("/api/workspaces/{ws_id}", tags=["workspaces"])
def get_workspace(ws_id: str):
    rows = _workspace_list_data()
    row = next((r for r in rows if r["id"] == ws_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return row


@app.post("/api/workspaces/{ws_id}", tags=["workspaces"])
@app.put("/api/workspaces/{ws_id}", tags=["workspaces"])
def update_workspace(ws_id: str, body: WorkspaceUpdate):
    from power_teams.db import connect
    label = (body.label or body.name or "").strip()
    if label:
        with connect(DB_PATH) as db:
            db.execute("UPDATE project_sessions SET name=?, updated_at=CURRENT_TIMESTAMP WHERE workspace_id=?", (label, ws_id))
            db.commit()
    return {"ok": True, "workspace_id": ws_id, "label": label}


@app.delete("/api/workspaces/{ws_id}", tags=["workspaces"])
def delete_workspace(ws_id: str):
    from power_teams.db import connect
    with connect(DB_PATH) as db:
        db.execute("DELETE FROM project_sessions WHERE workspace_id=?", (ws_id,))
        db.commit()
    settings = dict(read_settings())
    if settings.get("active_workspace_id") == ws_id or settings.get("workspace_id") == ws_id:
        for key in ("active_workspace_id", "active_project_session", "workspace_id", "project_session_id", "workspace_path"):
            settings.pop(key, None)
        write_settings(settings)
    return {"ok": True, "workspace_id": ws_id}


@app.post("/api/workspaces/{ws_id}/activate", tags=["workspaces"])
def activate_workspace(ws_id: str):
    from power_teams.db import connect
    with connect(DB_PATH) as db:
        row = db.execute("SELECT * FROM project_sessions WHERE workspace_id=? ORDER BY is_active DESC, updated_at DESC LIMIT 1", (ws_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="workspace not found")
        db.execute("UPDATE project_sessions SET is_active=0")
        db.execute("UPDATE project_sessions SET is_active=1 WHERE id=?", (row["id"],))
        db.commit()
    new_settings = dict(read_settings())
    new_settings["active_workspace_id"] = ws_id
    new_settings["active_project_session"] = row["id"]
    new_settings["workspace_id"] = ws_id
    new_settings["workspace_path"] = row["workspace_path"] or ""
    new_settings["project_session_id"] = row["id"]
    write_settings(new_settings)
    return {"ok": True, "workspace_id": ws_id, "sessions": _workspace_sessions_data(ws_id)}


@app.post("/api/workspaces/{ws_id}/relink", tags=["workspaces"])
async def relink_workspace(ws_id: str, request: Request):
    from power_teams.db import connect, normalize_workspace_path, get_workspace_fingerprint, is_workspace_path_duplicate, check_fingerprint_mismatch
    with connect(DB_PATH) as db:
        row = db.execute("SELECT * FROM project_sessions WHERE workspace_id=? ORDER BY is_active DESC, updated_at DESC LIMIT 1", (ws_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="workspace not found")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    new_path = payload.get("path", "").strip()
    if not new_path:
        raise HTTPException(status_code=400, detail="new path is required")
    wp = Path(new_path)
    if not wp.exists():
        raise HTTPException(status_code=400, detail="path does not exist")
    norm_path = normalize_workspace_path(new_path)
    with connect(DB_PATH) as db:
        duplicate = db.execute("SELECT 1 FROM project_sessions WHERE workspace_path=? AND workspace_id != ? LIMIT 1", (norm_path, ws_id)).fetchone()
    if duplicate:
        raise HTTPException(status_code=409, detail="another workspace already uses this path")
    old_fp = row["workspace_fingerprint"]
    new_fp = get_workspace_fingerprint(new_path)
    mismatch, mismatch_msg = False, ""
    if old_fp and new_fp:
        mismatch, mismatch_msg = check_fingerprint_mismatch(ws_id, new_path, path=DB_PATH)
        if mismatch:
            raise HTTPException(status_code=409, detail=f"fingerprint_mismatch: {mismatch_msg}")
    session_id = row["id"]
    with connect(DB_PATH) as db:
        db.execute("UPDATE project_sessions SET workspace_path=?, path_missing=0, workspace_fingerprint=?, updated_at=CURRENT_TIMESTAMP WHERE workspace_id=?", (norm_path, new_fp, ws_id))
        db.execute("UPDATE project_handoff SET project_folder_location=? WHERE session_id=?", (norm_path, session_id))
        db.commit()
    settings = dict(read_settings())
    if settings.get("active_workspace_id") == ws_id or settings.get("workspace_id") == ws_id:
        settings["workspace_path"] = norm_path
        write_settings(settings)
    return {"ok": True, "workspace_id": ws_id, "workspace_path": norm_path, "workspace_fingerprint": new_fp}


@app.post("/api/workspaces/{ws_id}/new-session", tags=["workspaces"])
def workspace_new_session(ws_id: str):
    from power_teams.db import get_workspace_fingerprint, connect
    with connect(DB_PATH) as db:
        row = db.execute("SELECT * FROM project_sessions WHERE workspace_id=? ORDER BY is_active DESC, updated_at DESC LIMIT 1", (ws_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="workspace not found")
    ws_path = row["workspace_path"]
    if not ws_path:
        raise HTTPException(status_code=400, detail="workspace has no path set, use relink first")
    fp = get_workspace_fingerprint(ws_path)
    session_id = f"ps_{uuid.uuid4().hex[:8]}"
    with connect(DB_PATH) as db:
        db.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path, path_missing, workspace_fingerprint, is_active) VALUES (?, ?, ?, ?, ?, ?, ?)", (session_id, ws_id, f"Session {session_id[:8]}", ws_path, 0, fp, 1))
        db.execute("UPDATE project_sessions SET is_active=0 WHERE id != ? AND workspace_id=?", (session_id, ws_id))
        db.commit()
    new_settings = dict(read_settings())
    new_settings["active_workspace_id"] = ws_id
    new_settings["active_project_session"] = session_id
    new_settings["workspace_id"] = ws_id
    new_settings["workspace_path"] = ws_path
    new_settings["project_session_id"] = session_id
    write_settings(new_settings)
    return {"ok": True, "session_id": session_id, "workspace_id": ws_id, "workspace_path": ws_path, "sessions": _workspace_sessions_data(ws_id)}


@app.get("/api/workspaces/{ws_id}/sessions", tags=["workspaces"])
def workspace_sessions(ws_id: str):
    return _workspace_sessions_data(ws_id)


# ══════════════════════════════════════════════════════════════════════════════
#  PLAN / TODO
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/plan", response_model=PlanData, tags=["plan"])
def get_plan():
    import sqlite3
    session_id = get_active_project_session_id()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT content, updated_by, updated_at FROM session_plan WHERE session_id=?", (session_id,)).fetchone()
            if row:
                return {"content": row[0], "updated_by": row[1], "updated_at": row[2], "session_id": session_id}
            return {"content": "", "updated_by": None, "updated_at": None, "session_id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/plan", tags=["plan"])
async def update_plan(request: Request):
    import sqlite3
    session_id = get_active_project_session_id()
    try:
        payload = await request.json()
        content = payload.get("content", "")
        updated_by = payload.get("updated_by", "user")
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """INSERT INTO session_plan (session_id, content, updated_by, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(session_id) DO UPDATE SET
                     content=excluded.content, updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP""",
                (session_id, content, updated_by),
            )
            conn.commit()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/todos", response_model=list[TodoItem], tags=["todos"])
def get_todos():
    import sqlite3
    session_id = get_active_project_session_id()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, session_id, parent_id, content, status, priority, position, owner, updated_at
                   FROM session_todos WHERE session_id=? ORDER BY position""",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/todos", tags=["todos"])
def create_todo(body: TodoCreate):
    import sqlite3
    session_id = get_active_project_session_id()
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    parent_id = body.parent_id
    status = body.status or "pending"
    try:
        with sqlite3.connect(DB_PATH) as conn:
            if parent_id:
                max_pos = conn.execute("SELECT COALESCE(MAX(position), -1) FROM session_todos WHERE session_id=? AND parent_id=?", (session_id, parent_id)).fetchone()[0]
            else:
                max_pos = conn.execute("SELECT COALESCE(MAX(position), -1) FROM session_todos WHERE session_id=? AND parent_id IS NULL", (session_id,)).fetchone()[0]
            new_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO session_todos
                     (id, session_id, parent_id, content, status, priority, position, owner, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (new_id, session_id, parent_id, content, status, body.priority or "medium", max_pos + 1, body.owner or "user"),
            )
            conn.commit()
        return {"ok": True, "id": new_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/todos/{todo_id}", tags=["todos"])
def update_todo(todo_id: str, body: TodoUpdate):
    import sqlite3
    try:
        with sqlite3.connect(DB_PATH) as conn:
            updates = []
            values = []
            if body.status is not None:
                updates.append("status=?")
                values.append(body.status)
            if body.content is not None:
                updates.append("content=?")
                values.append(body.content)
            if body.priority is not None:
                updates.append("priority=?")
                values.append(body.priority)
            if body.owner is not None:
                updates.append("owner=?")
                values.append(body.owner)
            if updates:
                updates.append("updated_at=CURRENT_TIMESTAMP")
                values.extend([todo_id, get_active_project_session_id()])
                query = f"UPDATE session_todos SET {', '.join(updates)} WHERE id=? AND session_id=?"
                conn.execute(query, values)
                conn.commit()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/todos/{todo_id}", tags=["todos"])
def delete_todo(todo_id: str):
    import sqlite3
    try:
        with sqlite3.connect(DB_PATH) as conn:
            session_id = get_active_project_session_id()
            conn.execute(
                "DELETE FROM session_todos WHERE session_id=? AND (id=? OR parent_id=?)",
                (session_id, todo_id, todo_id),
            )
            conn.commit()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
#  RUNTIME
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/runtime/status", tags=["runtime"])
def runtime_status():
    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        return mgr.get_runtime_health()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/runtime/active-work", response_model=ActiveWorkResponse, tags=["runtime"])
def runtime_active_work():
    try:
        active, reason = _db().has_active_work(session_id=get_active_project_session_id(), path=DB_PATH)
        return {"active_work": active, "reason": reason}
    except Exception as e:
        return {"active_work": False, "reason": str(e)}


@app.post("/api/runtime/stop-all", tags=["runtime"])
def runtime_stop_all():
    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        raw_results = mgr.stop_all_managed()
        results = []
        for r in raw_results:
            result_obj = r.get("result", {})
            results.append({"server_id": str(r.get("instance_id", "")), "ok": bool(result_obj.get("ok", False)), "error": result_obj.get("error")})
        return {"ok": True, "results": results}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/runtime/checkpoint", tags=["runtime"])
async def runtime_checkpoint(request: Request):
    try:
        payload = await request.json()
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        result = mgr.create_runtime_checkpoint(
            project_session_id=payload.get("project_session_id"),
            workspace_id=payload.get("workspace_id"),
            reason=payload.get("reason", "manual"),
            notes=payload.get("notes"),
        )
        return {"ok": True, **result}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/runtime/checkpoints", tags=["runtime"])
def runtime_checkpoints():
    try:
        checkpoints = _db().list_checkpoints(path=DB_PATH)
        return {"checkpoints": [dict(c) for c in checkpoints]}
    except Exception as e:
        return {"checkpoints": [], "error": str(e)}


@app.post("/api/runtime/checkpoints/{cp_id}/resume", tags=["runtime"])
def runtime_checkpoint_resume(cp_id: str):
    try:
        if not cp_id.isdigit():
            raise HTTPException(status_code=400, detail="checkpoint id must be numeric")
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        row = _db().get_checkpoint_by_id(int(cp_id), path=DB_PATH)
        restore_result = None
        if row:
            restore_result = OpenCodeLifecycleManager(db_path=DB_PATH).restore_checkpoint_to_registry(int(cp_id))
        return {"checkpoint": dict(row) if row else None, "restore": restore_result}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/runtime/checkpoints/{cp_id}/archive", tags=["runtime"])
def runtime_checkpoint_archive(cp_id: str):
    try:
        _db().archive_checkpoint(int(cp_id), path=DB_PATH)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/runtime/bindings/{role}", tags=["runtime"])
def runtime_binding(role: str):
    try:
        from power_teams.db import get_agent_binding, list_agent_bindings
        if role in ("manager", "worker", "reviewer", "chat"):
            row = get_agent_binding(role, path=DB_PATH)
            return {"binding": dict(row) if row else None}
        bindings = list_agent_bindings(path=DB_PATH)
        return {"bindings": [dict(b) for b in bindings]}
    except Exception as e:
        return {"error": str(e)}


@app.put("/api/runtime/bindings/{role}", tags=["runtime"])
@app.post("/api/runtime/bindings/{role}", tags=["runtime"])
async def runtime_binding_update(role: str, request: Request):
    try:
        if role not in ("manager", "worker", "reviewer", "chat"):
            raise HTTPException(status_code=404, detail="unknown role")
        payload = await request.json()
        from power_teams.db import get_agent_binding, update_agent, upsert_agent_binding
        host = payload.get("host", "127.0.0.1")
        port = int(payload.get("port", 18765))
        opencode_agent = payload.get("opencode_agent", "general")
        model = payload.get("model")
        upsert_agent_binding(
            role,
            server_instance_id=payload.get("server_instance_id"),
            host=host,
            port=port,
            opencode_agent=opencode_agent,
            model=model,
            binding_source=payload.get("binding_source", "user"),
            path=DB_PATH,
        )
        update_agent(role, host=host, port=port, opencode_agent=opencode_agent, model=model)
        row = get_agent_binding(role, path=DB_PATH)
        return {"binding": dict(row) if row else None}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/runtime/policy", tags=["runtime"])
def runtime_policy_get():
    from power_teams.db import get_runtime_policy
    try:
        policy = get_runtime_policy(path=DB_PATH)
        return {"policy": dict(policy) if policy else None}
    except Exception as e:
        return {"error": str(e)}


@app.put("/api/runtime/policy", tags=["runtime"])
async def runtime_policy(request: Request):
    from power_teams.db import get_runtime_policy, upsert_runtime_policy
    try:
        payload = await request.json()
        upsert_runtime_policy(
            name=payload.get("name", "default"),
            close_behavior=payload.get("close_behavior", "ask"),
            background_mode_enabled=bool(payload.get("background_mode_enabled", False)),
            on_backend_exit=payload.get("on_backend_exit", "stop_managed_opencode"),
            on_backend_crash_recovery=payload.get("on_backend_crash_recovery", "ask"),
            on_opencode_crash=payload.get("on_opencode_crash", "mark_error"),
            max_managed_opencode_servers=int(payload.get("max_managed_opencode_servers", 1)),
            default_topology=payload.get("default_topology", "shared"),
            default_shared_port=int(payload.get("default_shared_port", 18765)),
            allow_external_attach=bool(payload.get("allow_external_attach", True)),
            allow_unknown_attach=bool(payload.get("allow_unknown_attach", False)),
            path=DB_PATH,
        )
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


# -- OpenCode lifecycle --
@app.get("/api/runtime/opencode", tags=["runtime-opencode"])
def runtime_opencode_list():
    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        mgr.refresh_external_servers()
        servers = mgr.list_managed_servers() + mgr.list_external_servers() + mgr.list_unknown_servers()
        return {"servers": [s for s in servers if s.get("status") == "running"]}
    except Exception as e:
        return {"servers": [], "error": str(e)}


@app.get("/api/runtime/opencode/discover", tags=["runtime-opencode"])
def runtime_opencode_discover():
    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        results = mgr.discover_external()
        return {"ok": True, "discovered": results}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/runtime/opencode/start", tags=["runtime-opencode"])
async def runtime_opencode_start(request: Request):
    try:
        payload = await request.json()
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        result = mgr.start_managed_server(
            port=payload.get("port"),
            topology=payload.get("topology", "shared"),
            project_session_id=payload.get("project_session_id"),
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"ok": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/runtime/opencode/attach", tags=["runtime-opencode"])
async def runtime_opencode_attach(request: Request):
    try:
        payload = await request.json()
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        result = mgr.attach_external_server(payload.get("host", "127.0.0.1"), payload.get("port", 18765))
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"ok": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/runtime/opencode/test", tags=["runtime-opencode"])
async def runtime_opencode_test(request: Request):
    try:
        payload = await request.json()
        host = payload.get("host", "127.0.0.1")
        port = int(payload.get("port", 18765))
        is_running, message = is_opencode_http_reachable(host, port)
        return {"ok": True, "is_running": is_running, "message": message}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/runtime/opencode/ignore", tags=["runtime-opencode"])
async def runtime_opencode_ignore(request: Request):
    try:
        payload = await request.json()
        host = payload.get("host", "127.0.0.1")
        port = int(payload.get("port", 18765))
        return {"ok": True, "message": f"Server {host}:{port} ignored"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/runtime/opencode/{instance_id}/stop", tags=["runtime-opencode"])
def runtime_opencode_stop(instance_id: str):
    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        result = mgr.stop_managed_server(int(instance_id))
        return result
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/runtime/opencode/{instance_id}/restart", tags=["runtime-opencode"])
def runtime_opencode_restart(instance_id: str):
    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        result = mgr.restart_managed_server(int(instance_id))
        return result
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/runtime/opencode/{instance_id}/refresh", tags=["runtime-opencode"])
def runtime_opencode_refresh(instance_id: str):
    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        result = mgr.refresh_server_health(int(instance_id))
        return result
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
#  MISC
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/backends", response_model=BackendList, tags=["misc"])
def get_backends():
    try:
        from power_teams.runtime.backend_registry import list_backends
        return {"backends": list_backends()}
    except Exception as e:
        return {"backends": ["opencode"], "error": str(e)}


@app.get("/api/opencode/models", tags=["misc"])
def opencode_models():
    from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
    try:
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        servers = mgr.list_managed_servers() + mgr.list_external_servers()
        for srv in servers:
            if srv.get("status") != "running":
                continue
            host = srv.get("host", "127.0.0.1")
            port = srv.get("port")
            if not port:
                continue
            base = f"http://{host}:{port}"
            try:
                provider_payload = fetch_json(f"{base}/config/providers", timeout=3)
                models = []
                all_providers = provider_payload.get("providers") or []
                for provider in all_providers:
                    pid = provider.get("id")
                    provider_models = provider.get("models") or {}
                    for mid, mdata in provider_models.items():
                        status = (mdata if isinstance(mdata, dict) else {}).get("status", "active")
                        if status != "active":
                            continue
                        pname = provider.get("name") or pid
                        mname = (mdata if isinstance(mdata, dict) else {}).get("name") or mid
                        models.append({"id": f"{pid}/{mid}", "name": f"{pname} / {mname}"})
                if models:
                    return {"models": models}
            except Exception:
                continue
        return {"models": [], "note": "No reachable opencode servers with configured models"}
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.get("/api/opencode/agents", tags=["misc"])
def opencode_agents():
    """Return cached available agents (discovered at startup) or live-fetch from a running server."""
    try:
        from power_teams.runtime.opencode_lifecycle import (
            OpenCodeLifecycleManager,
            get_cached_available_agents,
            discover_available_agents,
        )
    except ImportError as e:
        return {"agents": [], "error": f"lifecycle import failed: {e}"}
    cached = get_cached_available_agents()
    if cached:
        return {"agents": cached, "source": "cache"}
    try:
        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
        servers = mgr.list_managed_servers() + mgr.list_external_servers()
        for srv in servers:
            if srv.get("status") != "running":
                continue
            host = srv.get("host", "127.0.0.1")
            port = srv.get("port")
            if not port:
                continue
            try:
                agents = discover_available_agents(host, int(port), timeout=4.0)
                if agents:
                    return {"agents": agents, "source": f"live http://{host}:{port}"}
            except Exception:
                continue
    except Exception as e:
        return {"agents": [], "error": str(e)}
    return {"agents": [], "note": "no reachable opencode server returned an agent list"}


@app.get("/api/opencode_options", tags=["misc"])
def opencode_options(host: str = Query(default="127.0.0.1"), port: int = Query(default=4096)):
    base = f"http://{host}:{port}"
    try:
        agents_payload = fetch_json(f"{base}/agent")
        provider_payload = fetch_json(f"{base}/provider")
    except Exception as exc:
        return {"error": str(exc), "agents": [], "models": []}
    agents = [{"value": item.get("name"), "label": item.get("name"), "mode": item.get("mode"), "model": item.get("model")} for item in agents_payload if item.get("mode") != "subagent" and item.get("name")]
    return {
        "agents": agents,
        "models": model_options(provider_payload),
        "approval_formats": [
            {"value": "ask", "label": "Ask interactively"},
            {"value": "once", "label": "Approve once"},
            {"value": "always", "label": "Approve always"},
            {"value": "reject", "label": "Reject"},
        ],
        "output_modes": [
            {"value": "answer", "label": "Final answer only"},
            {"value": "debug", "label": "Answer + tool summary"},
            {"value": "raw-stream", "label": "Raw stream"},
            {"value": "subagents", "label": "Show subagents"},
        ],
    }


@app.post("/api/opencode_send_stream", tags=["misc"])
async def opencode_send_stream(request: Request):
    import threading
    import queue as _queue
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    raw_port = str(payload.get("port", "")).strip()
    host = str(payload.get("host", "127.0.0.1")).strip() or "127.0.0.1"
    prompt = str(payload.get("prompt", "")).strip()
    extra_input = str(payload.get("extra_input", "")).strip()
    if extra_input:
        prompt = f"{prompt}\n\nExtra input:\n{extra_input}".strip()
    if not raw_port.isdigit() or not prompt:
        return StreamingResponse(iter([sse_event("error", {"message": "port and prompt are required"})]), media_type="text/event-stream")
    port = int(raw_port)
    options = payload.get("options") or {}
    try:
        timeout = max(5, min(600, int(options.get("timeout") or 120)))
    except (TypeError, ValueError):
        timeout = 120
    model = str(payload.get("model", "")).strip() or None
    agent = str(payload.get("agent", "")).strip() or "general"

    events = _queue.Queue()

    def worker():
        for _entry in reversed(PYTHONPATH_ENTRIES):
            if _entry not in sys.path:
                sys.path.insert(0, _entry)
        from power_teams.integrations.opencode_provider import OpencodeServeProvider
        provider = OpencodeServeProvider(host=host, port=port, model=model, agent=agent, timeout=timeout)
        session = None
        response_text = ""
        reasoning_text = ""
        tools = []
        subagents = []
        try:
            events.put(("status", {"message": f"Creating session on {host}:{port}..."}))
            session = provider.create_session(title=prompt[:80])
            actual_agent = resolve_opencode_agent(provider, agent)
            events.put(("agent", {"agent": actual_agent.replace("\u200b", ""), "model": model or "", "session_id": session["id"]}))

            def on_delta(part_type, chunk):
                events.put((part_type, {"text": repair_mojibake(str(chunk))}))

            raw_reply = provider.send_message(session["id"], prompt, model=model, agent=actual_agent, timeout=timeout, on_delta=on_delta)
            response_text = repair_mojibake(provider.extract_text(raw_reply).strip())
            reasoning_text = extract_reasoning(raw_reply)
            response_text, reasoning_text = split_answer_and_thinking(response_text, reasoning_text)
            tools, subagents = extract_tools(raw_reply)
        except Exception as exc:
            events.put(("error", {"message": f"Send failed: {exc}"}))
        finally:
            if session and not options.get("keep_session"):
                try:
                    provider.delete_session(session["id"])
                except Exception:
                    pass
            events.put(("done", {"response": response_text, "reasoning": reasoning_text, "tools": tools, "subagents": subagents, "output": f"Sent to {host}:{port}" if response_text else "Finished with no final response"}))
            events.put((None, None))

    threading.Thread(target=worker, daemon=True).start()

    def event_generator():
        while True:
            kind, data = events.get()
            if kind is None:
                break
            try:
                yield sse_event(kind, data)
            except (BrokenPipeError, ConnectionResetError):
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/port_checks", tags=["misc"])
async def port_checks(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    raw_port = str(payload.get("port", "")).strip()
    host = str(payload.get("host", "127.0.0.1")).strip() or "127.0.0.1"
    if not raw_port.isdigit():
        raise HTTPException(status_code=400, detail="port must be a number")
    port = int(raw_port)
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return {"ok": True, "is_running": 1, "output": f"{host}:{port} is running"}
    except OSError as exc:
        return {"ok": True, "is_running": 0, "output": f"{host}:{port} is not reachable ({exc})"}


FLOW_01_TABLES = (
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
)


def _flow_01_default_workspace() -> Path:
    return Path.home() / "Desktop" / "test" / "05"


def _flow_01_workspace_path(raw_path: str | None = None) -> Path:
    if raw_path and raw_path.strip():
        return Path(raw_path).expanduser()
    return _flow_01_default_workspace()


def _flow_01_directive_file(workspace_path: str | None = None, directive_file: str | None = None) -> Path:
    if directive_file and directive_file.strip():
        return Path(directive_file).expanduser()
    return _flow_01_workspace_path(workspace_path) / "human_directive.txt"


def _flow_01_default_directive() -> str:
    return (
        "Create a minimal observable flow_01 test artifact in this workspace. "
        "Start from the manager-selected task, keep the change small, and report "
        "the exact files changed and verification result."
    )


def _flow_01_read_directive(workspace_path: str | None = None, directive_file: str | None = None) -> str:
    path = _flow_01_directive_file(workspace_path, directive_file)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _flow_01_write_directive(
    directive: str | None = None,
    workspace_path: str | None = None,
    directive_file: str | None = None,
) -> dict:
    path = _flow_01_directive_file(workspace_path, directive_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (directive or _flow_01_default_directive()).strip()
    path.write_text(content + "\n", encoding="utf-8")
    return {
        "workspace_path": str(path.parent),
        "directive_file": str(path),
        "directive": content,
        "directive_chars": len(content),
    }


def _flow_01_insert_db_directive(directive: str, workspace_path: str | None = None) -> dict:
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage
    from power_teams.agentic_workflows.flow_01.interface import utc_now

    settings = read_settings()
    session_id = get_active_project_session_id()
    power_team_project_id = str(settings.get("active_workspace_id") or settings.get("workspace_id") or "power-teams")
    storage = FlowStorage()
    storage.init_db()
    now = utc_now()
    with sqlite3.connect(storage.db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO user_directives
              (power_team_project_id, session_id, directive, status, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (power_team_project_id, session_id, directive, now, now),
        )
        directive_id = int(cursor.lastrowid)
        conn.commit()
    return {
        "directive_id": directive_id,
        "fake_db": str(storage.db_path),
        "power_team_project_id": power_team_project_id,
        "session_id": session_id,
        "workspace_path": str(_flow_01_workspace_path(workspace_path)),
        "status": "pending",
    }


def _flow_01_counts() -> dict[str, int]:
    from power_teams.agentic_workflows.flow_01 import FlowStorage

    storage = FlowStorage()
    return {table: storage.count(table) for table in FLOW_01_TABLES}


def _flow_01_run_summary(row: Any) -> dict:
    output = json.loads(row["output_json"] or "{}")
    flow_input = json.loads(row["input_json"] or "{}")
    worker_payload = output.get("worker", {}).get("payload", {}) if isinstance(output.get("worker"), dict) else {}
    return {
        "id": row["id"],
        "power_team_project_id": row["power_team_project_id"],
        "project_session_id": row["project_session_id"],
        "loop_index": row["loop_index"],
        "status": row["status"],
        "created_at": row["created_at"],
        "workspace_path": flow_input.get("workspace_path", ""),
        "phase": output.get("phase", ""),
        "task": (output.get("suggestion") or {}).get("content", "") if isinstance(output.get("suggestion"), dict) else output.get("task", ""),
        "test_result": worker_payload.get("test_result", ""),
        "known_issues": worker_payload.get("known_issues", output.get("known_issues", [])),
        "files_changed": worker_payload.get("files_changed", []),
        "error": output.get("error"),
    }


def _flow_01_create_start_loop_run(flow_input: Any, *, use_real_worker: bool, emit_real_ui_signals: bool) -> dict:
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage
    from power_teams.agentic_workflows.flow_01.interface import utc_now

    storage = FlowStorage()
    storage.init_db()
    now = utc_now()
    identity = flow_input.identity()
    output_json = {
        "status": "running",
        "phase": "manager_running",
        "task": "",
        "todo_update_json": {"items": []},
        "use_real_worker": use_real_worker,
        "emit_real_ui_signals": emit_real_ui_signals,
    }
    with sqlite3.connect(storage.db_path) as conn:
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
                identity.workspace_path,
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
                    identity.workspace_path,
                    now,
                    now,
                ),
            )
        cursor = conn.execute(
            """
            INSERT INTO workflow_runs
              (power_team_project_id, project_session_id, loop_index, status,
               manager_opencode_session_id, worker_opencode_session_id,
               reviewer_opencode_session_id, server_instance_id,
               input_json, output_json, created_at)
            VALUES (?, ?, 1, 'running', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identity.power_team_project_id,
                identity.project_session_id,
                identity.manager_opencode_session_id,
                identity.worker_opencode_session_id,
                identity.reviewer_opencode_session_id,
                identity.server_instance_id,
                json.dumps(asdict(flow_input), ensure_ascii=False),
                json.dumps(output_json, ensure_ascii=False),
                now,
            ),
        )
        run_id = int(cursor.lastrowid)
        conn.commit()
    return {
        "run_id": run_id,
        "status": "running",
        "phase": "manager_running",
        "task": "",
        "todos": [],
        "fake_db": str(storage.db_path),
    }


def _flow_01_mark_run(run_id: int, *, status: str, output_json: dict) -> None:
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage

    storage = FlowStorage()
    storage.init_db()
    with sqlite3.connect(storage.db_path) as conn:
        conn.execute(
            "UPDATE workflow_runs SET status=?, output_json=? WHERE id=?",
            (status, json.dumps(output_json, ensure_ascii=False), run_id),
        )
        conn.commit()


def _flow_01_run_row(run_id: int) -> Any | None:
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage

    storage = FlowStorage()
    storage.init_db()
    with sqlite3.connect(storage.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()


def _flow_01_run_is_cancelling(run_id: int) -> bool:
    row = _flow_01_run_row(run_id)
    return bool(row and row["status"] in {"cancelling", "cancelled"})


def _flow_01_merge_run_output(run_id: int, *, status: str | None = None, updates: dict | None = None) -> dict:
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage

    storage = FlowStorage()
    storage.init_db()
    updates = updates or {}
    with sqlite3.connect(storage.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="flow_01 run not found")
        output_json = json.loads(row["output_json"] or "{}")
        output_json.update(updates)
        next_status = status or row["status"]
        conn.execute(
            "UPDATE workflow_runs SET status=?, output_json=? WHERE id=?",
            (next_status, json.dumps(output_json, ensure_ascii=False), run_id),
        )
        conn.commit()
    return output_json


def _flow_01_write_manager_state(run_id: int, flow_input: Any, state: Any) -> None:
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage
    from power_teams.agentic_workflows.flow_01.interface import utc_now

    storage = FlowStorage()
    storage.init_db()
    now = utc_now()
    identity = flow_input.identity()
    manager_message = (
        f"Loop {state.loop_input.loop_index}: I digested the directive, manager message, "
        f"todo list, worker report, and reviewer feedback. Next task: {state.suggestion_content}"
    )
    with sqlite3.connect(storage.db_path) as conn:
        conn.execute(
            """
            INSERT INTO manager_messages
              (power_team_project_id, content, session_id, queue_status, status_label, created_at)
            VALUES (?, ?, ?, 'manager_response', 'Manager response', ?)
            """,
            (identity.power_team_project_id, manager_message, identity.project_session_id, now),
        )
        conn.execute(
            """
            INSERT INTO session_plan (session_id, power_team_project_id, content, updated_by, updated_at)
            VALUES (?, ?, ?, 'manager', ?)
            ON CONFLICT(session_id) DO UPDATE SET
              power_team_project_id=excluded.power_team_project_id,
              content=excluded.content,
              updated_by=excluded.updated_by,
              updated_at=excluded.updated_at
            """,
            (identity.project_session_id, identity.power_team_project_id, state.plan, now),
        )
        for item in state.todo_list:
            conn.execute(
                """
                INSERT INTO session_todos
                  (id, power_team_project_id, session_id, parent_id, content, status, priority, position, owner, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  content=excluded.content,
                  status=excluded.status,
                  priority=excluded.priority,
                  position=excluded.position,
                  owner=excluded.owner,
                  updated_at=excluded.updated_at
                """,
                (
                    item["id"],
                    identity.power_team_project_id,
                    item["session_id"],
                    item.get("parent_id"),
                    item["content"],
                    item.get("status", "pending"),
                    item.get("priority", "medium"),
                    item.get("position", 0),
                    item.get("owner", "manager"),
                    now,
                    now,
                ),
            )
        conn.execute(
            """
            INSERT INTO suggestion_queue
              (power_team_project_id, content, status, verification, related_files, session_id, created_at, updated_at, released_at)
            VALUES (?, ?, 'released', ?, '[]', ?, ?, ?, ?)
            """,
            (
                identity.power_team_project_id,
                state.suggestion_content,
                state.suggestion_verification,
                identity.project_session_id,
                now,
                now,
                now,
            ),
        )
        conn.commit()
    _flow_01_merge_run_output(
        run_id,
        updates={
            "phase": "manager_completed",
            "task": state.suggestion_content,
            "todo_update_json": state.todo_update_json,
            "manager": {
                "input_digest": state.input_digest,
                "decision": state.decision,
                "plan": state.plan,
                "handoff_update": state.handoff_update,
            },
        },
    )


def _flow_01_finish_run_from_output(run_id: int, storage: Any, flow_input: Any, output: Any, *, phase: str) -> None:
    import sqlite3

    storage.write_output(flow_input, output)
    output_json = asdict(output)
    output_json["phase"] = phase
    with sqlite3.connect(storage.db_path) as conn:
        conn.row_factory = sqlite3.Row
        duplicate = conn.execute(
            """
            SELECT id FROM workflow_runs
             WHERE project_session_id=? AND id>?
             ORDER BY id DESC
             LIMIT 1
            """,
            (flow_input.project_session_id, run_id),
        ).fetchone()
        conn.execute(
            "UPDATE workflow_runs SET status=?, output_json=? WHERE id=?",
            (output.status, json.dumps(output_json, ensure_ascii=False), run_id),
        )
        if duplicate:
            conn.execute("DELETE FROM workflow_runs WHERE id=?", (int(duplicate["id"]),))
        conn.commit()


class _Flow01CancellationToken:
    def __init__(self, run_id: int) -> None:
        self.run_id = run_id

    def cancelled(self) -> bool:
        return _flow_01_run_is_cancelling(self.run_id)


def _flow_01_background_run(run_id: int, flow_input: Any, body: FlowRunRequest) -> None:
    from power_teams.agentic_workflows.flow_01 import (
        Flow01Workflow,
        FlowLoopInput,
        FlowState,
        FlowStorage,
        LocalFileWorkerExecutor,
        LocalManagerExecutor,
        LocalReviewerExecutor,
        OpenCodeManagerExecutor,
        OpenCodeReviewerExecutor,
        OpenCodeWorkerExecutor,
    )
    from power_teams.agentic_workflows.flow_01.adapters import FastApiServiceSignalAdapter, RecordingSignalAdapter

    storage = FlowStorage()
    adapter = FastApiServiceSignalAdapter() if body.emit_real_ui_signals else RecordingSignalAdapter()
    manager_executor = OpenCodeManagerExecutor() if body.use_real_worker else LocalManagerExecutor()
    worker_executor = OpenCodeWorkerExecutor() if body.use_real_worker else LocalFileWorkerExecutor()
    reviewer_executor = OpenCodeReviewerExecutor() if body.use_real_worker else LocalReviewerExecutor()
    workflow = Flow01Workflow(
        storage=storage,
        signal_adapter=adapter,
        workdir=Path(flow_input.workspace_path),
        manager_executor=manager_executor,
        worker_executor=worker_executor,
        reviewer_executor=reviewer_executor,
    )
    try:
        cancel_token = _Flow01CancellationToken(run_id)
        loop_input = FlowLoopInput(loop_index=1)
        adapter.loop_started(flow_input, loop_input.loop_index)
        state = FlowState(flow_input=flow_input, loop_input=loop_input, status="running")
        _flow_01_merge_run_output(run_id, updates={"phase": "manager_running"})
        state = workflow.manager_step(state, cancel_token=cancel_token)
        _flow_01_write_manager_state(run_id, flow_input, state)
        manager_output = workflow.to_output(state)
        adapter.manager_completed(manager_output)

        if _flow_01_run_is_cancelling(run_id):
            state.status = "cancelled"
            state.loop_input.worker_report = "Cancelled before Worker started."
            state.loop_input.test_result = "cancelled"
            state.loop_input.known_issues = ["Cancelled by user before Worker started."]
            state.loop_input.reviewer_feedback = "Cancelled before Reviewer started."
            output = workflow.to_output(state)
            _flow_01_finish_run_from_output(run_id, storage, flow_input, output, phase="cancelled_before_worker")
            adapter.loop_completed(output)
            return

        _flow_01_merge_run_output(run_id, updates={"phase": "worker_running"})
        state = workflow.worker_step(state, cancel_token=cancel_token)
        worker_output = workflow.to_output(state)
        adapter.worker_completed(worker_output)

        if _flow_01_run_is_cancelling(run_id):
            state.status = "cancelled"
            state.loop_input.test_result = state.loop_input.test_result or "cancelled"
            state.loop_input.known_issues = list(state.loop_input.known_issues or []) + ["Cancelled by user after Worker returned."]
            state.loop_input.reviewer_feedback = "Cancelled before Reviewer. Worker output was recorded."
            output = workflow.to_output(state)
            _flow_01_finish_run_from_output(run_id, storage, flow_input, output, phase="cancelled_after_worker")
            adapter.loop_completed(output)
            return

        _flow_01_merge_run_output(run_id, updates={"phase": "reviewer_running"})
        state = workflow.reviewer_step(state, cancel_token=cancel_token)
        output = workflow.to_output(state)
        _flow_01_finish_run_from_output(run_id, storage, flow_input, output, phase="completed")
        adapter.reviewer_completed(output)
        adapter.loop_completed(output)
    except Exception as exc:
        if _flow_01_run_is_cancelling(run_id):
            _flow_01_merge_run_output(
                run_id,
                status="cancelled",
                updates={
                    "status": "cancelled",
                    "phase": "cancelled_during_worker",
                    "error": str(exc),
                },
            )
            return
        _flow_01_mark_run(
            run_id,
            status="failed",
            output_json={
                "status": "failed",
                "phase": "failed",
                "error": str(exc),
                "input": asdict(flow_input),
            },
        )


def _flow_01_input_from_current_ui(body: FlowRunRequest):
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowInput

    session_id = get_active_project_session_id()
    settings = read_settings()
    workspace_path = _flow_01_workspace_path(body.workspace_path)
    directive = (
        body.directive
        or _flow_01_read_directive(str(workspace_path), body.directive_file)
        or read_active_runtime_file("user_input.txt")
        or ""
    ).strip()
    if not directive:
        directive = _flow_01_default_directive()

    todo_items: list[str] = []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT content FROM session_todos
             WHERE session_id=? AND parent_id IS NULL
             ORDER BY position
             LIMIT 10
            """,
            (session_id,),
        ).fetchall()
        todo_items = [str(row["content"]) for row in rows if str(row["content"]).strip()]

    if not todo_items:
        todo_items = [body.suggested_task or "Run a visible flow_01 UI signal test"]

    return FlowInput(
        power_team_project_id=str(settings.get("active_workspace_id") or settings.get("workspace_id") or "power-teams"),
        project_session_id=session_id,
        workspace_id=str(settings.get("active_workspace_id") or settings.get("workspace_id") or "workflow-ui"),
        workspace_path=str(workspace_path),
        manager_opencode_session_id=str(settings.get("manager_session_id") or "flow_01_manager_ui"),
        worker_opencode_session_id=str(settings.get("worker_session_id") or "flow_01_worker_ui"),
        reviewer_opencode_session_id=str(settings.get("reviewer_session_id") or "flow_01_reviewer_ui"),
        chat_opencode_session_id=str(settings.get("chat_session_id") or "flow_01_chat_ui"),
        server_instance_id=None,
        human_directive=directive,
        human_new_thought_and_suggestion=body.thought or "Triggered from FastAPI for real UI signal verification.",
        human_suggested_new_task_or_item=body.suggested_task or todo_items[0],
        manager_message=body.manager_message or "Flow 01 is emitting real dashboard signals while keeping workflow DB writes in its fake DB.",
        todo_items=todo_items,
    )


@app.get("/api/workflows/flow_01/status", tags=["workflows"])
def flow_01_status():
    from power_teams.agentic_workflows.flow_01 import DB_PATH as FLOW_01_DB_PATH

    directive_path = _flow_01_directive_file()
    directive = directive_path.read_text(encoding="utf-8") if directive_path.exists() else ""
    settings = read_settings()
    return {
        "ok": True,
        "flow": "flow_01",
        "fake_db": str(FLOW_01_DB_PATH),
        "fake_db_exists": FLOW_01_DB_PATH.exists(),
        "fake_db_counts": _flow_01_counts(),
        "default_workspace_path": str(_flow_01_default_workspace()),
        "active_ui_workspace_path": str(settings.get("workspace_path") or ""),
        "directive_file": str(directive_path),
        "directive_exists": directive_path.exists(),
        "directive_chars": len(directive.strip()),
        "run_endpoint": "/api/workflows/flow_01/run",
    }


@app.post("/api/workflows/flow_01/prepare", tags=["workflows"])
def prepare_flow_01(body: FlowPrepareRequest):
    try:
        from power_teams.agentic_workflows.flow_01 import FlowStorage

        workspace = _flow_01_workspace_path(body.workspace_path)
        workspace.mkdir(parents=True, exist_ok=True)
        directive_info = _flow_01_write_directive(body.directive, str(workspace))
        storage = FlowStorage()
        storage.init_db()
        db_directive = _flow_01_insert_db_directive(directive_info["directive"], str(workspace))
        return {
            "ok": True,
            "flow": "flow_01",
            "workspace_path": str(workspace),
            "directive_file": directive_info["directive_file"],
            "directive": directive_info["directive"],
            "db_directive": db_directive,
            "fake_db": str(storage.db_path),
            "fake_db_counts": _flow_01_counts(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/workflows/flow_01/directive", tags=["workflows"])
def get_flow_01_directive(workspace_path: str | None = Query(default=None), directive_file: str | None = Query(default=None)):
    path = _flow_01_directive_file(workspace_path, directive_file)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {
        "ok": True,
        "flow": "flow_01",
        "workspace_path": str(path.parent),
        "directive_file": str(path),
        "exists": path.exists(),
        "directive": content,
        "directive_chars": len(content.strip()),
    }


@app.put("/api/workflows/flow_01/directive", tags=["workflows"])
def put_flow_01_directive(body: FlowDirectiveRequest):
    if not body.directive.strip():
        raise HTTPException(status_code=400, detail="directive is required")
    directive_info = _flow_01_write_directive(body.directive, body.workspace_path, body.directive_file)
    db_directive = _flow_01_insert_db_directive(directive_info["directive"], body.workspace_path)
    return {"ok": True, "flow": "flow_01", **directive_info, "db_directive": db_directive, "fake_db_counts": _flow_01_counts()}


@app.get("/api/workflows/flow_01/runs", tags=["workflows"])
def list_flow_01_runs(limit: int = Query(default=20, ge=1, le=100)):
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage

    storage = FlowStorage()
    storage.init_db()
    with sqlite3.connect(storage.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM workflow_runs
             ORDER BY id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return {
        "ok": True,
        "flow": "flow_01",
        "fake_db": str(storage.db_path),
        "runs": [_flow_01_run_summary(row) for row in rows],
        "fake_db_counts": _flow_01_counts(),
    }


@app.get("/api/workflows/flow_01/runs/{run_id}", tags=["workflows"])
def get_flow_01_run(run_id: int):
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage

    storage = FlowStorage()
    storage.init_db()
    with sqlite3.connect(storage.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="flow_01 run not found")
    return {
        "ok": True,
        "flow": "flow_01",
        "fake_db": str(storage.db_path),
        "run": _flow_01_run_summary(row),
        "input": json.loads(row["input_json"]),
        "output": json.loads(row["output_json"]),
    }


@app.post("/api/workflows/flow_01/runs/{run_id}/cancel", tags=["workflows"])
def cancel_flow_01_run(run_id: int, body: FlowCancelRequest | None = None):
    body = body or FlowCancelRequest()
    row = _flow_01_run_row(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="flow_01 run not found")
    current_status = str(row["status"])
    if current_status in {"completed", "failed", "cancelled"}:
        return {
            "ok": True,
            "flow": "flow_01",
            "run_id": run_id,
            "status": current_status,
            "changed": False,
            "message": f"run is already {current_status}",
        }
    output = json.loads(row["output_json"] or "{}")
    phase = str(output.get("phase") or "")
    cancel_output = _flow_01_merge_run_output(
        run_id,
        status="cancelling",
        updates={
            "status": "cancelling",
            "phase": "cancelling",
            "cancel_requested_at": utc_now(),
            "cancel_reason": body.reason,
            "previous_status": current_status,
            "previous_phase": phase,
        },
    )
    worker_stop_result: dict[str, Any] | None = None
    role_to_stop = None
    if phase == "manager_running":
        role_to_stop = "manager"
    elif phase in {"manager_completed", "worker_running"}:
        role_to_stop = "worker"
    elif phase == "reviewer_running":
        role_to_stop = "reviewer"
    if body.stop_worker and role_to_stop:
        try:
            worker_stop_result = agent_kill(role_to_stop)
        except Exception as exc:
            worker_stop_result = {"ok": False, "error": str(exc)}
    _append_text(
        active_agent_stream_path("worker"),
        json.dumps({"t": "sys", "msg": f"flow_01 cancel requested for run #{run_id}"}, ensure_ascii=False) + "\n",
    )
    _append_text(_RUN_LOG, f"[{utc_now()}] flow_01 cancel requested run_id={run_id} reason={body.reason}\n")
    return {
        "ok": True,
        "flow": "flow_01",
        "run_id": run_id,
        "status": "cancelling",
        "changed": current_status != "cancelling",
        "phase": cancel_output.get("phase"),
        "previous_phase": phase,
        "stop_worker": body.stop_worker,
        "stopped_role": role_to_stop,
        "worker_stop_result": worker_stop_result,
        "poll": f"/api/workflows/flow_01/runs/{run_id}",
    }


@app.get("/api/workflows/flow_01/plan", tags=["workflows"])
def get_flow_01_plan():
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage

    storage = FlowStorage()
    storage.init_db()
    session_id = get_active_project_session_id()
    with sqlite3.connect(storage.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM session_plan WHERE session_id=? ORDER BY updated_at DESC LIMIT 1", (session_id,)).fetchone()
    if not row:
        return {"content": "", "updated_by": None, "updated_at": None, "session_id": None}
    return {
        "content": row["content"],
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
        "session_id": row["session_id"],
    }


@app.put("/api/workflows/flow_01/plan", tags=["workflows"])
async def put_flow_01_plan(request: Request):
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage
    from power_teams.agentic_workflows.flow_01.interface import utc_now

    payload = await request.json()
    content = str(payload.get("content", ""))
    updated_by = str(payload.get("updated_by") or "human")
    settings = read_settings()
    session_id = get_active_project_session_id()
    power_team_project_id = str(settings.get("active_workspace_id") or settings.get("workspace_id") or "power-teams")
    storage = FlowStorage()
    storage.init_db()
    now = utc_now()
    with sqlite3.connect(storage.db_path) as conn:
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
            (session_id, power_team_project_id, content, updated_by, now),
        )
        conn.commit()
    return {"ok": True}


@app.get("/api/workflows/flow_01/todos", tags=["workflows"])
def get_flow_01_todos():
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage

    storage = FlowStorage()
    storage.init_db()
    session_id = get_active_project_session_id()
    with sqlite3.connect(storage.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, session_id, parent_id, content, status, priority, position, owner, updated_at
              FROM session_todos
             WHERE session_id=?
             ORDER BY parent_id IS NOT NULL, position, updated_at DESC
            """,
            (session_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/workflows/flow_01/todos", tags=["workflows"])
async def post_flow_01_todo(request: Request):
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage
    from power_teams.agentic_workflows.flow_01.interface import utc_now

    payload = await request.json()
    content = str(payload.get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    settings = read_settings()
    session_id = get_active_project_session_id()
    power_team_project_id = str(settings.get("active_workspace_id") or settings.get("workspace_id") or "power-teams")
    todo_id = str(uuid.uuid4())
    now = utc_now()
    storage = FlowStorage()
    storage.init_db()
    with sqlite3.connect(storage.db_path) as conn:
        position = int(conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM session_todos WHERE session_id=?", (session_id,)).fetchone()[0])
        conn.execute(
            """
            INSERT INTO session_todos
              (id, power_team_project_id, session_id, parent_id, content, status, priority, position, owner, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
            """,
            (
                todo_id,
                power_team_project_id,
                session_id,
                payload.get("parent_id"),
                content,
                payload.get("priority") or "medium",
                position,
                payload.get("owner") or "manager",
                now,
                now,
            ),
        )
        conn.commit()
    return {"ok": True, "id": todo_id}


@app.patch("/api/workflows/flow_01/todos/{todo_id}", tags=["workflows"])
async def patch_flow_01_todo(todo_id: str, request: Request):
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage
    from power_teams.agentic_workflows.flow_01.interface import utc_now

    payload = await request.json()
    allowed = {"pending", "in_progress", "completed", "blocked"}
    status = payload.get("status")
    if status not in allowed:
        raise HTTPException(status_code=400, detail="unsupported todo status")
    storage = FlowStorage()
    storage.init_db()
    session_id = get_active_project_session_id()
    with sqlite3.connect(storage.db_path) as conn:
        conn.execute("UPDATE session_todos SET status=?, updated_at=? WHERE id=? AND session_id=?", (status, utc_now(), todo_id, session_id))
        conn.commit()
    return {"ok": True}


@app.delete("/api/workflows/flow_01/todos/{todo_id}", tags=["workflows"])
def delete_flow_01_todo(todo_id: str):
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage

    storage = FlowStorage()
    storage.init_db()
    session_id = get_active_project_session_id()
    with sqlite3.connect(storage.db_path) as conn:
        conn.execute("DELETE FROM session_todos WHERE session_id=? AND (id=? OR parent_id=?)", (session_id, todo_id, todo_id))
        conn.commit()
    return {"ok": True}


@app.get("/api/workflows/flow_01/suggestion", tags=["workflows"])
def get_flow_01_suggestion():
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage

    storage = FlowStorage()
    storage.init_db()
    session_id = get_active_project_session_id()
    with sqlite3.connect(storage.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM suggestion_queue WHERE session_id=? ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
    if not row:
        return {}
    return {
        "id": row["id"],
        "content": row["content"],
        "status": row["status"],
        "queue_status": row["status"],
        "status_label": row["status"],
        "verification": row["verification"],
        "related_files": json.loads(row["related_files"] or "[]"),
        "created_at": row["created_at"],
    }


@app.get("/api/workflows/flow_01/manager-messages", tags=["workflows"])
def get_flow_01_manager_messages(limit: int = Query(default=50, ge=1, le=200)):
    import sqlite3

    from power_teams.agentic_workflows.flow_01 import FlowStorage

    storage = FlowStorage()
    storage.init_db()
    session_id = get_active_project_session_id()
    with sqlite3.connect(storage.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, content, created_at, queue_status, status_label
              FROM manager_messages
             WHERE session_id=?
             ORDER BY id DESC
             LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "content": row["content"],
            "created_at": row["created_at"],
            "is_human": False,
            "queue_status": row["queue_status"],
            "status_label": row["status_label"],
        }
        for row in reversed(rows)
    ]


@app.post("/api/workflows/flow_01/run", tags=["workflows"])
def run_flow_01(body: FlowRunRequest):
    try:
        from power_teams.agentic_workflows.flow_01 import (
            Flow01Workflow,
            FlowStorage,
            LocalFileWorkerExecutor,
            LocalManagerExecutor,
            LocalReviewerExecutor,
            OpenCodeManagerExecutor,
            OpenCodeReviewerExecutor,
            OpenCodeWorkerExecutor,
        )
        from power_teams.agentic_workflows.flow_01.adapters import (
            FastApiServiceSignalAdapter,
            RecordingSignalAdapter,
        )

        flow_input = _flow_01_input_from_current_ui(body)
        adapter = FastApiServiceSignalAdapter() if body.emit_real_ui_signals else RecordingSignalAdapter()
        storage = FlowStorage()
        storage.init_db()
        manager_executor = OpenCodeManagerExecutor() if body.use_real_worker else LocalManagerExecutor()
        worker_executor = OpenCodeWorkerExecutor() if body.use_real_worker else LocalFileWorkerExecutor()
        reviewer_executor = OpenCodeReviewerExecutor() if body.use_real_worker else LocalReviewerExecutor()
        workflow = Flow01Workflow(
            storage=storage,
            signal_adapter=adapter,
            workdir=Path(flow_input.workspace_path or ROOT / "core" / "runtime" / "workflow_01_ui"),
            manager_executor=manager_executor,
            worker_executor=worker_executor,
            reviewer_executor=reviewer_executor,
        )
        outputs = workflow.run_loops(flow_input, loops=body.loops)
        count_tables = (
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
        )
        fake_db_counts = {table: workflow.storage.count(table) for table in count_tables}
        last_output = outputs[-1] if outputs else None
        return {
            "ok": True,
            "flow": "flow_01",
            "loops": len(outputs),
            "requested": {
                "workspace_path": flow_input.workspace_path,
                "use_real_worker": body.use_real_worker,
            },
            "session_id": flow_input.project_session_id,
            "power_team_project_id": flow_input.power_team_project_id,
            "fake_db": str(workflow.storage.db_path),
            "fake_db_counts": fake_db_counts,
            "emit_real_ui_signals": body.emit_real_ui_signals,
            "stream_agents": list(_DEFAULT_STREAM_AGENTS),
            "last_status": last_output.status if last_output else None,
            "last_task": last_output.suggestion.content if last_output else None,
            "last_output": {
                "manager_message": last_output.manager_message.content,
                "worker_report": last_output.worker.content,
                "reviewer_feedback": last_output.reviewer.content,
                "files_changed": last_output.worker.payload.get("files_changed", []),
                "test_result": last_output.worker.payload.get("test_result", ""),
                "known_issues": last_output.worker.payload.get("known_issues", []),
            } if last_output else None,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/workflows/flow_01/start-loop", tags=["workflows"])
def start_flow_01_loop(body: FlowRunRequest):
    try:
        from power_teams.agentic_workflows.flow_01 import FlowStorage

        flow_input = _flow_01_input_from_current_ui(body)
        storage = FlowStorage()
        storage.init_db()
        start = _flow_01_create_start_loop_run(
            flow_input,
            use_real_worker=body.use_real_worker,
            emit_real_ui_signals=body.emit_real_ui_signals,
        )
        thread = threading.Thread(
            target=_flow_01_background_run,
            args=(start["run_id"], flow_input, body),
            name=f"flow_01_run_{start['run_id']}",
            daemon=True,
        )
        thread.start()
        return {
            "ok": True,
            "flow": "flow_01",
            "mode": "background",
            "run_id": start["run_id"],
            "status": start["status"],
            "phase": start["phase"],
            "session_id": flow_input.project_session_id,
            "power_team_project_id": flow_input.power_team_project_id,
            "workspace_path": flow_input.workspace_path,
            "task": start["task"],
            "todos": start["todos"],
            "fake_db": start["fake_db"],
            "fake_db_counts": _flow_01_counts(),
            "poll": f"/api/workflows/flow_01/runs/{start['run_id']}",
            "use_real_worker": body.use_real_worker,
            "emit_real_ui_signals": body.emit_real_ui_signals,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/clear-all", tags=["misc"])
def clear_all():
    try:
        stop_mvp_cycle()
        stop_mvp_loop()
        for agent_name in _DEFAULT_STREAM_AGENTS:
            stream_file = active_agent_stream_path(agent_name)
            stream_file.parent.mkdir(parents=True, exist_ok=True)
            stream_file.write_text("", encoding="utf-8")
            legacy_stream = agent_stream_path(agent_name)
            if legacy_stream != stream_file:
                legacy_stream.write_text("", encoding="utf-8")
        for rel in ("worker_report.md", "manager_feedback.md", "manager_msg_user.md", "tasks.md", "work_0001_status.txt"):
            write_active_runtime_file(rel, "idle\n" if rel == "work_0001_status.txt" else "")
        session_id = get_active_project_session_id()
        import sqlite3
        with sqlite3.connect(DB_PATH) as conn:
            for table in ("suggestion_queue", "manager_messages", "session_plan", "session_todos", "project_handoff"):
                try:
                    conn.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
                except sqlite3.Error:
                    pass
            try:
                conn.execute("UPDATE agent_registry SET state='idle', task_complete=0, last_error=NULL")
            except sqlite3.Error:
                conn.execute("UPDATE agent_registry SET state='idle', task_complete=0")
            conn.commit()
        _append_text(_RUN_LOG, f"[{utc_now()}] clear-all session={session_id}\n")
        return {"ok": True, "session_id": session_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/pick-folder", tags=["misc"])
async def pick_folder(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    folder_path = payload.get("path", "").strip()
    if not folder_path:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            chosen = filedialog.askdirectory(title="Select project folder")
            root.destroy()
            folder_path = str(chosen or "").strip()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"folder picker unavailable: {exc}")
        if not folder_path:
            return {"ok": False, "cancelled": True}
    ws_path = Path(folder_path)
    if not ws_path.exists():
        raise HTTPException(status_code=400, detail="path does not exist")
    return {"ok": True, "path": folder_path}


@app.post("/api/debug-logs", tags=["misc"])
def debug_logs(body: DebugLogEntry):
    try:
        debug_log(f"[DEBUG-LAUNCH-PAD] [FRONTEND] [{body.source}] {body.msg}")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════════
#  STATIC FILES (UI) — must be LAST
# ══════════════════════════════════════════════════════════════════════════════

if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="static")


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Task Hounds FastAPI server")
    parser.add_argument("--port", type=int, default=8766, help="Port (default: 8766)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host (default: 0.0.0.0)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    print(f"Starting FastAPI server on {args.host}:{args.port}")
    print(f"Swagger UI:  http://localhost:{args.port}/docs")
    print(f"ReDoc:       http://localhost:{args.port}/redoc")

    uvicorn.run(
        "api.fastapi_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
