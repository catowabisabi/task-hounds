"""api.routes.agents — agent registry endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from task_hounds_api.db.ops import agent as db_agent
from task_hounds_api.db.ops import execution as db_execution
from task_hounds_api.api import schemas
from task_hounds_api.api.deps import resolve_session_id

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("", response_model=list[schemas.AgentOut])
def list_agents(session_id: str | None = Query(default=None)) -> list[dict]:
    """Migration audit symbol 146: GET /api/agents returns a typed
    list of AgentOut objects (extras allowed for DB-derived fields)."""
    agents = db_agent.list_agents()
    sid = resolve_session_id(session_id)
    if not sid:
        return agents
    executions = db_execution.list_executions(sid)
    latest_by_role: dict[str, dict] = {}
    for execution in executions:
        latest_by_role.setdefault(str(execution.get("role")), execution)
    for agent in agents:
        execution = latest_by_role.get(str(agent.get("role")))
        if not execution:
            agent["state"] = "idle"
            agent["current_step"] = None
            agent["last_error"] = None
            agent["last_error_at"] = None
            agent["last_stream_at"] = None
            agent["project_session_id"] = sid
            continue
        agent["state"] = execution.get("status") or "idle"
        agent["current_step"] = execution.get("current_step")
        agent["last_error"] = execution.get("error")
        agent["project_session_id"] = sid
        agent["role_session_id"] = execution.get("opencode_session_id")
    return agents


@router.get("/executions")
def list_agent_executions(
    session_id: str | None = Query(default=None),
    workflow_run_id: int | None = Query(default=None),
) -> list[dict]:
    sid = resolve_session_id(session_id)
    if not sid:
        return []
    return db_execution.list_executions(sid, workflow_run_id)


@router.get("/{name}", response_model=schemas.AgentOut)
def get_agent(name: str) -> dict:
    """Migration audit symbol 146: GET /api/agents/{name} returns a
    typed AgentOut."""
    a = db_agent.get_agent(name)
    if not a:
        raise HTTPException(status_code=404, detail="agent not found")
    return a


@router.patch("/{name}", response_model=schemas.AgentOut)
def update_agent(name: str, body: schemas.AgentUpdate) -> dict:
    """Migration audit symbol 146/147: PATCH /api/agents/{name} uses
    the strict AgentUpdate schema for the body and returns AgentOut."""
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
