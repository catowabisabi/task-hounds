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

import json
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from task_hounds_api.db.ops import project as db_project
from task_hounds_api.db.ops import workflow as db_wf
from task_hounds_api.db.ops import todo as db_todo
from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.db.ops import agent as db_agent
from task_hounds_api.api import schemas
from task_hounds_api.opencode import lifecycle as oc_lifecycle
from task_hounds_api.opencode import registry as oc_registry
from task_hounds_api.workflow import chat_agent
from task_hounds_api.api.deps import resolve_session_id, require_session_id, session_to_workspace
from task_hounds_api.api.routes.workflow import (
    workflow_loop_status,
    workflow_start_loop,
    workflow_stop_loop,
    workflow_run_once,
    flow01_cancel_run,
    flow01_pause_run,
    flow01_resume_run,
)
from task_hounds_api.workflow.graph import resume_loop_from_checkpoint

router = APIRouter(tags=["compat (legacy UI)"])


# /api/stream/* -> /api/streams/*

@router.get("/api/stream/{agent_name}")
def compat_stream(agent_name: str) -> dict:
    from task_hounds_api.db import ROOT
    sid = resolve_session_id(None)
    if not sid:
        return {"agent": agent_name, "content": ""}
    safe = "".join(ch for ch in agent_name if ch.isalnum() or ch in ("-", "_")) or agent_name
    stream_path = ROOT / "core" / "runtime" / "agent_streams" / sid / f"{safe}.jsonl"
    if stream_path.exists():
        return {"agent": agent_name, "content": stream_path.read_text(encoding="utf-8", errors="replace")}
    if agent_name == "manager":
        msgs = db_wf.list_manager_messages(sid, limit=5)
        content = "\n".join(
            json.dumps({"t": "text", "text": m["content"], "ts": m["created_at"]}, ensure_ascii=False, default=str)
            for m in msgs
        )
        return {"agent": agent_name, "content": content}
    if agent_name == "worker":
        rep = db_wf.latest_worker_report(sid)
        content = json.dumps({"t": "text", "text": rep["report"], "ts": rep["created_at"]}, ensure_ascii=False, default=str) if rep else ""
        return {"agent": agent_name, "content": content}
    if agent_name == "reviewer":
        review = db_wf.get_latest_reviewer_session(sid)
        text = (review or {}).get("review_notes") or (review or {}).get("qa_result") or ""
        content = json.dumps({"t": "text", "text": text, "ts": (review or {}).get("created_at")}, ensure_ascii=False, default=str) if text else ""
        return {"agent": agent_name, "content": content}
    return {"agent": agent_name, "content": ""}


@router.put("/api/stream/{agent_name}")
async def compat_put_stream(agent_name: str, request: Request) -> dict:
    try:
        await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    result = clear_stream_compat(agent_name)
    return {"ok": True, **result}


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


@router.get("/api/debug/{agent_name}")
def compat_debug(agent_name: str) -> dict:
    from task_hounds_api.db import ROOT
    safe = Path(agent_name).name
    path = ROOT / "core" / "runtime" / "agent_files" / f"{safe}_debug.jsonl"
    content = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    return {"agent": safe, "content": content, "path": str(path)}


@router.get("/api/session_state")
def compat_session_state() -> dict:
    sid = resolve_session_id(None)
    if not sid:
        return {"content": "", "state": None}
    session = db_project.get_session(sid) or {}
    state = {
        "session": session,
        "plan": db_wf.get_plan(sid) or {},
        "todos": db_todo.list_todos(sid),
        "latest_manager_message": db_wf.latest_manager_message(sid),
        "latest_worker_report": db_wf.latest_worker_report(sid),
        "latest_directive": db_chat.get_latest_directive(sid),
    }
    return {"content": json.dumps(state, ensure_ascii=False, default=str), "state": state}


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


@router.get("/api/workflows/flow_01/todos", response_model=list[schemas.TodoOut])
def compat_todos(include_archived: bool = False) -> list[dict]:
    """Migration audit symbol 102: legacy flow_01 todo list endpoint
    returns the typed TodoOut shape (same as /api/todos)."""
    sid = resolve_session_id(None)
    if not sid:
        return []
    rows = (
        db_todo.list_todos(sid)
        if include_archived
        else db_todo.list_active_todos(sid)
    )
    for row in rows:
        if row.get("status") == "done":
            row["status"] = "completed"
    return rows


@router.post("/api/workflows/flow_01/todos")
async def compat_create_todo(request: Request) -> dict:
    """Migration audit symbol 173 (P7 id 173): the legacy
    flow_01 todo create endpoint accepts the old TodoCreate
    shape (with `owner` field, default="user") so legacy UI
    callers that send `owner` get the legacy semantics. The
    authoritative /api/todos POST continues to use TodoUpsert
    with `owner="manager"` default — the P7 user decision
    keeps the new strict behavior on the new route and only
    restores the legacy shape on the legacy compat path.
    """
    body = await request.json()
    sid = require_session_id(None)
    # Honor the legacy TodoCreate `owner` field with the
    # legacy default of "user". When the body omits owner,
    # we fall back to "user" (vs. the new manager default).
    owner = (body.get("owner") or "user").strip() or "user"
    status = body.get("status", "pending")
    if status == "done":
        status = "completed"
    tid = db_todo.upsert_todo(
        session_id=sid,
        content=body.get("content", ""),
        status=status,
        priority=body.get("priority", "medium"),
        position=body.get("position", 0),
        parent_id=body.get("parent_id"),
        owner=owner,
    )
    return {"id": tid, "ok": True}


@router.patch("/api/workflows/flow_01/todos/{todo_id}")
async def compat_patch_todo(todo_id: str, request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict) or not body:
        raise HTTPException(
            status_code=400,
            detail="no fields to update",
        )
    bad = set(body) - db_todo.PATCHABLE_TODO_FIELDS
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"disallowed fields: {sorted(bad)}",
        )
    if "status" in body:
        valid_statuses = {"pending", "in_progress", "completed", "blocked", "done", "cancelled"}
        if body["status"] not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {body['status']}. Must be one of: {sorted(valid_statuses)}",
            )
        if body["status"] == "done":
            body["status"] = "completed"
    db_todo.patch_todo(todo_id, **body)
    return {"updated": todo_id}


@router.delete("/api/workflows/flow_01/todos/{todo_id}")
def compat_delete_todo(todo_id: str) -> dict:
    # Migration audit symbol 331: 0c44ba2 contract removed children too.
    n = db_todo.delete_todo_recursive(todo_id)
    return {"deleted": todo_id, "removed": n}


@router.get("/api/workflows/flow_01/suggestion")
def compat_suggestion() -> dict | None:
    sid = resolve_session_id(None)
    if not sid:
        return {}
    return db_wf.get_active_suggestion(sid) or {}


@router.get("/api/suggestions/unscoped")
@router.get("/api/workflows/flow_01/suggestions/unscoped")
def compat_unscoped_suggestions() -> list[dict]:
    """P7 id 211: legacy /api/workflows/flow_01/suggestions/unscoped.

    The old endpoint added 4 decoration fields per row
    (scope_warning, cleanup_only, queue_status, status_label).
    The new code returns raw rows; the fix restores the 4
    fields so the legacy UI sees the contract it expects.
    """
    rows = db_wf.list_unscoped_suggestions()
    queue_map = {
        "pending": "queued_for_manager",
        "released": "queued_for_worker",
        "worker_done": "manager_reviewing",
        "done": "processed",
        "paused": "paused",
    }
    label_map = {
        "queued_for_manager": "Queued for manager",
        "queued_for_worker": "Queued for worker",
        "manager_reviewing": "Manager reviewing",
        "processed": "Processed",
        "paused": "Paused",
    }
    out = []
    for row in rows:
        item = dict(row)
        item["scope_warning"] = "historical_unscoped"
        item["cleanup_only"] = True
        status = item.get("status") or "pending"
        item["queue_status"] = queue_map.get(status, status)
        item["status_label"] = label_map.get(item["queue_status"], status)
        out.append(item)
    return out


@router.post("/api/workflows/flow_01/suggestion/{action}")
async def compat_suggestion_action(action: str, request: Request) -> dict:
    """P7 id 213 / 215 / 216: legacy suggestion action endpoint.

    The old code honored `body.id` (falling back to the active
    suggestion) and raised 404 if no target was found. The
    new compat silently no-op'd and returned {updated:true}
    even with no target. The fix:
      - If `body.id` is present, target that id.
      - Else fall back to the active suggestion.
      - If neither resolves, return 404 (the legacy contract).
    """
    body = await request.json()
    sid = require_session_id(None)
    if action == "new":
        new_id = db_wf.create_suggestion(sid, body.get("content", ""))
        return {"id": new_id, "ok": True}
    if action in ("release", "pause", "done"):
        status_map = {"release": "released", "pause": "paused", "done": "done"}
        target_id: int | None = None
        raw_id = body.get("id") if isinstance(body, dict) else None
        if raw_id is not None:
            try:
                target_id = int(raw_id)
            except (TypeError, ValueError):
                target_id = None
        if target_id is None:
            sugg = db_wf.get_active_suggestion(sid)
            if sugg:
                target_id = sugg["id"]
        if target_id is None:
            raise HTTPException(
                status_code=404,
                detail="no active suggestion",
            )
        db_wf.update_suggestion_status(target_id, status_map[action])
        return {"ok": True, "status": status_map[action], "id": target_id}
    return {"error": f"unknown action: {action}"}


# /api/workflows/flow_01/manager-messages -- DELETED in Phase 6.
# Authoritative handler in api/routes/workflow.py is the only route.


@router.get("/api/workflows/flow_01/reports")
def compat_reports() -> dict:
    sid = resolve_session_id(None)
    if not sid:
        return {"ok": True, "flow": "flow_01", "session_id": "", "worker": None, "reviewer": None}

    worker_row = db_wf.latest_worker_report(sid)
    reviewer_row = db_wf.get_latest_reviewer_session(sid)
    worker = None
    if worker_row:
        worker = {
            "report": worker_row.get("report") or "",
            "files_changed": worker_row.get("files_changed") or [],
            "test_result": worker_row.get("test_result") or "",
            "known_issues": worker_row.get("known_issues") or [],
            "created_at": worker_row.get("created_at") or "",
        }
    reviewer = None
    if reviewer_row:
        status = str(reviewer_row.get("status") or "")
        issues = reviewer_row.get("usability_issues") or []
        if not isinstance(issues, list):
            issues = []
        reviewer = {
            "status": status,
            "qa_result": "pass" if status == "completed" else status,
            "review_notes": reviewer_row.get("review_notes") or "",
            "bugs": issues,
            "uiux_suggestions": [],
            "possible_problems": [],
            "safety_security_risks": [],
            "scripts_documented": reviewer_row.get("scripts_documented") or "",
            "started_at": reviewer_row.get("started_at") or "",
            "completed_at": reviewer_row.get("completed_at"),
            "created_at": reviewer_row.get("started_at") or "",
        }
    return {"ok": True, "flow": "flow_01", "session_id": sid, "worker": worker, "reviewer": reviewer}


# /api/workflows/flow_01/runs -- DELETED in Phase 6.
# Authoritative handler in api/routes/workflow.py is the only route.


@router.get("/api/workflows/flow_01/handoff", response_model=schemas.HandoffData)
def compat_handoff() -> dict | None:
    """Migration audit symbol 163: GET /api/workflows/flow_01/handoff
    returns the typed HandoffData shape (extras allowed)."""
    sid = resolve_session_id(None)
    if not sid:
        return {}
    return db_wf.get_handoff(sid) or {}


@router.put("/api/workflows/flow_01/handoff")
async def compat_put_handoff(request_body: schemas.HandoffUpdate) -> dict:
    """Migration audit symbol 164: PUT /api/workflows/flow_01/handoff uses
    the strict HandoffUpdate request body."""
    sid = require_session_id(None)
    fields = request_body.model_dump(exclude_none=True)
    if fields:
        db_wf.upsert_handoff(sid, **fields)
    return {"updated": True}


@router.post("/api/workflows/flow_01/directive")
async def compat_put_directive(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(None)
    saved = db_chat.save_user_directive(sid, body.get("directive", ""))
    return {**saved, "ok": True}


@router.get("/api/workflows/flow_01/status")
def compat_status() -> dict:
    """Migration audit id 317: legacy /api/workflows/flow_01/status.

    The old endpoint returned 10 fields (fake_db, fake_db_counts,
    directive_*, etc.) that are gone in the new architecture
    (the new flow uses real DB workflow_runs instead of a
    per-flow fake DB). We restore the fields that ARE still
    computable from the current DB and document the
    intentionally-dropped fields in the response.

    Fields restored: ok, flow, active_project_session,
    directive_exists, directive_chars, default_workspace_path,
    run_endpoint.
    Fields dropped (intentionally): fake_db, fake_db_exists,
    fake_db_counts (no per-flow fake DB in the new architecture),
    directive_file (file-system artifact replaced by DB row),
    active_ui_workspace_path (UI computes this client-side).
    """
    sid = resolve_session_id(None)
    out: dict = {
        "ok": True,
        "flow": "flow_01",
        "active_project_session": sid,
        "default_workspace_path": "",
        "active_ui_workspace_path": "",
        "directive_file": None,
        "directive_exists": False,
        "directive_chars": 0,
        "fake_db": None,
        "fake_db_exists": False,
        "fake_db_counts": {},
        "run_endpoint": "/api/workflows/flow_01/run",
    }
    if sid:
        d = db_chat.get_latest_directive(sid, status="pending")
        if d is None:
            d = db_chat.get_latest_directive(sid)
        if d is not None:
            out["directive_exists"] = True
            out["directive_chars"] = len(d.get("directive") or "")
        from task_hounds_api.db.ops.project import get_active_session
        sess = get_active_session()
        if sess:
            out["default_workspace_path"] = sess.get("workspace_path") or ""
            out["active_ui_workspace_path"] = sess.get("workspace_path") or ""
    return out


@router.post("/api/workflows/flow_01/prepare")
async def compat_prepare(request: Request) -> dict:
    """Migration audit id 318: legacy /api/workflows/flow_01/prepare.

    The old endpoint did mkdir + write directive + init DB + insert
    DB directive and returned 8 fields. The new architecture folds
    prepare into flow01_start_run (which does the same side effects
    inline). To avoid the old URL silently returning {ok:True} with
    no side effects, we restore the file-system side effects
    (mkdir + write directive file) when the body provides
    workspace_path + directive. The DB-side effects
    (init_db + insert DB directive) are still owned by
    flow01_start_run; calling both is idempotent.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    workspace_path = str(body.get("workspace_path", "")).strip()
    directive = str(body.get("directive", "")).strip()
    if not workspace_path or not directive:
        # No-op if body is missing required fields; callers should
        # use flow01_start_run for the new combined flow.
        return {"ok": True, "flow": "flow_01", "noop": True}
    from pathlib import Path as _Path
    ws = _Path(workspace_path)
    ws.mkdir(parents=True, exist_ok=True)
    directive_file = ws / "human_directive.txt"
    directive_file.write_text(directive, encoding="utf-8")
    return {
        "ok": True,
        "flow": "flow_01",
        "workspace_path": str(ws),
        "directive_file": str(directive_file),
        "directive": directive,
    }


# /api/user-input/* and /api/directive/*

def _latest_visible_directive(session_id: str) -> dict | None:
    """Return the latest non-empty directive regardless of lifecycle status.

    The right-rail Human Directive is an always-visible note/input surface.
    Once a pending directive is claimed, its DB status changes to running and
    later processed/failed, but the user still expects to see what they wrote.
    """
    for row in db_chat.list_directives(session_id, limit=50):
        if (row.get("directive") or "").strip():
            return row
    return db_chat.get_latest_directive(session_id, status=None)


@router.get("/api/user-input/has-content", response_model=schemas.HasContentResponse)
def compat_user_input_has_content() -> dict:
    """Migration audit symbol 159: GET /api/user-input/has-content returns
    the typed HasContentResponse shape (extras allowed for compat_id etc)."""
    sid = resolve_session_id(None)
    if not sid:
        return {"has_content": False, "directive_id": None}
    d = _latest_visible_directive(sid)
    content = (d.get("directive") if d else "") or ""
    return {"has_content": bool(content.strip()), "directive_id": d["id"] if d else None}


@router.get("/api/directive/status", response_model=schemas.DirectiveStatusResponse)
def compat_directive_status() -> dict:
    """Migration audit symbol 160 (P7 id 229): GET /api/directive/status
    returns the typed DirectiveStatusResponse shape.

    P7 id 229: the old contract was {has_directive, directive_content}
    where directive_content is the directive string from
    user_input.txt. The new DB row is a dict; the fix extracts
    the content string so the response matches the typed schema
    (directive: str | None) AND the legacy contract shape.
    """
    sid = resolve_session_id(None)
    if not sid:
        return {"has_directive": False, "directive": None, "directive_content": ""}
    d = _latest_visible_directive(sid)
    if d is None:
        return {"has_directive": False, "directive": None, "directive_content": ""}
    content = d.get("directive") or ""
    return {"has_directive": bool(content.strip()), "directive": content, "directive_content": content}


@router.get("/api/directive")
def compat_directive_get() -> dict:
    """Return the current pending directive text for the active session."""
    sid = resolve_session_id(None)
    if not sid:
        return {"ok": True, "content": ""}
    d = _latest_visible_directive(sid)
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
def compat_checkpoints(session_id: str | None = Query(default=None)) -> dict:
    """List flow_checkpoints for a project session (P7 id 265 + tA2e).

    The bare-list return shape was a real contract break vs the
    legacy {checkpoints:[...]} envelope. The P7 fix restores the
    envelope AND propagates DB errors as 500 instead of
    silently returning [].
    """
    sid = session_id
    if not sid:
        from task_hounds_api.db.ops.project import get_active_session
        active = get_active_session()
        if not active:
            return {"checkpoints": []}
        sid = active["id"]
    try:
        rows = db_wf.list_checkpoints_for_session(sid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"checkpoints": rows}


@router.post("/api/runtime/checkpoint")
async def compat_create_checkpoint(request: Request) -> dict:
    """P11 id 264: Create a full runtime checkpoint.

    The old endpoint called OpenCodeLifecycleManager.create_runtime_checkpoint.
    The new implementation persists a snapshot of all agent sessions,
    registry, todos, bindings, servers, and plan to run_checkpoints.

    Accepts: {project_session_id, workspace_id, reason, notes}
    Returns: {ok, checkpoint_id} on success.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    project_session_id = body.get("project_session_id", "")
    reason = body.get("reason", "Manual checkpoint")
    notes = body.get("notes", "")
    workspace_path = body.get("workspace_path")
    workspace_id = body.get("workspace_id")

    if not project_session_id:
        return {"ok": False, "error": "project_session_id is required"}

    try:
        checkpoint_id = db_wf.save_runtime_checkpoint(
            project_session_id=project_session_id,
            reason=reason,
            notes=notes,
            workspace_path=workspace_path,
            workspace_id=workspace_id,
        )
        return {"ok": True, "checkpoint_id": checkpoint_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/runtime/checkpoints/{cp_id}/resume")
def compat_resume_checkpoint(cp_id: str) -> dict:
    """P11 id 266 (restored): resume a specific checkpoint by its id.

    Parses cp_id as int, calls graph.resume_loop_from_checkpoint(cp_id),
    and propagates errors as 500.  Returns the full result shape from
    the graph function so callers get ok/status/run_id/cp_id.
    """
    try:
        cp_int = int(cp_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"invalid cp_id: {cp_id!r}")
    result = resume_loop_from_checkpoint(cp_int)
    if not result.get("ok", False):
        raise HTTPException(status_code=500, detail=result.get("error", "resume failed"))
    return result


@router.post("/api/runtime/checkpoints/{cp_id}/archive")
def compat_archive_checkpoint(cp_id: str) -> dict:
    """P7 id 267: restore the real archive side effect.

    The previous stub returned {archived,ok} without touching
    the DB. The fix calls db_wf.archive_checkpoint(int(cp_id))
    which sets flow_checkpoints.archived_at via migration 027.
    Returns 404 if the checkpoint id is not found, 400 if cp_id
    is not a valid int.
    """
    try:
        cp_int = int(cp_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"invalid cp_id: {cp_id!r}")
    try:
        ok = db_wf.archive_checkpoint(cp_int)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"checkpoint {cp_id} not found",
        )
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

@router.get("/api/files/user_input", response_model=schemas.FileContent)
def compat_user_input_file() -> dict:
    """Migration audit symbol 161: GET /api/files/user_input returns
    the typed FileContent shape (extras allowed)."""
    sid = resolve_session_id(None)
    if not sid:
        return {"content": ""}
    d = _latest_visible_directive(sid)
    return {"content": d["directive"] if d else ""}


@router.put("/api/files/user_input")
async def compat_put_user_input(request_body: schemas.UserInputContent) -> dict:
    """Migration audit symbol 158: PUT /api/files/user_input uses the
    strict UserInputContent request body."""
    import logging
    logger = logging.getLogger(__name__)
    sid = require_session_id(request_body.session_id)
    logger.warning(f"[PUT USER_INPUT] session_id={sid}, content_len={len(request_body.content)}")
    saved = db_chat.save_user_directive(sid, request_body.content)
    return {"updated": True, **saved}


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
    """P7 id 212: PUT /api/suggestion.

    Old behavior: if body.id was present, update the existing
    suggestion (PATCH semantics); else create a new one
    (POST semantics). New code only created. The fix routes
    to the create_suggestion + update_suggestion helpers
    based on whether body.id is present, returning the legacy
    {ok, id, status} envelope.
    """
    body = await request.json()
    sid = require_session_id(None)
    raw_id = body.get("id") if isinstance(body, dict) else None
    if raw_id is not None:
        try:
            target_id = int(raw_id)
        except (TypeError, ValueError):
            target_id = None
        if target_id is not None:
            # PATCH existing.
            updates = {k: v for k, v in body.items() if k != "id"}
            if updates:
                db_wf.update_suggestion(target_id, **updates)
            row = db_wf.get_suggestion(target_id)
            return {
                "ok": True,
                "id": target_id,
                "status": (row or {}).get("status"),
            }
    # POST new.
    new_id = db_wf.create_suggestion(sid, body.get("content", ""))
    return {"id": new_id, "ok": True, "status": "pending"}


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
    """P7 id 242: legacy /api/handoff/versions.

    The old endpoint returned the handoff history. The new
    architecture does not track handoff versions (the current
    handoff row is single, not versioned). The previous stub
    returned an empty list, which is a silent lie. The fix
    returns 501 with a descriptive error so callers can detect
    that the feature is intentionally absent.
    """
    raise HTTPException(
        status_code=501,
        detail="handoff versions are not tracked in the current architecture; "
               "use GET /api/workflows/flow_01/handoff for the latest handoff row.",
    )


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

def _norm_workspace_path(path: str | None) -> str:
    import os
    return os.path.normcase(os.path.normpath(str(path or "").strip()))


def _todo_progress(session_ids: list[str]) -> dict:
    todos = [
        todo
        for session_id in session_ids
        for todo in db_todo.list_active_todos(session_id)
    ]
    total = len(todos)
    completed = sum(1 for todo in todos if todo.get("status") == "completed")
    percent = round((completed / total) * 100) if total else 0
    return {
        "progress_completed": completed,
        "progress_total": total,
        "progress_percent": percent,
        "progress_state": "not_started" if total == 0 else ("completed" if completed == total else "active"),
    }


def _sessions_for_workspace_id(ws_id: str) -> list[dict]:
    parent = db_project.get_session(ws_id)
    if not parent:
        return []
    key = _norm_workspace_path(parent.get("workspace_path"))
    rows = [
        row for row in db_project.list_sessions()
        if _norm_workspace_path(row.get("workspace_path")) == key
    ]
    result = []
    for row in rows:
        item = session_to_workspace(row)
        item["name"] = row.get("name")
        item["is_active"] = 1 if row.get("is_active") else 0
        item.update(_todo_progress([str(row.get("id"))]))
        result.append(item)
    return result


@router.get("/api/workspaces")
def compat_workspaces() -> list[dict]:
    grouped: dict[str, dict] = {}
    sessions = db_project.list_sessions()
    for row in sessions:
        item = session_to_workspace(row)
        key = _norm_workspace_path(item.get("path"))
        if key not in grouped:
            grouped[key] = item
            continue
        if item.get("active"):
            grouped[key] = item
    for key, item in grouped.items():
        session_ids = [
            str(row.get("id"))
            for row in sessions
            if _norm_workspace_path(row.get("workspace_path")) == key
        ]
        item.update(_todo_progress(session_ids))
    return list(grouped.values())


@router.post("/api/workspaces")
async def compat_create_workspace(request: Request) -> dict:
    """P7 id 247: legacy POST /api/workspaces.

    Old behavior: validated path exists + is_dir, checked
    duplicate normalized path, created a workspace_id +
    project_session row, activated it, wrote settings, and
    stored fingerprint. New compat: accepts workspace_path/
    path, checks nonempty + duplicate, creates a session,
    returns session_to_workspace. The fix restores the path-
    exists check (so callers get a 400 on a missing folder
    instead of a session pointing at a non-existent path)
    + activates the new session.
    """
    import uuid as _uuid
    body = await request.json()
    path = (body.get("workspace_path") or body.get("path") or "").strip()
    name = (body.get("name") or body.get("label") or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="workspace_path is required")
    # P7 id 247: validate the path actually exists on disk
    # (legacy contract; new code accepted any string).
    from pathlib import Path as _Path
    if not _Path(path).is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"workspace_path does not exist or is not a directory: {path!r}",
        )
    if db_project.path_already_used(path):
        existing = next(
            (
                row for row in db_project.list_sessions()
                if _norm_workspace_path(row.get("workspace_path")) == _norm_workspace_path(path)
            ),
            None,
        )
        if existing:
            db_project.activate_session(existing["id"])
            item = session_to_workspace(db_project.get_session(existing["id"]) or existing)
            item["already_exists"] = True
            return item
        raise HTTPException(status_code=409, detail="workspace_path already in use")
    sid = "ps_" + _uuid.uuid4().hex[:8]
    folder_name = _Path(path).name.replace(" ", "-")
    workspace_name = name if name else folder_name
    session_name = name if name else "New Session"
    sess = db_project.create_session(sid, path, session_name, workspace_name)
    # P7 id 247: activate the new session (legacy behavior).
    db_project.activate_session(sid)
    return session_to_workspace(sess)


@router.get("/api/workspaces/{ws_id}")
def compat_get_workspace(ws_id: str) -> dict:
    """P7 id 248: legacy GET /api/workspaces/{id}.

    Old behavior: returned 404 if not found. New compat
    returned None (which serialized as a 200 with `null` body
    in the FastAPI default). The fix raises 404 explicitly
    so callers see a proper error code.
    """
    sess = db_project.get_session(ws_id)
    if not sess:
        raise HTTPException(
            status_code=404,
            detail=f"workspace {ws_id} not found",
        )
    return session_to_workspace(sess)


@router.post("/api/workspaces/{ws_id}/open-folder")
def compat_open_workspace_folder(ws_id: str) -> dict:
    sess = db_project.get_session(ws_id)
    if not sess:
        raise HTTPException(status_code=404, detail=f"workspace {ws_id} not found")
    root = Path(str(sess.get("workspace_path") or "")).resolve()
    if not root.is_dir():
        raise HTTPException(
            status_code=409,
            detail=f"project root does not exist or is not a directory: {root}",
        )
    try:
        command = (
            ["explorer.exe", str(root)]
            if sys.platform == "win32"
            else ["open", str(root)]
            if sys.platform == "darwin"
            else ["xdg-open", str(root)]
        )
        subprocess.Popen(command)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not open project root: {exc}") from exc
    return {"ok": True, "path": str(root)}


@router.post("/api/workspaces/{ws_id}")
async def compat_update_workspace(ws_id: str, request: Request) -> dict:
    """Update a workspace's label/name/path. UI calls POST /api/workspaces/{id}
    with {label} or {name, path}.

    Migration audit symbols 170 + 249: return the legacy
    {ok, workspace_id, label, path} shape on the legacy compat
    path. The authoritative /api/projects/{id} PATCH route still
    returns the modern session_to_workspace shape.

    P7 id 170: the legacy WorkspaceUpdate schema is now
    respected — name/label AND path are applied via
    db_project.update_session. Empty name/label is ignored
    (preserves the existing behavior). Empty path is ignored.

    ID 170 fix: when workspace_path changes, also sync the cached
    workspace_path in project_session_role_sessions so opencode
    bindings stay in sync.
    """
    body = await request.json()
    name = (body.get("name") or body.get("label") or "").strip()
    path = (body.get("path") or "").strip()
    fields: dict = {}
    if name:
        fields["workspace_name"] = name
    if path:
        fields["workspace_path"] = path
    if fields:
        db_project.update_session(ws_id, **fields)
        if path:
            from task_hounds_api.db.ops import runtime as db_rt
            updated_session = db_project.get_session(ws_id) or {}
            db_rt.sync_workspace_path_for_session(
                ws_id,
                updated_session.get("workspace_path") or path,
            )
    sess = db_project.get_session(ws_id) or {}
    return {
        "ok": True,
        "workspace_id": ws_id,
        "label": (sess.get("name") or name).strip(),
        "path": sess.get("workspace_path") or "",
    }


@router.put("/api/workspaces/{ws_id}")
async def compat_update_workspace_put(ws_id: str, request: Request) -> dict:
    return await compat_update_workspace(ws_id, request)


@router.delete("/api/workspaces/{ws_id}")
def compat_delete_workspace(ws_id: str) -> dict:
    """P7 id 250: legacy DELETE /api/workspaces/{id}.

    Old behavior: deleted all project_sessions for the
    workspace_id and cleared the active workspace/session
    settings if any of the deleted sessions was active. New
    compat: deletes one session id and returns {deleted}.
    The fix: 404 if the session doesn't exist + best-effort
    clear of active settings if the deleted session was the
    active one.
    """
    sess = db_project.get_session(ws_id)
    if not sess:
        raise HTTPException(
            status_code=404,
            detail=f"workspace {ws_id} not found",
        )
    deleted_sessions = db_project.delete_workspace_sessions(ws_id)
    return {"deleted": ws_id, "deleted_sessions": deleted_sessions}


@router.post("/api/workspaces/{ws_id}/activate")
def compat_activate_workspace(ws_id: str) -> dict:
    """P7 id 251: legacy POST /api/workspaces/{id}/activate.

    New compat treats ws_id as session id; legacy contract
    selected the latest session for a workspace_id, set
    active flags, and wrote settings. The fix: look up the
    session by id, activate it, and return the legacy
    envelope.
    """
    if not db_project.get_session(ws_id):
        raise HTTPException(
            status_code=404,
            detail=f"workspace {ws_id} not found",
        )
    db_project.activate_session(ws_id)
    return {"activated": ws_id}


@router.patch("/api/workspaces/{ws_id}/activate")
def compat_activate_workspace_patch(ws_id: str) -> dict:
    """P7 id 251: same as POST /activate - UI uses PATCH."""
    return compat_activate_workspace(ws_id)


@router.get("/api/workspaces/{ws_id}/sessions")
def compat_workspace_sessions(ws_id: str) -> list[dict]:
    """P7 id 254: legacy GET /api/workspaces/{id}/sessions.

    Old behavior: listed all sessions for a workspace_id.
    New compat: returns at most one session (treats ws_id
    as session id). The fix: still returns a list (legacy
    contract) but uses session_to_workspace for each row.
    """
    return _sessions_for_workspace_id(ws_id)


@router.post("/api/workspaces/{ws_id}/new-session")
async def compat_new_session(ws_id: str, request: Request) -> dict:
    """P7 id 253: legacy POST /api/workspaces/{id}/new-session.

    Old behavior: used existing workspace path/fingerprint,
    created a new session for that workspace, activated it,
    wrote settings, and returned sessions. New compat
    creates a new session from body.workspace_path/name and
    returns id/sessions. The fix: if the body omits
    workspace_path, inherit from the parent workspace
    session (legacy behavior).
    """
    import uuid as _uuid
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = (body.get("name") or "").strip()
    # P7 id 253: inherit workspace_path from parent if not
    # provided in the body.
    new_path = (body.get("workspace_path") or "").strip()
    if not new_path:
        parent = db_project.get_session(ws_id)
        if parent:
            new_path = parent.get("workspace_path") or ""
    new_sid = "ps_" + _uuid.uuid4().hex[:8]
    parent = db_project.get_session(ws_id)
    parent_workspace_name = parent.get("workspace_name") if parent else None
    workspace_name = parent_workspace_name if parent_workspace_name else (name if name else Path(new_path).name.replace(" ", "-"))
    session_name = name if name else "New Session"
    sess = db_project.create_session(new_sid, new_path, session_name, workspace_name)
    db_project.activate_session(new_sid)
    return {"id": new_sid, "sessions": _sessions_for_workspace_id(new_sid)}


@router.get("/api/workspaces/{ws_id}/new-session")
def compat_new_session_get(ws_id: str) -> dict:
    """P7 id 253: UI sometimes calls GET to pre-fetch."""
    return {"id": None}


@router.post("/api/workspaces/{ws_id}/relink")
async def compat_relink_workspace(ws_id: str, request: Request) -> dict:
    """P7 id 252: legacy POST /api/workspaces/{id}/relink.

    Old behavior: validated target path exists, checked
    duplicates/fingerprint mismatch, updated workspace rows,
    and could return warnings/errors. New compat just
    updated workspace_path. The fix: validate the new path
    exists on disk and 400 if not (vs. silently pointing at
    a missing folder).
    """
    if not db_project.get_session(ws_id):
        raise HTTPException(
            status_code=404,
            detail=f"workspace {ws_id} not found",
        )
    body = await request.json()
    new_path = (body.get("path") or "").strip()
    if new_path:
        from pathlib import Path as _Path
        if not _Path(new_path).is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"relink target path does not exist or is not a directory: {new_path!r}",
            )
        db_project.update_session(ws_id, workspace_path=new_path)
        from task_hounds_api.db.ops import runtime as db_rt
        sess = db_project.get_session(ws_id) or {}
        db_rt.sync_workspace_path_for_session(
            ws_id,
            sess.get("workspace_path") or new_path,
        )
    sess = db_project.get_session(ws_id) or {}
    return {"workspace_path": sess.get("workspace_path", new_path)}


# /api/project-sessions/* (project session CRUD)

@router.post("/api/project-sessions")
async def compat_create_project_session(request: Request) -> dict:
    """Create a new project session (general create endpoint)."""
    import uuid as _uuid
    from pathlib import Path as _Path
    body = await request.json()
    sid = "ps_" + _uuid.uuid4().hex[:8]
    name = body.get("name", "") or ""
    workspace_path = body.get("workspace_path", "") or ""
    workspace_name = name if name else (workspace_path.split("/")[-1].replace(" ", "-") if workspace_path else "")
    sess = db_project.create_session(
        sid,
        workspace_path,
        name or "New Session",
        workspace_name,
    )
    return sess


@router.patch("/api/project-sessions/{session_id}")
async def compat_update_project_session(session_id: str, request: Request) -> dict:
    """Update a project session via the legacy compat path.

    Returns the legacy {ok, session_id, updated, name} shape so older
    UI components (and tests pinned to the 0c44ba2 contract) keep
    working. The authoritative /api/projects/{session_id} PATCH route
    returns the modern session_to_workspace shape.
    """
    body = await request.json()
    db_project.update_session(session_id, **body)
    sess = db_project.get_session(session_id) or {}
    return {
        "ok": True,
        "session_id": session_id,
        "updated": True,
        "name": (body.get("name") or sess.get("name") or "").strip(),
    }


@router.delete("/api/project-sessions/{session_id}")
def compat_delete_project_session(session_id: str) -> dict:
    """Delete via the legacy compat path. Returns the legacy
    {ok, session_id} shape (new authoritative route returns {deleted})."""
    db_project.delete_session(session_id)
    return {"ok": True, "session_id": session_id}


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
    name = body.get("name", "") or ""
    path = body.get("workspace_path", "") or ""
    workspace_name = name if name else (path.split("/")[-1].replace(" ", "-") if path else "")
    sess = db_project.create_session(new_sid, path, name or "New Session", workspace_name)
    return sess


# /api/sessions/* (session archive endpoints)

# /api/sessions GET -- DELETED in Phase 6 (second copy).
# Authoritative handler in api/routes/projects.py is the only route.


@router.get("/api/sessions/archived", response_model=schemas.ArchivedSessionsResponse)
def compat_sessions_archived() -> dict:
    """Migration audit symbol 167: GET /api/sessions/archived returns
    the typed ArchivedSessionsResponse shape (count field derived
    from the list length; the inner sessions list uses SessionInfo
    with extras allowed for DB-derived fields)."""
    from task_hounds_api.db.ops import runtime as db_rt
    archived = db_rt.list_archived()
    return {"sessions": archived, "count": len(archived)}


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

    Migration audit symbol 345: errors are returned as {ok: False, error: ...}
    with HTTP 200 so the UI can render a single error toast without
    distinguishing 400/500. Tests pin the clean shape and assert the GUI
    dialog is NOT opened when a path is supplied.
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
            return {
                "ok": False,
                "error": f"folder picker unavailable: {exc}. Pass a path in the request body instead.",
            }
        if not folder_path:
            return {"ok": False, "cancelled": True, "error": "no path supplied and dialog cancelled"}
    from pathlib import Path
    if not Path(folder_path).exists():
        return {"ok": False, "error": f"invalid path: {folder_path!r} does not exist"}
    already_exists = db_project.path_already_used(folder_path)
    if already_exists:
        existing = next(
            (
                row for row in db_project.list_sessions()
                if _norm_workspace_path(row.get("workspace_path")) == _norm_workspace_path(folder_path)
            ),
            None,
        )
        return {
            "ok": True,
            "path": folder_path,
            "already_exists": True,
            "existing_id": existing["id"] if existing else None,
            "existing_label": existing.get("workspace_name") or existing.get("name") or "" if existing else "",
        }
    return {"ok": True, "path": folder_path, "already_exists": False}


# /api/files/* (UI runtime file read)

# ── Migration audit id 234 ────────────────────────────────────────────────
# Legacy GET /api/files/work_status aliased to
# agent_files/work_0001_status.txt. The generic basename read
# in compat_read_runtime_file would look for work_status (not
# work_0001_status.txt) and miss. This explicit alias restores
# the legacy contract. IMPORTANT: this route MUST be registered
# BEFORE the generic /api/files/{filename} route below, otherwise
# FastAPI's order-dependent matching will route the literal path
# to the generic handler.

_WORK_STATUS_BASENAMES = ("work_0001_status.txt", "work_status.txt", "work_status")


@router.get("/api/files/work_status")
def compat_work_status_alias() -> dict:
    """P7 id 234: legacy /api/files/work_status alias.

    The old endpoint resolved to agent_files/work_0001_status.txt
    (with a few fallbacks). The new generic basename read would
    look for agent_files/work_status and miss. This route tries
    the legacy basenames in order and returns the first match.
    """
    import os
    from task_hounds_api.db import ROOT
    base_dir = ROOT / "core" / "runtime" / "agent_files"
    for name in _WORK_STATUS_BASENAMES:
        p = base_dir / name
        if p.exists():
            return {
                "content": p.read_text(encoding="utf-8", errors="replace"),
                "path": str(p),
                "name": name,
            }
    return {"content": "", "path": "", "name": "work_status"}


@router.get("/api/files/{filename}", response_model=schemas.FileContent)
def compat_read_runtime_file(filename: str) -> dict:
    """Migration audit symbol 161: GET /api/files/{filename} returns
    the typed FileContent shape (extras allowed for name, size, etc)."""
    import os
    from task_hounds_api.db import ROOT
    safe = os.path.basename(filename)
    sid = resolve_session_id(None)
    if sid and safe == "worker_report":
        rep = db_wf.latest_worker_report(sid)
        if rep:
            return {"content": rep.get("report") or "", "path": "db:worker_reports.latest", "name": safe}
    if sid and safe == "manager_feedback":
        msg = db_wf.latest_manager_message(sid)
        if msg:
            return {"content": msg.get("content") or "", "path": "db:manager_messages.latest", "name": safe}
    candidates = [
        ROOT / "core" / "runtime" / "agent_files" / safe,
        ROOT / "core" / "runtime" / safe,
    ]
    for p in candidates:
        if p.exists():
            return {"content": p.read_text(encoding="utf-8", errors="replace"), "path": str(p), "name": safe}
    return {"content": "", "path": "", "name": safe}
    return {"content": "", "path": "", "name": "work_status"}


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
    """Kill the named agent's subprocess and mark it error in the DB.

    Unlike the old handler this does NOT validate against a fixed
    allow-list — any registered agent name is eligible.  If the agent
    has no active run the call is a silent miss (no 404).
    """
    process_killed = oc_registry.kill_agent_run(name)
    db_agent.update_agent(name, state="error", last_error="killed by user")
    return {"killed": name, "process_killed": process_killed}


@router.post("/api/agents/{name}/health")
def compat_agent_health(name: str) -> dict:
    """P7 id 193: real health check through the OpenCode lifecycle.

    The previous stub returned {ok:True,name} without DB lookup,
    404, or adapter.health() call. The fix:
      1. Look up the agent in agent_registry.
      2. 404 if the agent is not registered.
      3. Construct an OpenCodeLifecycle for the agent's
         host/port and call .health() to ping the server.
      4. Wrap any exception as {ok:False, error:...} so the
         caller can detect a real backend failure.
    """
    row = db_agent.get_agent(name)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    host = row.get("host") or "127.0.0.1"
    port = int(row.get("port") or 0)
    if not port:
        return {"ok": False, "error": f"agent {name!r} has no port configured", "name": name}
    try:
        from task_hounds_api.opencode.lifecycle import OpenCodeLifecycle
        return OpenCodeLifecycle(host=host, port=port).health()
    except Exception as e:
        return {"ok": False, "error": str(e), "name": name}


@router.put("/api/agents/{name}/health")
def compat_agent_health_put(name: str) -> dict:
    return compat_agent_health(name)


@router.post("/api/agents/{name}/clear-error")
def compat_agent_clear_error(name: str) -> dict:
    """P7 id 194: legacy clear-error.

    The old code was conditional: if state=='error' then set
    state='idle' + clear last_error; else just clear last_error.
    The new compat was unconditional (always idle) which could
    clobber a busy agent. The fix: only force idle when the
    agent is in state='error' (preserves busy/starting).
    Always clear last_error and bump last_seen. Returns the
    legacy {ok, role} envelope.
    """
    from datetime import datetime, timezone
    row = db_agent.get_agent(name)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    fields: dict = {"last_error": None, "last_seen": datetime.now(timezone.utc).isoformat()}
    if row.get("state") == "error":
        fields["state"] = "idle"
    db_agent.update_agent(name, **fields)
    return {"ok": True, "role": name}


@router.post("/api/agents/{name}/mark-resolved")
def compat_agent_mark_resolved(name: str) -> dict:
    """P7 id 196: legacy mark-resolved.

    Old code: conditional — only set state='idle' if the agent
    was in state='error'; otherwise just clear last_error. The
    new compat was unconditional which could clobber a busy
    agent. The fix: only force idle when state='error'.
    Returns the legacy {ok, role} envelope.
    """
    row = db_agent.get_agent(name)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    fields: dict = {"last_error": None}
    if row.get("state") == "error":
        fields["state"] = "idle"
    db_agent.update_agent(name, **fields)
    return {"ok": True, "role": name}


@router.post("/api/agents/{name}/retry")
def compat_agent_retry(name: str) -> dict:
    """P7 id 195: legacy retry.

    Old code: set state='idle' + clear last_error + reset
    task_complete=0. The new compat was missing the
    task_complete reset, so an agent that had finished its
    task (task_complete=1) would never be re-tried. The fix
    restores the task_complete=0 reset and returns the legacy
    {ok, role} envelope.
    """
    row = db_agent.get_agent(name)
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    db_agent.update_agent(
        name,
        state="idle",
        last_error=None,
        task_complete=0,
    )
    return {"ok": True, "role": name}


# /api/validate/send-config

@router.post("/api/validate/send-config")
async def compat_validate_send_config(request: Request) -> dict:
    """UI calls this to validate a chat send config before sending.
    UI expects: { valid: boolean, errors: string[], warnings: string[] }"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = validate_send_config_compat(body if isinstance(body, dict) else {})
    return {
        "ok": True,
        "valid": bool(result.get("valid")),
        "errors": list(result.get("errors") or []),
        "warnings": list(result.get("warnings") or []),
        **({"agent_name": result["agent_name"]} if result.get("agent_name") else {}),
    }


# /api/clear-all

@router.post("/api/clear-all")
def compat_clear_all() -> dict:
    """P11 id 344 (restored): full clear of the active session.

    Three-phase clear:
      1. Stop the background loop (runtime).
      2. Kill all agent subprocesses (runtime).
      3. Reset session-scoped DB tables and agent states (DB) via
         reset_session (same scope as /api/session/reset).

    The project/workspace row and user_directives are preserved so
    operators can start fresh with the same session.
    """
    workflow_stop_loop()
    oc_registry.kill_all_runs()
    sid = resolve_session_id(None)
    reset_result = {}
    if sid:
        reset_result = db_wf.reset_session(sid)
    return {"cleared": True, "session_id": sid, **reset_result}


# /api/session/reset (POST and GET)

@router.get("/api/session/reset")
def compat_session_reset_get() -> dict:
    return {"ok": True}


@router.post("/api/session/reset")
def compat_session_reset_post() -> dict:
    """P11 id 202 (restored): reset the active session.

    Two-phase reset:
      1. Stop the background loop and kill all agent runs (runtime).
      2. Clear session-scoped DB tables (manager_messages, worker_reports,
         chat_messages, session_todos, suggestion_queue, session_plan,
         project_handoff, reviewer_sessions, flow_checkpoints,
         workflow_runs), null role-session IDs, reset agent states.
      3. Clear streaming files for chat, manager, worker, reviewer.

    The project/workspace row in project_sessions is preserved.
    User directive is preserved.
    """
    workflow_stop_loop()
    oc_registry.kill_all_runs()
    sid = resolve_session_id(None)
    if sid:
        db_wf.reset_session(sid)
        clear_stream_compat("chat")
        clear_stream_compat("manager")
        clear_stream_compat("worker")
        clear_stream_compat("reviewer")
    return {"reset": True, "session_id": sid}


# /api/manager-messages POST (second compat copy) -- DELETED in Phase 6.

# /api/run-cycle, /api/loop/*, /api/worker/restart

@router.post("/api/run-cycle")
def compat_run_cycle() -> dict:
    """P7 id 200: legacy POST /api/run-cycle.

    The 0c44ba2 audit said the old endpoint raised 503 if
    opencode was disabled and 409 if no pending directive.
    The new arch delegates to workflow_run_once which
    returns {ok, ran, result} and never raises. Pinning the
    old error contract here would break the pre-existing
    P2/P3 tests (test_compat_loop_endpoints.py + P2/P3
    compat2) which treat run-cycle as a non-error "no-op"
    endpoint on a fresh DB. The fix: keep the current
    {ok, ran, result} envelope; the 503/409 contract is
    documented as a "follow-up" if a UI consumer needs it.
    """
    return workflow_run_once()


@router.post("/api/worker/restart")
def compat_worker_restart() -> dict:
    return {"restarted": True}


# /api/stream/*/clear (UI uses POST to clear stream files)

@router.post("/api/stream/manager/clear")
def compat_stream_manager_clear() -> dict:
    return clear_stream_compat("manager")


@router.post("/api/stream/worker/clear")
def compat_stream_worker_clear() -> dict:
    return clear_stream_compat("worker")


@router.post("/api/stream/reviewer/clear")
def compat_stream_reviewer_clear() -> dict:
    return clear_stream_compat("reviewer")


@router.post("/api/stream/chat/clear")
def compat_stream_chat_clear() -> dict:
    return clear_stream_compat("chat")


@router.post("/api/stream/{agent_name}/clear")
def compat_stream_agent_clear(agent_name: str) -> dict:
    return clear_stream_compat(agent_name)


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


# DEPRECATED for Task Hounds agent settings.
# Use GET /api/runtime/availability instead. That endpoint returns live
# OpenCode agents together with models, role bindings, server reachability,
# and credential warnings in one snapshot. Keeping this route only for
# old clients avoids accidental regressions, but new UI code should not
# call it because split legacy endpoints make debugging stale server/model
# state harder.
@router.get("/api/opencode/agents")
def compat_opencode_agents(host: str = "127.0.0.1", port: int = 18765) -> list[dict]:
    from task_hounds_api.opencode import client as oc_client
    return [
        {"id": a.get("name") or a.get("id"), "name": a.get("name") or a.get("id"), **a}
        for a in oc_client.list_agents(host, port)
        if a.get("name") or a.get("id")
    ]


# ── Migration audit compat aggregate (id 286) ───────────────────────────
# The 0c44ba2 opencode_options was a single aggregate endpoint that
# returned {agents, models, approval_formats, output_modes}. The new
# architecture exposes these separately (/api/opencode/agents,
# /api/opencode/models, /api/opencode/config-info, etc). This
# compat aggregate calls the existing helpers and adds the static
# approval_formats + output_modes options (constant UI values, not
# fake data — they are the legal values the UI can pass to the
# approval/output_mode APIs).
#
# Live agent list (compat_opencode_agents) requires a reachable
# OpenCode server. To avoid breaking the aggregate when the server
# is down, we return the static parts unconditionally and only add
# the live parts if the helper succeeds.


@router.get("/api/opencode_options")
def compat_opencode_options(host: str = "127.0.0.1", port: int = 18765) -> dict:
    """Migration audit symbol 286 compat: legacy GET /api/opencode_options
    aggregates the existing helpers. Static parts (approval_formats,
    output_modes) are always returned; live parts (agents, models)
    only if the OpenCode server is reachable."""
    out: dict = {
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
    try:
        out["agents"] = compat_opencode_agents(host=host, port=port)
        out["models"] = compat_opencode_available_models().get("models", [])
    except Exception:
        # OpenCode server unreachable: still return the static parts so
        # the UI can render the (empty) form skeleton.
        out["agents"] = []
        out["models"] = []
    return out


# DEPRECATED for Task Hounds agent settings.
# Use GET /api/runtime/availability instead. The new endpoint includes this
# model list plus runtime warnings, so the UI can show the operator why a
# selection may fail before sending requests to OpenCode.
@router.get("/api/opencode/available-models")
def compat_opencode_available_models() -> dict:
    return {"models": _model_options()}


@router.get("/api/opencode/config-info")
def compat_opencode_config_info() -> dict:
    import os
    from task_hounds_api.db import DB_PATH
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
        "xdg_config_home": os.environ.get("XDG_CONFIG_HOME") or None,
        "power_teams_db": str(DB_PATH),
    }


# DEPRECATED for Task Hounds agent settings.
# Use GET /api/runtime/availability instead. This legacy route returns only
# configured models/providers; it does not include live agents, bindings,
# reachability, or credential warnings, which caused incomplete debugging
# signals during the MiniMax/OpenCode chain investigation.
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
    """P7 id 262: DB-backed active-work check.

    The previous stub always returned {has_active:False}. The
    fix delegates to db_wf.has_active_work(active_session_id)
    which looks for a pending OR running directive on the
    user_directives table. Returns the legacy {has_active,active}
    shape so the new route stays consistent with id 176's
    user-decision wrapper (separate wrapper restored in id 176
    returns the older {active_work,reason} shape on demand).
    """
    sid = resolve_session_id(None)
    if not sid:
        return {
            "active_work": False,
            "has_active": False,
            "active": None,
            "job": None,
            "reason": "no_active_session",
        }
    try:
        from task_hounds_api.db.ops import graphflow_jobs as db_jobs
        job = db_jobs.active_for_session(sid)
        is_active = job is not None or db_wf.has_active_work(sid)
    except Exception as e:
        return {
            "active_work": False,
            "has_active": False,
            "active": None,
            "job": None,
            "reason": "error",
            "error": str(e),
        }
    return {
        "active_work": is_active,
        "has_active": is_active,
        "active": sid if is_active else None,
        "job": job,
        "reason": "active" if is_active else "idle",
    }


# ── Migration audit id 176: ActiveWorkResponse legacy compat ──────────
# The 0c44ba2 ActiveWorkResponse Pydantic model used the
# shape {active_work: bool, reason: str}. The new
# /api/runtime/active-work returns the richer {has_active,active}
# shape. Per the P7 user decision, we add a legacy wrapper
# at /api/active-work that exposes the OLD {active_work, reason}
# shape so the legacy UI can call it without code changes.
# The new route is unchanged.


@router.get("/api/active-work")
def compat_active_work_legacy() -> dict:
    """P7 id 176: legacy {active_work, reason} wrapper.

    The old ActiveWorkResponse Pydantic class used
    {active_work: bool, reason: str}. The new
    /api/runtime/active-work returns {has_active, active}.
    This wrapper delegates to the new check and returns the
    legacy shape so the old UI code works unchanged.

    `reason` is a short string describing the state:
    - "no_active_session"  when there is no active session
    - "idle"               when there is an active session but
                            no pending/running directive
    - "active"             when a pending/running directive
                            exists for the active session
    """
    sid = resolve_session_id(None)
    if not sid:
        return {"active_work": False, "reason": "no_active_session"}
    try:
        is_active = db_wf.has_active_work(sid)
    except Exception as e:
        return {"active_work": False, "reason": f"error: {e}"}
    if is_active:
        return {"active_work": True, "reason": "active"}
    return {"active_work": False, "reason": "idle"}


# /api/workflows/flow_01/* additional

# /api/workflows/flow_01/runs GET (second compat copy) -- DELETED in Phase 6.


@router.get("/api/workflows/flow_01/start-loop")
def compat_flow_start_loop_get() -> dict:
    return {"running": False}


@router.post("/api/workflows/flow_01/runs/{run_id}/cancel")
async def compat_flow_cancel(run_id: int, request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return flow01_cancel_run(run_id, body if isinstance(body, dict) else {})


@router.post("/api/workflows/flow_01/runs/{run_id}/pause")
async def compat_flow_pause(run_id: int, request: Request) -> dict:
    """Compat pause route. Reads {step_name} from body if present;
    flips workflow_runs.status to paused_before_{step_name} or
    paused. Delegates to db_wf.update_workflow_run_status.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    return flow01_pause_run(run_id, body if isinstance(body, dict) else {})


@router.post("/api/workflows/flow_01/runs/{run_id}/resume")
def compat_flow_resume(run_id: int) -> dict:
    """Compat resume route. Delegates to graph.resume_loop via
    RunController, so it actually loads the latest checkpoint
    and continues from the next node — not just a status flip.

    Repeated calls on a non-paused run return ok=False with a
    descriptive error so the caller can distinguish "no-op"
    from "real resume in progress".
    """
    return flow01_resume_run(run_id)


@router.put("/api/workflows/flow_01/directive")
async def compat_flow_put_directive_v2(request: Request) -> dict:
    return await compat_put_directive(request)


@router.get("/api/workflows/flow_01/directive")
def compat_flow_get_directive() -> dict | None:
    """UI sometimes calls GET instead of POST. Return current directive if any."""
    sid = resolve_session_id(None)
    if not sid:
        return {}
    d = _latest_visible_directive(sid)
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


# ── Migration audit P2 batch compat helpers (id 139, 191, 203, 238) ─────────
# These are small, targeted shims that restore the 0c44ba2 contract for
# legacy callers without re-introducing the deleted file-layout. Each
# helper is one screenful and pinned by a focused test.


def write_active_runtime_file(name: str, value: str) -> dict:
    """Migration audit symbol 139 compat: write a runtime file by name.

    The 0c44ba2 version wrote BOTH `active_runtime_file(name)` AND a
    legacy `RUNTIME_FILES / name` copy. The new architecture has no
    `RUNTIME_FILES` directory (DB-only state). This shim writes to a
    single canonical path under `core/runtime/<name>` and returns the
    legacy `{ok, path}` shape so any caller that checked the return
    still works.

    Safe: only writes inside the repo's runtime dir; never opens DB.
    """
    from task_hounds_api.db import ROOT
    path = Path(ROOT) / "core" / "runtime" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return {"ok": True, "path": str(path)}


def get_sessions_wrapped() -> dict:
    """Migration audit symbol 203 compat: /api/sessions wrapper.

    The 0c44ba2 endpoint returned {live, live_count, archived_count}.
    The new /api/sessions returns a bare list. This helper preserves
    the old wrapper for any caller that indexed .live_count.
    """
    from task_hounds_api.db.ops import runtime as db_rt
    from task_hounds_api.db.ops import project as db_project
    live = [session_to_workspace(s) for s in db_project.list_sessions()]
    archived = db_rt.list_archived()
    return {
        "live": live,
        "live_count": len(live),
        "archived": archived,
        "archived_count": len(archived),
    }


def clear_stream_compat(agent_name: str) -> dict:
    from task_hounds_api.db import ROOT
    sid = resolve_session_id(None)
    safe = "".join(ch for ch in agent_name if ch.isalnum() or ch in ("-", "_")) or agent_name
    if sid:
        path = ROOT / "core" / "runtime" / "agent_streams" / sid / f"{safe}.jsonl"
    else:
        path = ROOT / "core" / "runtime" / "agent_streams" / f"{safe}.jsonl"
    if path.exists():
        path.write_text("", encoding="utf-8")
    return {"cleared": agent_name, "path": str(path)}


def validate_send_config_compat(body: dict) -> dict:
    """Migration audit symbol 191 compat: validate an agent's send config.

    The 0c44ba2 endpoint required agent_name, ensured backend ready,
    called validate_agent_config, and raised HTTPException on failure.
    The new code path is via RuntimeManager. This shim accepts the
    legacy body shape and returns the legacy {valid, errors} shape.
    """
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.db.ops import runtime as db_rt

    agent_name = (body or {}).get("agent_name", "").strip()
    if not agent_name:
        return {"valid": False, "errors": ["agent_name is required"]}

    # Get the binding for this agent to find its specific provider
    binding = db_rt.get_binding(agent_name)
    if not binding:
        return {"valid": False, "errors": [f"no binding found for agent {agent_name!r}"]}

    model = binding.get("model", "")
    if not model:
        return {"valid": False, "errors": [f"no model configured for agent {agent_name!r}"]}

    provider_id = model.split("/", 1)[0]
    rm = rm_mod.RuntimeManager.instance()
    creds = rm.validate_credentials(provider_ids={provider_id}) or []
    if creds:
        return {"valid": False, "errors": list(creds)}
    return {"valid": True, "errors": [], "agent_name": agent_name}
