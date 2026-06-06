"""api.routes.todos — CRUD for session todos.

Read endpoints return [] when no active session.
Write endpoints return 400 when no active session.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from task_hounds_api.db.ops import todo as db_todo
from task_hounds_api.api.deps import resolve_session_id, require_session_id
from task_hounds_api.api import schemas

router = APIRouter(prefix="/api/todos", tags=["todos"])


@router.get("")
def list_todos(session_id: str | None = Query(default=None)) -> list[dict]:
    sid = resolve_session_id(session_id)
    if not sid:
        return []
    return db_todo.list_todos(sid)


@router.post("")
def upsert_todo(
    body: schemas.TodoUpsert,
    session_id: str | None = Query(default=None),
) -> dict:
    sid = require_session_id(session_id)
    tid = db_todo.upsert_todo(
        session_id=sid,
        content=body.content,
        todo_id=body.id,
        status=body.status,
        priority=body.priority,
        position=body.position,
        parent_id=body.parent_id,
        owner=body.owner,
    )
    return {"id": tid}


@router.post("/batch")
def batch_upsert(
    body: schemas.TodoBatchUpsert,
    session_id: str | None = Query(default=None),
) -> dict:
    sid = require_session_id(session_id)
    n = db_todo.bulk_upsert_todos(sid, [t.model_dump() for t in body.todos])
    return {"count": n}


@router.patch("/{todo_id}")
def patch_todo(todo_id: str, body: schemas.TodoPatch) -> dict:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    db_todo.patch_todo(todo_id, **fields)
    return {"updated": todo_id}


@router.delete("/{todo_id}")
def delete_todo(todo_id: str) -> dict:
    db_todo.delete_todo(todo_id)
    return {"deleted": todo_id}
