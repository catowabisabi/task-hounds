"""DB ops for session_todos.

Todos are scoped to a project_session_id and have hierarchical parent_id.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from task_hounds_api.db import connect


def list_todos(session_id: str, path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            """
            SELECT * FROM session_todos
             WHERE session_id=?
             ORDER BY parent_id IS NOT NULL, parent_id, position, id
            """,
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_todo(
    session_id: str,
    content: str,
    todo_id: str | None = None,
    status: str = "pending",
    priority: str = "medium",
    position: int = 0,
    parent_id: str | None = None,
    owner: str = "manager",
    path: Path | None = None,
) -> str:
    tid = todo_id or str(uuid.uuid4())
    with connect(path) as db:
        db.execute(
            """
            INSERT INTO session_todos
                (id, session_id, parent_id, content, status, priority, position, owner, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                content=excluded.content,
                status=excluded.status,
                priority=excluded.priority,
                position=excluded.position,
                owner=excluded.owner,
                updated_at=CURRENT_TIMESTAMP
            """,
            (tid, session_id, parent_id, content, status, priority, position, owner),
        )
        db.commit()
    return tid


def bulk_upsert_todos(session_id: str, todos: list[dict], path: Path | None = None) -> int:
    """Replace or insert many todos at once. Returns count."""
    n = 0
    with connect(path) as db:
        for pos, item in enumerate(todos):
            tid = item.get("id") or str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO session_todos
                    (id, session_id, parent_id, content, status, priority, position, owner, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    content=excluded.content,
                    status=excluded.status,
                    priority=excluded.priority,
                    position=excluded.position,
                    owner=excluded.owner,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    tid,
                    session_id,
                    item.get("parent_id"),
                    item.get("content", ""),
                    item.get("status", "pending"),
                    item.get("priority", "medium"),
                    item.get("position", pos),
                    item.get("owner", "manager"),
                ),
            )
            n += 1
        db.commit()
    return n


def patch_todo(todo_id: str, path: Path | None = None, **fields) -> None:
    if not fields:
        return
    keys = list(fields)
    sets = ", ".join(f"{k}=?" for k in keys) + ", updated_at=CURRENT_TIMESTAMP"
    values = [fields[k] for k in keys] + [todo_id]
    with connect(path) as db:
        db.execute(f"UPDATE session_todos SET {sets} WHERE id=?", values)
        db.commit()


def delete_todo(todo_id: str, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute("DELETE FROM session_todos WHERE id=?", (todo_id,))
        db.commit()


def delete_session_todos(session_id: str, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute("DELETE FROM session_todos WHERE session_id=?", (session_id,))
        db.commit()
