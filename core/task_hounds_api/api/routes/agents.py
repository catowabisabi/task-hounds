"""api.routes.agents — agent registry endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from task_hounds_api.db.ops import agent as db_agent
from task_hounds_api.api import schemas

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
def list_agents() -> list[dict]:
    return db_agent.list_agents()


@router.get("/{name}")
def get_agent(name: str) -> dict:
    a = db_agent.get_agent(name)
    if not a:
        raise HTTPException(status_code=404, detail="agent not found")
    return a


@router.patch("/{name}")
def update_agent(name: str, body: schemas.AgentUpdate) -> dict:
    if not db_agent.get_agent(name):
        raise HTTPException(status_code=404, detail="agent not found")
    fields = body.model_dump(exclude_none=True)
    db_agent.update_agent(name, **fields)
    return db_agent.get_agent(name) or {}


@router.post("/seed")
def seed_agents() -> dict:
    """Insert the 4 default agents (manager/worker/reviewer/chat) if missing."""
    db_agent.seed_default_agents()
    return {"seeded": True, "agents": [a["name"] for a in db_agent.list_agents()]}
