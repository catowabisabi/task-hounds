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


@router.get("", response_model=list[schemas.TodoOut])
def list_todos(
    session_id: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
) -> list[dict]:
    """List todos for the active session (or `session_id` query param).

    Migration audit symbol 102: the response is the typed `TodoOut`
    schema (mirrors the old 0c44ba2 UITodoItem TypedDict). Frontend
    code that consumed the bare DB dict will see the same fields with
    type validation.
    """
    sid = resolve_session_id(session_id)
    if not sid:
        return []
    return (
        db_todo.list_todos(sid)
        if include_archived
        else db_todo.list_active_todos(sid)
    )


@router.post("")
def upsert_todo(
    body: schemas.TodoUpsert,
    session_id: str | None = Query(default=None),
) -> dict:
    """Migration audit symbol 258: POST /api/todos returns the
    legacy {ok, id} shape on success (the new authoritative shape
    is {id} only; the legacy wrapper is preserved for old UI
    callers that check `ok` first)."""
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
    return {"ok": True, "id": tid}


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
    # Migration audit symbol 259: refuse DB-only fields if any caller
    # bypasses TodoPatch. The schema already excludes id/session_id, but
    # a request body could still smuggle them via extra='allow' (Pydantic
    # default). Keep the defense-in-depth check.
    bad = set(fields) - db_todo.PATCHABLE_TODO_FIELDS
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"disallowed fields: {sorted(bad)}",
        )
    db_todo.patch_todo(todo_id, **fields)
    return {"updated": todo_id}


@router.delete("/{todo_id}")
def delete_todo(todo_id: str) -> dict:
    # Migration audit symbol 260: the 0c44ba2 contract deleted children
    # when their parent was deleted. Recursive is the safer default.
    n = db_todo.delete_todo_recursive(todo_id)
    return {"deleted": todo_id, "removed": n}
