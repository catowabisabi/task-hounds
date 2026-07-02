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


def list_active_todos(session_id: str, path: Path | None = None) -> list[dict]:
    return [todo for todo in list_todos(session_id, path) if bool(todo.get("is_active", 1))]


def list_archived_todos(
    session_id: str,
    limit: int = 20,
    path: Path | None = None,
) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            """
            SELECT * FROM session_todos
             WHERE session_id=? AND is_active=0
             ORDER BY archived_at DESC, updated_at DESC
             LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def restore_completed_from_snapshot(
    session_id: str,
    snapshot_todos: list[dict],
    reopen_todo_ids: set[str] | None = None,
    path: Path | None = None,
) -> int:
    """Restore persisted completion unless a later explicit reopen exists."""
    completed_ids = {
        str(todo.get("id"))
        for todo in snapshot_todos
        if isinstance(todo, dict)
        and todo.get("id")
        and str(todo.get("status") or "").lower() == "completed"
    }
    completed_ids -= reopen_todo_ids or set()
    if not completed_ids:
        return 0
    placeholders = ", ".join("?" for _ in completed_ids)
    with connect(path) as db:
        cursor = db.execute(
            f"""UPDATE session_todos
                   SET status='completed', updated_at=CURRENT_TIMESTAMP
                 WHERE session_id=? AND is_active=1
                   AND status!='completed'
                   AND id IN ({placeholders})""",
            [session_id, *sorted(completed_ids)],
        )
        db.commit()
    return int(cursor.rowcount)


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
    from task_hounds_api.db.ops.rounds import active_round_id
    round_id = active_round_id(session_id, path)
    with connect(path) as db:
        db.execute(
            """
            INSERT INTO session_todos
                (id, session_id, parent_id, content, status, worker_task_status,
                 reviewer_task_status, attempt_count, human_attention_status,
                 is_active, round_id, priority, position, owner, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                content=excluded.content,
                status=excluded.status,
                worker_task_status=excluded.worker_task_status,
                reviewer_task_status=excluded.reviewer_task_status,
                attempt_count=excluded.attempt_count,
                human_attention_status=excluded.human_attention_status,
                is_active=1,
                archive_reason=NULL,
                archive_note=NULL,
                archived_at=NULL,
                archived_by=NULL,
                replaced_by_todo_id=NULL,
                priority=excluded.priority,
                position=excluded.position,
                owner=excluded.owner,
                updated_at=CURRENT_TIMESTAMP
            """,
            (tid, session_id, parent_id, content, status, "pending", "pending", 0, "none",
             round_id, priority, position, owner),
        )
        db.commit()
    return tid


def bulk_upsert_todos(
    session_id: str,
    todos: list[dict],
    path: Path | None = None,
    reopen_todo_ids: set[str] | None = None,
) -> int:
    """Replace or insert many todos at once. Returns count."""
    n = 0
    allowed_reopens = reopen_todo_ids or set()
    from task_hounds_api.db.ops.rounds import active_round_id
    round_id = active_round_id(session_id, path)
    with connect(path) as db:
        for pos, item in enumerate(todos):
            tid = item.get("id") or str(uuid.uuid4())
            requested_status = item.get("status", "pending")
            existing = db.execute(
                "SELECT status FROM session_todos WHERE id=? AND session_id=?",
                (tid, session_id),
            ).fetchone()
            if (
                existing
                and existing["status"] == "completed"
                and requested_status != "completed"
                and tid not in allowed_reopens
            ):
                requested_status = "completed"
            db.execute(
                """
                INSERT INTO session_todos
                    (id, session_id, parent_id, content, status, worker_task_status,
                     reviewer_task_status, attempt_count, human_attention_status,
                     is_active, round_id, plan_revision, priority, position, owner, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    content=excluded.content,
                    status=excluded.status,
                    worker_task_status=excluded.worker_task_status,
                    reviewer_task_status=excluded.reviewer_task_status,
                    attempt_count=excluded.attempt_count,
                    human_attention_status=excluded.human_attention_status,
                    is_active=1,
                    plan_revision=excluded.plan_revision,
                    archive_reason=NULL,
                    archive_note=NULL,
                    archived_at=NULL,
                    archived_by=NULL,
                    replaced_by_todo_id=NULL,
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
                    requested_status,
                    item.get("worker_task_status", "pending"),
                    item.get("reviewer_task_status", "pending"),
                    int(item.get("attempt_count", 0) or 0),
                    item.get("human_attention_status", "none"),
                    round_id,
                    int(item.get("plan_revision", 1) or 1),
                    item.get("priority", "medium"),
                    item.get("position", pos),
                    item.get("owner", "manager"),
                ),
            )
            n += 1
        db.commit()
    return n


def sync_manager_todos(
    session_id: str,
    todos: list[dict],
    archive_updates: list[dict] | None = None,
    path: Path | None = None,
    reopen_todos: list[dict] | None = None,
) -> dict[str, int]:
    """Publish one complete Manager plan revision and archive missing active todos."""
    archive_by_id = {
        str(item.get("todo_id")): item
        for item in (archive_updates or [])
        if item.get("todo_id")
    }
    reopen_ids = {
        str(item.get("todo_id"))
        for item in (reopen_todos or [])
        if item.get("todo_id") and str(item.get("reason") or "").strip() and item.get("evidence")
    }
    with connect(path) as db:
        row = db.execute(
            "SELECT COALESCE(MAX(plan_revision), 0) AS revision FROM session_todos WHERE session_id=?",
            (session_id,),
        ).fetchone()
        revision = int(row["revision"] or 0) + 1
    revision_todos = [{**todo, "plan_revision": revision} for todo in todos]
    upserted = bulk_upsert_todos(
        session_id,
        revision_todos,
        path,
        reopen_todo_ids=reopen_ids,
    )
    with connect(path) as db:
        active_rows = db.execute(
            "SELECT id FROM session_todos WHERE session_id=? AND is_active=1",
            (session_id,),
        ).fetchall()
        incoming_ids = {
            str(item.get("id"))
            for item in todos
            if item.get("id")
        }
        archived = 0
        for row in active_rows:
            todo_id = str(row["id"])
            if todo_id in incoming_ids:
                continue
            directive = archive_by_id.get(todo_id, {})
            reason = str(directive.get("reason") or "other")
            note = str(directive.get("note") or f"Removed from active plan revision {revision}.")
            db.execute(
                """
                UPDATE session_todos
                   SET is_active=0, archive_reason=?, archive_note=?,
                       archived_at=CURRENT_TIMESTAMP, archived_by='manager',
                       replaced_by_todo_id=?, updated_at=CURRENT_TIMESTAMP
                 WHERE id=? AND session_id=?
                """,
                (
                    reason,
                    note,
                    directive.get("replaced_by_todo_id"),
                    todo_id,
                    session_id,
                ),
            )
            archived += 1
        db.commit()
    return {"revision": revision, "upserted": upserted, "archived": archived}


def patch_todo(todo_id: str, path: Path | None = None, **fields) -> None:
    if not fields:
        return
    keys = list(fields)
    sets = ", ".join(f"{k}=?" for k in keys) + ", updated_at=CURRENT_TIMESTAMP"
    values = [fields[k] for k in keys] + [todo_id]
    with connect(path) as db:
        db.execute(f"UPDATE session_todos SET {sets} WHERE id=?", values)
        db.commit()


# Whitelist of patchable todo columns. Migration audit symbol 259:
# prevent callers from updating DB-only fields like id/session_id
# via arbitrary kwargs (this was a partial regression).
PATCHABLE_TODO_FIELDS: frozenset[str] = frozenset(
    {
        "content", "status", "priority", "position", "owner", "parent_id",
        "worker_task_status", "reviewer_task_status", "attempt_count",
        "worker_timeout_count",
        "human_attention_status",
        "is_active", "archive_reason", "archive_note", "archived_by",
        "replaced_by_todo_id",
    }
)


def delete_todo(todo_id: str, path: Path | None = None) -> None:
    """Single-row delete. Use delete_todo_recursive for parent+children."""
    with connect(path) as db:
        db.execute("DELETE FROM session_todos WHERE id=?", (todo_id,))
        db.commit()


def delete_todo_recursive(todo_id: str, path: Path | None = None) -> int:
    """Delete a todo and all its descendants.

    Migration audit symbol 260 / 331: the 0c44ba2 contract removed
    children when their parent was deleted; the new delete_todo only
    removed the single row. This helper restores the recursive
    behavior. Returns the total number of rows deleted.
    """
    with connect(path) as db:
        # Walk down the tree: collect every descendant id, then delete.
        to_delete = [todo_id]
        frontier = [todo_id]
        while frontier:
            placeholders = ",".join("?" for _ in frontier)
            rows = db.execute(
                f"SELECT id FROM session_todos WHERE parent_id IN ({placeholders})",
                frontier,
            ).fetchall()
            frontier = [r[0] for r in rows]
            to_delete.extend(frontier)
        placeholders = ",".join("?" for _ in to_delete)
        cur = db.execute(
            f"DELETE FROM session_todos WHERE id IN ({placeholders})",
            to_delete,
        )
        db.commit()
        return cur.rowcount


def delete_session_todos(session_id: str, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute("DELETE FROM session_todos WHERE session_id=?", (session_id,))
        db.commit()
