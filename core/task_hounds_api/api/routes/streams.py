"""api.routes.streams — SSE endpoints for live state updates.

Streams agent state and workflow progress from the DB.
"""
from __future__ import annotations

import asyncio
import json
import time
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from task_hounds_api.db.ops import agent as db_agent
from task_hounds_api.db.ops import workflow as db_wf

router = APIRouter(prefix="/api/streams", tags=["streams"])


@router.get("/agents")
async def stream_agents() -> StreamingResponse:
    """SSE stream of agent_registry state changes. Polls DB every 2s."""
    async def event_gen():
        last = ""
        while True:
            agents = db_agent.list_agents()
            snapshot = json.dumps([{"name": a["name"], "state": a["state"], "current_step": a.get("current_step", "")} for a in agents])
            if snapshot != last:
                yield f"data: {snapshot}\n\n"
                last = snapshot
            await asyncio.sleep(2)
    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/manager-messages")
async def stream_manager_messages(session_id: str) -> StreamingResponse:
    """SSE stream of new manager_messages for a session."""
    async def event_gen():
        last_id = 0
        while True:
            msgs = db_wf.list_manager_messages(session_id, limit=5)
            for m in reversed(msgs):
                if m["id"] > last_id:
                    yield f"data: {json.dumps(m)}\n\n"
                    last_id = m["id"]
            await asyncio.sleep(2)
    return StreamingResponse(event_gen(), media_type="text/event-stream")
