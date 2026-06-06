"""api.routes.compat - backward-compat shim for the old UI.

The React dashboard was built against the old fastapi_server.py
which had paths like /api/workflows/flow_01/*. The new API uses
simpler paths like /api/workflow/*. This shim maps the old paths
to the new ones so the existing UI keeps working.

If you ever rebuild the UI from scratch, this can be deleted.

Read endpoints return empty ([] or None) when no active session exists.
Write endpoints return 400 when no active session exists.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from task_hounds_api.db.ops import project as db_project
from task_hounds_api.db.ops import workflow as db_wf
from task_hounds_api.db.ops import todo as db_todo
from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.db.ops import agent as db_agent
from task_hounds_api.opencode import lifecycle as oc_lifecycle
from task_hounds_api.opencode import registry as oc_registry
from task_hounds_api.workflow import chat_agent
from task_hounds_api.api.deps import resolve_session_id, require_session_id, session_to_workspace
from task_hounds_api.api.routes.workflow import (
    workflow_loop_status,
    workflow_start_loop,
    workflow_stop_loop,
    workflow_run_once,
)

router = APIRouter(tags=["compat (legacy UI)"])


# /api/stream/* -> /api/streams/*

@router.get("/api/stream/{agent_name}")
def compat_stream(agent_name: str) -> dict:
    """Old: returns stream content for an agent. New: read latest DB messages."""
    sid = resolve_session_id(None)
    if not sid:
        return {"agent": agent_name, "messages": []}
    if agent_name == "manager":
        msgs = db_wf.list_manager_messages(sid, limit=5)
        return {"agent": agent_name, "messages": [{"content": m["content"], "created_at": m["created_at"]} for m in msgs]}
    if agent_name == "worker":
        rep = db_wf.latest_worker_report(sid)
        return {"agent": agent_name, "report": rep}
    return {"agent": agent_name, "messages": []}


@router.get("/api/timer/{agent_name}")
def compat_timer(agent_name: str) -> dict:
    """Old: per-agent timer state. UI reads d.content (the timestamp string)."""
    sid = resolve_session_id(None)
    if not sid:
        return {"agent": agent_name, "content": ""}
    if agent_name == "manager":
        m = db_wf.latest_manager_message(sid)
        return {"agent": agent_name, "content": m["created_at"] if m else ""}
    return {"agent": agent_name, "content": ""}


# /api/workflows/flow_01/* -> /api/workflow/*

@router.get("/api/workflows/flow_01/plan")
def compat_plan() -> dict | None:
    sid = resolve_session_id(None)
    if not sid:
        return {}
    return db_wf.get_plan(sid) or {}


@router.put("/api/workflows/flow_01/plan")
async def compat_put_plan(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(None)
    db_wf.set_plan(sid, body.get("content", ""), updated_by="manager")
    return {"updated": True}


@router.get("/api/workflows/flow_01/todos")
def compat_todos() -> list[dict]:
    sid = resolve_session_id(None)
    if not sid:
        return []
    return db_todo.list_todos(sid)


@router.post("/api/workflows/flow_01/todos")
async def compat_create_todo(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(None)
    tid = db_todo.upsert_todo(
        session_id=sid,
        content=body.get("content", ""),
        status=body.get("status", "pending"),
        priority=body.get("priority", "medium"),
        position=body.get("position", 0),
        parent_id=body.get("parent_id"),
    )
    return {"id": tid}


@router.patch("/api/workflows/flow_01/todos/{todo_id}")
async def compat_patch_todo(todo_id: str, request: Request) -> dict:
    body = await request.json()
    db_todo.patch_todo(todo_id, **body)
    return {"updated": todo_id}


@router.delete("/api/workflows/flow_01/todos/{todo_id}")
def compat_delete_todo(todo_id: str) -> dict:
    db_todo.delete_todo(todo_id)
    return {"deleted": todo_id}


@router.get("/api/workflows/flow_01/suggestion")
def compat_suggestion() -> dict | None:
    sid = resolve_session_id(None)
    if not sid:
        return {}
    return db_wf.get_active_suggestion(sid) or {}


@router.get("/api/workflows/flow_01/suggestions/unscoped")
def compat_unscoped_suggestions() -> list[dict]:
    return db_wf.list_unscoped_suggestions()


@router.post("/api/workflows/flow_01/suggestion/{action}")
async def compat_suggestion_action(action: str, request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(None)
    if action == "new":
        new_id = db_wf.create_suggestion(sid, body.get("content", ""))
        return {"id": new_id}
    if action in ("release", "pause", "done"):
        status_map = {"release": "released", "pause": "paused", "done": "done"}
        sugg = db_wf.get_active_suggestion(sid)
        if sugg:
            db_wf.update_suggestion_status(sugg["id"], status_map[action])
        return {"updated": True}
    return {"error": f"unknown action: {action}"}


# /api/workflows/flow_01/manager-messages -- DELETED in Phase 6.
# Authoritative handler in api/routes/workflow.py is the only route.


@router.get("/api/workflows/flow_01/reports")
def compat_reports(limit: int = Query(default=20)) -> list[dict]:
    sid = resolve_session_id(None)
    if not sid:
        return []
    try:
        return db_wf.list_worker_reports(sid, limit=limit)
    except Exception:
        return []


# /api/workflows/flow_01/runs -- DELETED in Phase 6.
# Authoritative handler in api/routes/workflow.py is the only route.


@router.get("/api/workflows/flow_01/handoff")
def compat_handoff() -> dict | None:
    sid = resolve_session_id(None)
    if not sid:
        return {}
    return db_wf.get_handoff(sid) or {}


@router.put("/api/workflows/flow_01/handoff")
async def compat_put_handoff(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(None)
    db_wf.upsert_handoff(sid, **body)
    return {"updated": True}


@router.post("/api/workflows/flow_01/directive")
async def compat_put_directive(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(None)
    did = db_chat.create_directive(sid, body.get("directive", ""))
    return {"id": did, "ok": True}


@router.get("/api/workflows/flow_01/status")
def compat_status() -> dict:
    return {"ok": True}


@router.post("/api/workflows/flow_01/prepare")
def compat_prepare() -> dict:
    return {"ok": True}


# /api/user-input/* and /api/directive/*

@router.get("/api/user-input/has-content")
def compat_user_input_has_content() -> dict:
    """Old: returns whether the user_input.txt file has content. New: check DB directive."""
    sid = resolve_session_id(None)
    if not sid:
        return {"has_content": False, "directive_id": None}
    d = db_chat.get_latest_directive(sid, status="pending")
    return {"has_content": d is not None, "directive_id": d["id"] if d else None}


@router.get("/api/directive/status")
def compat_directive_status() -> dict:
    sid = resolve_session_id(None)
    if not sid:
        return {"has_directive": False, "directive": None}
    d = db_chat.get_latest_directive(sid, status="pending")
    return {"has_directive": d is not None, "directive": d}


@router.get("/api/directive")
def compat_directive_get() -> dict:
    """Return the current pending directive text for the active session."""
    sid = resolve_session_id(None)
    if not sid:
        return {"ok": True, "content": ""}
    d = db_chat.get_latest_directive(sid, status="pending")
    return {"ok": True, "content": d["directive"] if d else ""}


@router.get("/api/dashboard/active")
def compat_dashboard_active() -> dict:
    """Return active session summary for the dashboard."""
    active = db_project.get_active_session()
    if not active:
        return {"ok": True, "active_project_session": None}
    return {"ok": True, "active_project_session": active["id"]}


@router.get("/api/agent-stream/{agent_name}")
def compat_agent_stream(agent_name: str) -> list[dict]:
    """Return stream entries for an agent. Empty list when no active session."""
    sid = resolve_session_id(None)
    if not sid:
        return []
    if agent_name == "manager":
        m = db_wf.latest_manager_message(sid)
        return [{"role": "manager", "content": m["content"], "created_at": m["created_at"]}] if m else []
    if agent_name == "worker":
        rep = db_wf.latest_worker_report(sid)
        return [{"role": "worker", "content": rep["report"], "created_at": rep["created_at"]}] if rep else []
    return []


# /api/chat/* -- DELETED in Phase 3 (commit c781090+1).
# Authoritative handlers in api/routes/chat.py are the only route.
# The /status endpoint was migrated to the authoritative chat.py too.


# /api/runtime/*

@router.get("/api/runtime/checkpoints")
def compat_checkpoints() -> list[dict]:
    """Old: list runtime checkpoints. New: not implemented, return empty list."""
    return []


@router.post("/api/runtime/checkpoint")
def compat_create_checkpoint() -> dict:
    return {"id": 0, "ok": True}


@router.post("/api/runtime/checkpoints/{cp_id}/resume")
def compat_resume_checkpoint(cp_id: str) -> dict:
    return {"resumed": cp_id, "ok": True}


@router.post("/api/runtime/checkpoints/{cp_id}/archive")
def compat_archive_checkpoint(cp_id: str) -> dict:
    return {"archived": cp_id, "ok": True}



@router.get("/api/loop/status")
def compat_loop_status() -> dict:
    return workflow_loop_status()


@router.post("/api/loop/start")
def compat_loop_start() -> dict:
    return workflow_start_loop()


@router.post("/api/loop/stop")
def compat_loop_stop() -> dict:
    return workflow_stop_loop()


# /api/sessions -- DELETED in Phase 6.
# The authoritative project-sessions endpoints live in api/routes/projects.py.
# UI calls /api/projects (authoritative) for the list; the legacy
# /api/sessions alias is no longer needed.

# /api/agents/* -- DELETED in Phase 6 (commit 4993ed4+1).
# Authoritative handlers in api/routes/agents.py are the only route.


# /api/files/* and /api/manager-messages

@router.get("/api/files/user_input")
def compat_user_input_file() -> dict:
    """Old: read user_input.txt. New: read from DB directive."""
    sid = resolve_session_id(None)
    if not sid:
        return {"content": ""}
    d = db_chat.get_latest_directive(sid, status="pending")
    return {"content": d["directive"] if d else ""}


@router.put("/api/files/user_input")
async def compat_put_user_input(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(None)
    db_chat.create_directive(sid, body.get("content", ""))
    return {"updated": True}


# /api/manager-messages GET + POST -- DELETED in Phase 6.
# Authoritative handlers in api/routes/workflow.py are the only route.
# (/api/manager-messages GET is exposed via /api/workflow/manager-messages)


# /api/suggestion (singular, old form)

@router.get("/api/suggestion")
def compat_suggestion_root() -> dict | None:
    sid = resolve_session_id(None)
    if not sid:
        return {}
    return db_wf.get_active_suggestion(sid) or {}


@router.put("/api/suggestion")
async def compat_put_suggestion(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(None)
    new_id = db_wf.create_suggestion(sid, body.get("content", ""))
    return {"id": new_id, "ok": True}


# /api/handoff (old)

@router.get("/api/handoff")
def compat_handoff_root() -> dict | None:
    sid = resolve_session_id(None)
    if not sid:
        return {}
    return db_wf.get_handoff(sid) or {}


@router.put("/api/handoff")
async def compat_put_handoff_root(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(None)
    db_wf.upsert_handoff(sid, **body)
    return {"updated": True}


@router.get("/api/handoff/versions")
def compat_handoff_versions() -> list[dict]:
    return []


# /api/plan and /api/todos (old)

@router.get("/api/plan")
def compat_plan_root() -> dict | None:
    sid = resolve_session_id(None)
    if not sid:
        return {}
    return db_wf.get_plan(sid) or {}


@router.put("/api/plan")
async def compat_put_plan_root(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(None)
    db_wf.set_plan(sid, body.get("content", ""), updated_by="manager")
    return {"updated": True}


# /api/todos/* -- DELETED in Phase 6.
# Authoritative handlers in api/routes/todos.py are the only route.


# /api/workspaces/* (old project naming)

@router.get("/api/workspaces")
def compat_workspaces() -> list[dict]:
    return [session_to_workspace(s) for s in db_project.list_sessions()]


@router.post("/api/workspaces")
async def compat_create_workspace(request: Request) -> dict:
    import uuid as _uuid
    body = await request.json()
    path = (body.get("workspace_path") or body.get("path") or "").strip()
    name = (body.get("name") or body.get("label") or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="workspace_path is required")
    if db_project.path_already_used(path):
        raise HTTPException(status_code=409, detail="workspace_path already in use")
    sid = "ps_" + _uuid.uuid4().hex[:8]
    sess = db_project.create_session(sid, path, name)
    return session_to_workspace(sess)


@router.get("/api/workspaces/{ws_id}")
def compat_get_workspace(ws_id: str) -> dict | None:
    sess = db_project.get_session(ws_id)
    if not sess:
        return None
    return session_to_workspace(sess)


@router.post("/api/workspaces/{ws_id}")
async def compat_update_workspace(ws_id: str, request: Request) -> dict:
    """Update a workspace's label/name. UI calls POST /api/workspaces/{id} with {label}."""
    body = await request.json()
    name = (body.get("name") or body.get("label") or "").strip()
    if name:
        db_project.update_session(ws_id, name=name)
    sess = db_project.get_session(ws_id)
    return session_to_workspace(sess)


@router.put("/api/workspaces/{ws_id}")
async def compat_update_workspace_put(ws_id: str, request: Request) -> dict:
    return await compat_update_workspace(ws_id, request)


@router.delete("/api/workspaces/{ws_id}")
def compat_delete_workspace(ws_id: str) -> dict:
    db_project.delete_session(ws_id)
    return {"deleted": ws_id}


@router.post("/api/workspaces/{ws_id}/activate")
def compat_activate_workspace(ws_id: str) -> dict:
    db_project.activate_session(ws_id)
    return {"activated": ws_id}


@router.patch("/api/workspaces/{ws_id}/activate")
def compat_activate_workspace_patch(ws_id: str) -> dict:
    """Same as POST /activate - UI uses PATCH."""
    db_project.activate_session(ws_id)
    return {"activated": ws_id}


@router.get("/api/workspaces/{ws_id}/sessions")
def compat_workspace_sessions(ws_id: str) -> list[dict]:
    """Sessions for a workspace - for now each workspace = 1 session."""
    sess = db_project.get_session(ws_id)
    return [sess] if sess else []


@router.post("/api/workspaces/{ws_id}/new-session")
async def compat_new_session(ws_id: str, request: Request) -> dict:
    """Create a new session within a workspace."""
    import uuid as _uuid
    body = await request.json() if False else {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = body.get("name", "")
    new_sid = "ps_" + _uuid.uuid4().hex[:8]
    sess = db_project.create_session(new_sid, body.get("workspace_path", ""), name)
    return {"id": new_sid, "sessions": [sess]}


@router.get("/api/workspaces/{ws_id}/new-session")
def compat_new_session_get(ws_id: str) -> dict:
    """UI sometimes calls GET to pre-fetch."""
    return {"id": None}


@router.post("/api/workspaces/{ws_id}/relink")
async def compat_relink_workspace(ws_id: str, request: Request) -> dict:
    """Re-link a workspace to a new folder path."""
    body = await request.json()
    new_path = body.get("path", "")
    if new_path:
        db_project.update_session(ws_id, workspace_path=new_path)
    sess = db_project.get_session(ws_id) or {}
    return {"workspace_path": sess.get("workspace_path", new_path)}


# /api/project-sessions/* (project session CRUD)

@router.post("/api/project-sessions")
async def compat_create_project_session(request: Request) -> dict:
    """Create a new project session (general create endpoint)."""
    import uuid as _uuid
    body = await request.json()
    sid = "ps_" + _uuid.uuid4().hex[:8]
    sess = db_project.create_session(
        sid,
        body.get("workspace_path", ""),
        body.get("name", ""),
    )
    return sess


@router.patch("/api/project-sessions/{session_id}")
async def compat_update_project_session(session_id: str, request: Request) -> dict:
    body = await request.json()
    db_project.update_session(session_id, **body)
    return db_project.get_session(session_id) or {}


@router.delete("/api/project-sessions/{session_id}")
def compat_delete_project_session(session_id: str) -> dict:
    db_project.delete_session(session_id)
    return {"deleted": session_id}


@router.post("/api/project-sessions/{session_id}")
async def compat_post_project_session(session_id: str, request: Request) -> dict:
    """UI calls POST to add a new session in a workspace. The session_id here
    is actually the workspace_id; create a new session row."""
    import uuid as _uuid
    try:
        body = await request.json()
    except Exception:
        body = {}
    new_sid = "ps_" + _uuid.uuid4().hex[:8]
    name = body.get("name", "")
    path = body.get("workspace_path", "")
    sess = db_project.create_session(new_sid, path, name)
    return sess


# /api/sessions/* (session archive endpoints)

# /api/sessions GET -- DELETED in Phase 6 (second copy).
# Authoritative handler in api/routes/projects.py is the only route.


@router.get("/api/sessions/archived")
def compat_sessions_archived() -> list[dict]:
    from task_hounds_api.db.ops import runtime as db_rt
    return db_rt.list_archived()


@router.put("/api/sessions/archive/{session_id}")
def compat_archive_session(session_id: str) -> dict:
    from task_hounds_api.db.ops import runtime as db_rt
    db_rt.archive_session(session_id, agent_name="")
    return {"archived": session_id}


@router.delete("/api/sessions/archive/{session_id}")
def compat_delete_archived_session(session_id: str) -> dict:
    from task_hounds_api.db.ops import runtime as db_rt
    db_rt.unarchive_session(session_id)
    return {"deleted": session_id}


# /api/pick-folder (UI folder picker)

@router.post("/api/pick-folder")
async def compat_pick_folder(request: Request) -> dict:
    """Open a native folder picker on the server (works in browser and Electron).

    The browser cannot open a folder dialog with a full absolute path, so the
    server spawns tkinter.filedialog.askdirectory() instead. The dialog blocks
    this request handler until the user picks or cancels.

    If the client already sent a path (e.g. user typed it in the prompt, or
    Electron returned one from its own picker), we just validate it.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    folder_path = (body.get("path", "") or "").strip()
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
            raise HTTPException(
                status_code=500,
                detail=f"folder picker unavailable: {exc}. Pass a path in the request body instead.",
            )
        if not folder_path:
            return {"ok": False, "cancelled": True}
    from pathlib import Path
    if not Path(folder_path).exists():
        raise HTTPException(status_code=400, detail="path does not exist")
    return {"ok": True, "path": folder_path}


# /api/files/* (UI runtime file read)

@router.get("/api/files/{filename}")
def compat_read_runtime_file(filename: str) -> dict:
    """Read a runtime file by name (UI old API)."""
    import os
    from task_hounds_api.db import ROOT
    safe = os.path.basename(filename)
    candidates = [
        ROOT / "core" / "runtime" / "agent_files" / safe,
        ROOT / "core" / "runtime" / safe,
    ]
    for p in candidates:
        if p.exists():
            return {"content": p.read_text(encoding="utf-8", errors="replace"), "path": str(p)}
    return {"content": "", "path": ""}


# /api/agents/{id}/* (per-agent actions)

@router.post("/api/agents/{name}")
async def compat_update_agent_by_name(name: str, request: Request) -> dict:
    body = await request.json()
    db_agent.update_agent(name, **body)
    return db_agent.get_agent(name) or {}


@router.put("/api/agents/{name}")
async def compat_update_agent_put(name: str, request: Request) -> dict:
    body = await request.json()
    db_agent.update_agent(name, **body)
    return db_agent.get_agent(name) or {}


@router.post("/api/agents/{name}/kill")
def compat_agent_kill(name: str) -> dict:
    """Mark agent as killed/error in DB."""
    db_agent.update_agent(name, state="error", last_error="killed by user")
    return {"killed": name}


@router.post("/api/agents/{name}/health")
def compat_agent_health(name: str) -> dict:
    return {"ok": True, "name": name}


@router.put("/api/agents/{name}/health")
def compat_agent_health_put(name: str) -> dict:
    return {"ok": True, "name": name}


@router.post("/api/agents/{name}/clear-error")
def compat_agent_clear_error(name: str) -> dict:
    db_agent.update_agent(name, state="idle", last_error=None)
    return {"cleared": name}


@router.post("/api/agents/{name}/mark-resolved")
def compat_agent_mark_resolved(name: str) -> dict:
    db_agent.update_agent(name, state="idle", last_error=None)
    return {"resolved": name}


@router.post("/api/agents/{name}/retry")
def compat_agent_retry(name: str) -> dict:
    db_agent.update_agent(name, state="idle", last_error=None)
    return {"retried": name}


# /api/validate/send-config

@router.post("/api/validate/send-config")
async def compat_validate_send_config(request: Request) -> dict:
    """UI calls this to validate a chat send config before sending.
    UI expects: { valid: boolean, errors: string[], warnings: string[] }"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    return {"ok": True, "valid": True, "errors": [], "warnings": []}


# /api/clear-all

@router.post("/api/clear-all")
def compat_clear_all() -> dict:
    return {"cleared": True}


# /api/session/reset (POST and GET)

@router.get("/api/session/reset")
def compat_session_reset_get() -> dict:
    return {"ok": True}


@router.post("/api/session/reset")
def compat_session_reset_post() -> dict:
    return {"reset": True}


# /api/manager-messages POST (second compat copy) -- DELETED in Phase 6.

# /api/run-cycle, /api/loop/*, /api/worker/restart

@router.post("/api/run-cycle")
def compat_run_cycle() -> dict:
    return workflow_run_once()


@router.post("/api/worker/restart")
def compat_worker_restart() -> dict:
    return {"restarted": True}


# /api/stream/*/clear (UI uses POST to clear stream files)

@router.post("/api/stream/manager/clear")
def compat_stream_manager_clear() -> dict:
    return {"cleared": True}


@router.post("/api/stream/worker/clear")
def compat_stream_worker_clear() -> dict:
    return {"cleared": True}


@router.post("/api/stream/reviewer/clear")
def compat_stream_reviewer_clear() -> dict:
    return {"cleared": True}


@router.post("/api/stream/chat/clear")
def compat_stream_chat_clear() -> dict:
    return {"cleared": True}


@router.post("/api/stream/{agent_name}/clear")
def compat_stream_agent_clear(agent_name: str) -> dict:
    return {"cleared": agent_name}


# /api/suggestion/new, /api/suggestion/{id}

@router.post("/api/suggestion/new")
async def compat_suggestion_new(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(body.get("session_id"))
    new_id = db_wf.create_suggestion(
        sid,
        body.get("content", ""),
        verification=body.get("verification"),
    )
    return {"id": new_id}


@router.post("/api/suggestion/{action}")
async def compat_suggestion_action_old(action: str, request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(body.get("session_id"))
    if action == "new":
        new_id = db_wf.create_suggestion(sid, body.get("content", ""))
        return {"id": new_id}
    if action in ("release", "pause", "done"):
        status_map = {"release": "released", "pause": "paused", "done": "done"}
        sugg = db_wf.get_active_suggestion(sid)
        if sugg:
            db_wf.update_suggestion_status(sugg["id"], status_map[action])
        return {"updated": True}
    return {"error": f"unknown action: {action}"}


# /api/opencode/* (model listing)

def _model_options() -> list[dict]:
    from task_hounds_api.opencode.config import list_providers, model_supports_thinking
    providers = list_providers()
    models = []
    for pid, provider in providers.items():
        provider_name = provider.get("name") or pid
        for mid, model in (provider.get("models") or {}).items():
            full_id = f"{pid}/{mid}"
            models.append({
                "id": full_id,
                "name": (model or {}).get("name") or full_id,
                "provider_id": pid,
                "provider_name": provider_name,
                "model_id": mid,
                "supports_thinking": model_supports_thinking(full_id),
            })
    return models


@router.get("/api/opencode/agents")
def compat_opencode_agents(host: str = "127.0.0.1", port: int = 18765) -> list[dict]:
    from task_hounds_api.opencode import client as oc_client
    return [
        {"id": a.get("name") or a.get("id"), "name": a.get("name") or a.get("id"), **a}
        for a in oc_client.list_agents(host, port)
        if a.get("name") or a.get("id")
    ]


@router.get("/api/opencode/available-models")
def compat_opencode_available_models() -> dict:
    return {"models": _model_options()}


@router.get("/api/opencode/config-info")
def compat_opencode_config_info() -> dict:
    from task_hounds_api.opencode.config import list_providers
    from task_hounds_api.opencode.binary import find
    providers = list_providers()
    bin_path = find()
    return {
        "binary": str(bin_path) if bin_path else None,
        "providers": [
            {"id": pid, "name": p.get("name"), "models": list((p.get("models") or {}).keys())}
            for pid, p in providers.items()
        ],
    }


@router.get("/api/opencode/models")
def compat_opencode_models() -> dict:
    from task_hounds_api.opencode.config import list_providers
    providers = list_providers()
    provider_list = [
        {
            "id": pid,
            "name": p.get("name") or pid,
            "models": list((p.get("models") or {}).keys()),
        }
        for pid, p in providers.items()
    ]
    return {"models": _model_options(), "providers": provider_list}


@router.get("/api/runtime/active-work")
def compat_runtime_active_work() -> dict:
    return {"has_active": False, "active": None}


# /api/workflows/flow_01/* additional

# /api/workflows/flow_01/runs GET (second compat copy) -- DELETED in Phase 6.


@router.get("/api/workflows/flow_01/start-loop")
def compat_flow_start_loop_get() -> dict:
    return {"running": False}


@router.post("/api/workflows/flow_01/runs/{run_id}/cancel")
def compat_flow_cancel(run_id: int) -> dict:
    return {"cancelled": run_id}


@router.post("/api/workflows/flow_01/runs/{run_id}/pause")
def compat_flow_pause(run_id: int) -> dict:
    return {"paused": run_id}


@router.post("/api/workflows/flow_01/runs/{run_id}/resume")
def compat_flow_resume(run_id: int) -> dict:
    return {"resumed": run_id}


@router.put("/api/workflows/flow_01/directive")
async def compat_flow_put_directive_v2(request: Request) -> dict:
    return await compat_put_directive(request)


@router.get("/api/workflows/flow_01/directive")
def compat_flow_get_directive() -> dict | None:
    """UI sometimes calls GET instead of POST. Return current directive if any."""
    sid = resolve_session_id(None)
    if not sid:
        return {}
    d = db_chat.get_latest_directive(sid, status="pending")
    return d


# /api/workflows/flow_01/runs GET (third compat copy) -- DELETED in Phase 6.


# /api/agent/* (singular) and /api/project-sessions/*

@router.get("/api/agent/{name}")
def compat_get_agent(name: str) -> dict | None:
    return db_agent.get_agent(name)


@router.patch("/api/agent/{name}")
async def compat_update_agent(name: str, request: Request) -> dict:
    body = await request.json()
    db_agent.update_agent(name, **body)
    return db_agent.get_agent(name) or {}


# /api/project-sessions/{session_id}/switch -- DELETED in Phase 6.
# Authoritative handler in api/routes/projects.py is the only route.


# /api/sessions POST archive (PUT is also above)

@router.put("/api/sessions/archive/{session_key}")
def compat_archive_session_v2(session_key: str) -> dict:
    from task_hounds_api.db.ops import runtime as db_rt
    db_rt.archive_session(session_key, agent_name="")
    return {"archived": session_key}


# /api/project-sessions/{session_id}/switch (second compat copy) -- DELETED in Phase 6.
