"""api.routes.workflow — workflow control: start/stop loop, plan, suggestion, reports.

session_id is optional on all session-scoped routes — defaults to the
active project session.

Loop control is delegated to a single BackgroundLoop singleton
(workflow.loop.BackgroundLoop). There is intentionally no second
controller here.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.db.ops import workflow as db_wf
from task_hounds_api.api.deps import resolve_session_id, require_session_id
from task_hounds_api.api import schemas
from task_hounds_api.workflow.loop import BackgroundLoop, run_once

router = APIRouter(prefix="/api/workflow", tags=["workflow"])


_bg = BackgroundLoop()


# ── Loop control helpers (imported by compat.py too) ───────────────────────


def workflow_loop_status() -> dict:
    """Current loop state plus the legacy `running`/`loop_running`
    fields. The UI consumes this to decide whether to show the
    loop as healthy, starting, or failed (with a retry button)."""
    running = _bg.is_running()
    return {
        "running": running,
        "loop_running": running,
        "loop_state": _bg.get_state(),
        "pid": _bg.get_pid(),
        "last_start_error": _bg.get_last_start_error(),
        "last_error_at": _bg.get_last_error_at(),
    }


def workflow_start_loop() -> dict:
    """Start the loop and BLOCK on the startup handshake.

    Returns the shape produced by `BackgroundLoop.start()`:
      ok, started, running, state, pid, error, reason.
    `started` is only True when ensure_managed_running actually
    succeeded. A failed handshake returns started=False with a
    populated `error` so the UI can surface the failure to the
    operator instead of silently accepting a dead loop."""
    return _bg.start()


def workflow_stop_loop() -> dict:
    return _bg.stop()


def workflow_run_once() -> dict:
    result = run_once()
    if result is None:
        return {"ok": True, "ran": False, "result": None}
    return {"ok": True, "ran": True, "result": result}


# ── Background loop control ────────────────────────────────────────────────


@router.get("/status")
def status() -> dict:
    return workflow_loop_status()


@router.post("/start-loop")
def start_loop() -> dict:
    return workflow_start_loop()


@router.post("/stop-loop")
def stop_loop() -> dict:
    return workflow_stop_loop()


@router.post("/run-once")
def run_once_route() -> dict:
    return workflow_run_once()


# ── Plan ────────────────────────────────────────────────────────────────────

@router.get("/plan")
def get_plan(session_id: str | None = Query(default=None)) -> dict | None:
    sid = resolve_session_id(session_id)
    if not sid:
        return {}
    return db_wf.get_plan(sid) or {}


@router.put("/plan")
def put_plan(body: dict, session_id: str | None = Query(default=None)) -> dict:
    sid = require_session_id(session_id)
    db_wf.set_plan(sid, body.get("content", ""), updated_by="manager")
    return {"updated": True}


# ── Suggestion ─────────────────────────────────────────────────────────────

@router.get("/suggestion")
def get_suggestion(session_id: str | None = Query(default=None)) -> dict | None:
    sid = resolve_session_id(session_id)
    if not sid:
        return {}
    return db_wf.get_active_suggestion(sid) or {}


@router.post("/suggestion")
def create_suggestion(body: dict, session_id: str | None = Query(default=None)) -> dict:
    sid = require_session_id(session_id)
    sugg_id = db_wf.create_suggestion(
        session_id=sid,
        content=body.get("content", ""),
        verification=body.get("verification"),
        status=body.get("status", "released"),
    )
    return {"id": sugg_id}


@router.post("/suggestion/{suggestion_id}/status")
def update_suggestion_status(suggestion_id: int, body: dict) -> dict:
    db_wf.update_suggestion_status(suggestion_id, body.get("status", "done"))
    return {"updated": suggestion_id}


# ── Worker reports ─────────────────────────────────────────────────────────

@router.get("/reports")
def list_reports(
    session_id: str | None = Query(default=None),
    limit: int = 20,
) -> list[dict]:
    sid = resolve_session_id(session_id)
    if not sid:
        return []
    return db_wf.list_worker_reports(sid, limit=limit)


# ── Manager messages ──────────────────────────────────────────────────────

@router.get("/manager-messages")
def manager_messages(
    session_id: str | None = Query(default=None),
    limit: int = 20,
) -> list[dict]:
    sid = resolve_session_id(session_id)
    if not sid:
        return []
    return db_wf.list_manager_messages(sid, limit=limit)


# Legacy aliases (Phase 6) -- the UI ternary flow01Mode ? ... : ...
# still uses /api/manager-messages and /api/workflows/flow_01/... .
# The compat duplicates were deleted; the authoritative versions
# live below as proper APIRouter modules.

manager_messages_root = APIRouter(tags=["manager-messages-legacy"])


@manager_messages_root.get("/api/manager-messages")
def legacy_manager_messages_root() -> list[dict]:
    sid = resolve_session_id(None)
    if not sid:
        return []
    return db_wf.list_manager_messages(sid)


@manager_messages_root.post("/api/manager-messages")
async def legacy_post_manager_message_root(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(body.get("session_id"))
    mid = db_wf.append_manager_message(sid, body.get("content", ""))
    return {"id": mid}


flow01_router = APIRouter(prefix="/api/workflows/flow_01", tags=["flow_01_legacy"])


@flow01_router.get("/manager-messages")
def legacy_flow01_manager_messages() -> list[dict]:
    sid = resolve_session_id(None)
    if not sid:
        return []
    return db_wf.list_manager_messages(sid)


@flow01_router.post("/manager-messages")
async def legacy_flow01_post_manager_message(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(body.get("session_id"))
    mid = db_wf.append_manager_message(sid, body.get("content", ""))
    return {"id": mid}


@flow01_router.get("/runs")
def legacy_flow01_runs(
    limit: int = Query(default=20),
) -> list[dict]:
    sid = resolve_session_id(None)
    if not sid:
        return []
    try:
        return db_wf.list_workflow_runs(sid, limit=limit)
    except Exception:
        return []


# ── Handoff ────────────────────────────────────────────────────────────────

@router.get("/handoff")
def get_handoff(session_id: str | None = Query(default=None)) -> dict | None:
    sid = resolve_session_id(session_id)
    if not sid:
        return {}
    return db_wf.get_handoff(sid) or {}


@router.put("/handoff")
def put_handoff(body: dict, session_id: str | None = Query(default=None)) -> dict:
    sid = require_session_id(session_id)
    db_wf.upsert_handoff(sid, **body)
    return {"updated": True}


# ── Directives ─────────────────────────────────────────────────────────────

@router.post("/directive")
def create_directive(body: schemas.DirectiveCreate) -> dict:
    sid = require_session_id(body.session_id)
    did = db_chat.create_directive(sid, body.directive)
    return {"id": did, "session_id": sid}


@router.get("/directives")
def list_directives(
    session_id: str | None = Query(default=None),
    limit: int = 20,
) -> list[dict]:
    sid = resolve_session_id(session_id)
    if not sid:
        return []
    return db_chat.list_directives(sid, limit=limit)
