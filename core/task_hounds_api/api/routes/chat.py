"""api.routes.chat — authoritative chat endpoints (Phase 3: only one handler per route).

Read endpoints return [] when no active session (UI can render empty state).
Write endpoints return 400 when no active session.
The compat.py duplicate handlers were removed in commit c781090+1.
All chat responses (success, error, null) are written to the debug log
so a silent failure shows up in docs/tools/debug_log_writer.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.api.deps import resolve_session_id, require_session_id
from task_hounds_api.api import schemas
from task_hounds_api.api.debug_logs import write_backend_debug
from task_hounds_api.workflow import chat_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/messages", response_model=list[schemas.ChatMessageOut])
def list_messages(
    session_id: str | None = Query(default=None),
    limit: int = 100,
) -> list[dict]:
    """Migration audit symbol 153: GET /api/chat/messages returns a
    typed list of ChatMessageOut objects."""
    sid = resolve_session_id(session_id)
    if not sid:
        write_backend_debug(
            session_id=None,
            level="info",
            category="chat",
            event="list_messages.no_active_session",
            data={"limit": limit},
        )
        return []
    rows = db_chat.list_chat(sid, limit=limit)
    write_backend_debug(
        session_id=sid,
        level="info",
        category="chat",
        event="list_messages.ok",
        data={"row_count": len(rows), "limit": limit},
    )
    return rows


@router.post("/send", response_model=schemas.ChatSendResponse)
async def send(request_body: dict) -> dict:
    """Send a chat message and return the Chat agent reply.

    Migration audit symbol 156: POST /api/chat/send returns the typed
    ChatSendResponse shape (ok/reply/messages/error).

    Phase-8 (P2): log the FULL request body and FULL response
    so operators can replay any chat interaction from the
    debug log. Also catch chat_agent.send exceptions and log
    them as send.exception — a silent subprocess crash used to
    have no trace.
    """
    sid = require_session_id(request_body.get("session_id"))
    sender = request_body.get("sender", "human")
    content = request_body.get("content", "")
    t0 = time.monotonic()
    request_log = {
        "session_id": sid,
        "sender": sender,
        "content": content,
    }
    try:
        result = await run_in_threadpool(chat_agent.send, sid, content, sender=sender)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        write_backend_debug(
            session_id=sid,
            level="error",
            category="chat",
            event="send.exception",
            data={
                "request": request_log,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "elapsed_ms": elapsed_ms,
            },
        )
        return {"ok": False, "error": str(exc)}
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if result.get("ok"):
        write_backend_debug(
            session_id=sid,
            level="info",
            category="chat",
            event="send.ok",
            data={
                "request": request_log,
                "response": result,
                "elapsed_ms": elapsed_ms,
            },
        )
    else:
        write_backend_debug(
            session_id=sid,
            level="error",
            category="chat",
            event="send.fail",
            data={
                "request": request_log,
                "response": result,
                "elapsed_ms": elapsed_ms,
            },
        )
    return result


@router.post("/messages/{message_id}/accept-directive")
def accept_directive(message_id: int, request_body: dict) -> dict:
    sid = require_session_id(request_body.get("session_id"))
    try:
        saved = db_chat.accept_directive_proposal(sid, message_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, **saved}


@router.get("/status", response_model=schemas.ChatStatusResponse)
def chat_status() -> dict:
    """Health check for the Chat agent subsystem. UI polls this to
    decide whether to show a runtime-down banner vs the chat input.

    Migration audit symbol 155: GET /api/chat/status returns the
    typed ChatStatusResponse shape.

    Phase-11 (P1): enabled = binding_ok (not cred_ok). An external
    OpenCode server that has its own credentials can still serve
    chat even when this backend's env credentials are missing.
    The actual /api/chat/send result is the final truth; this
    endpoint is a hint, not a gate.
    """
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    from task_hounds_api.db.ops import runtime as db_rt
    from task_hounds_api.db.ops import project as db_project

    rm = RuntimeManager.instance()
    active = db_project.get_active_session() or {}
    workspace_path = str(active.get("workspace_path") or "")
    workspace_missing = bool(active.get("path_missing")) or not workspace_path
    provider_ids: set[str] = set()
    try:
        for binding in db_rt.list_bindings():
            model = str(binding.get("model") or "")
            if "/" in model:
                provider_ids.add(model.split("/", 1)[0])
    except Exception:
        provider_ids = set()
    cred_warnings = rm.validate_credentials(provider_ids=provider_ids or None) or []
    credentials_ok = not cred_warnings
    binding_ok = True
    binding_reachable = True
    try:
        from task_hounds_api.opencode.binding_resolver import resolve_for_role
        resolve_for_role("chat")
    except Exception as exc:
        binding_ok = False
        binding_reachable = False
        write_backend_debug(
            session_id=None,
            level="warning",
            category="chat",
            event="status.binding_unresolved",
            data={"error": repr(exc)},
        )
    enabled = binding_ok and not workspace_missing
    reason = (
        "workspace_missing" if workspace_missing
        else "missing_credentials" if cred_warnings
        else ("binding_unresolved" if not binding_ok else "")
    )
    return {
        "ok": enabled,
        "enabled": enabled,
        "reason": reason,
        "binding_ok": binding_ok,
        "binding_reachable": binding_reachable,
        "credentials_ok": credentials_ok,
        "credential_warnings": list(cred_warnings),
        "workspace_missing": workspace_missing,
        "workspace_path": workspace_path,
    }
