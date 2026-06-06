"""api.routes.chat — authoritative chat endpoints (Phase 3: only one handler per route).

Read endpoints return [] when no active session (UI can render empty state).
Write endpoints return 400 when no active session.
The compat.py duplicate handlers were removed in commit c781090+1.
All chat responses (success, error, null) are written to the debug log
so a silent failure shows up in tools/debug_log_writer.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Query

from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.api.deps import resolve_session_id, require_session_id
from task_hounds_api.api.debug_logs import write_backend_debug
from task_hounds_api.workflow import chat_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/messages")
def list_messages(
    session_id: str | None = Query(default=None),
    limit: int = 100,
) -> list[dict]:
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


@router.post("/send")
async def send(request_body: dict) -> dict:
    """Send a chat message and return the Chat agent reply.

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
        result = chat_agent.send(sid, content, sender=sender)
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


@router.get("/status")
def chat_status() -> dict:
    """Health check for the Chat agent subsystem. UI polls this to
    decide whether to show a runtime-down banner vs the chat input."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    rm = RuntimeManager.instance()
    cred_warnings = rm.validate_credentials() or []
    chat_binding_ok = True
    try:
        from task_hounds_api.opencode.binding_resolver import resolve_for_role
        resolve_for_role("chat")
    except Exception as exc:
        chat_binding_ok = False
        write_backend_debug(
            session_id=None,
            level="warning",
            category="chat",
            event="status.binding_unresolved",
            data={"error": repr(exc)},
        )
    return {
        "ok": chat_binding_ok and not cred_warnings,
        "enabled": chat_binding_ok and not cred_warnings,
        "reason": (
            "missing_credentials" if cred_warnings
            else ("binding_unresolved" if not chat_binding_ok else "")
        ),
    }
