"""Manager Chat conversation and confirmed amendment endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from task_hounds_api.api.deps import require_session_id, resolve_session_id
from task_hounds_api.db.ops import manager_chat as db_manager_chat
from task_hounds_api.workflow import manager_chat_agent

router = APIRouter(prefix="/api/manager-chat", tags=["manager-chat"])


class SendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None
    conversation: list[dict[str, str]] = Field(default_factory=list, max_length=20)


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amendment_ids: list[str] = Field(min_length=1, max_length=30)
    session_id: str | None = None


@router.get("")
def get_manager_chat(session_id: str | None = Query(default=None)) -> dict:
    sid = resolve_session_id(session_id)
    if not sid:
        return {"messages": [], "amendments": []}
    return {
        "messages": [],
        "amendments": db_manager_chat.list_amendments(sid),
    }


@router.post("/send")
async def send_to_manager(body: SendBody) -> dict:
    sid = require_session_id(body.session_id)
    return await run_in_threadpool(
        manager_chat_agent.send, sid, body.content, body.conversation
    )


@router.post("/confirm")
def confirm_amendments(body: ConfirmBody) -> dict:
    sid = require_session_id(body.session_id)
    applied = db_manager_chat.apply_amendments(sid, body.amendment_ids)
    return {
        "ok": len(applied) == len(body.amendment_ids),
        "applied": applied,
        "amendments": db_manager_chat.list_amendments(sid),
    }
