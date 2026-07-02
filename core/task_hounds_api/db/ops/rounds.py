"""Mechanical project-round lifecycle."""
from __future__ import annotations

import json
from pathlib import Path

from task_hounds_api.db import connect


def current_round(session_id: str, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            """SELECT * FROM project_rounds
                WHERE project_session_id=?
                ORDER BY round_number DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    if item.get("snapshot_json"):
        try:
            item["snapshot"] = json.loads(item["snapshot_json"])
        except (TypeError, json.JSONDecodeError):
            item["snapshot"] = {}
    return item


def ensure_round(session_id: str, directive: str, path: Path | None = None) -> dict:
    existing = current_round(session_id, path)
    if existing:
        return existing
    round_id = f"round_{session_id}_1"
    with connect(path) as db:
        db.execute(
            """INSERT INTO project_rounds
               (id, project_session_id, round_number, directive, status)
               VALUES (?, ?, 1, ?, 'active')""",
            (round_id, session_id, directive),
        )
        db.commit()
    return current_round(session_id, path) or {}


def create_next_round(session_id: str, directive: str, path: Path | None = None) -> dict:
    previous = current_round(session_id, path)
    if previous and previous["status"] != "locked":
        raise ValueError("current round is not locked")
    number = int(previous["round_number"] if previous else 0) + 1
    round_id = f"round_{session_id}_{number}"
    with connect(path) as db:
        db.execute(
            """UPDATE session_todos
                  SET is_active=0, archive_reason='replaced',
                      archive_note='Previous project round was locked.',
                      archived_at=COALESCE(archived_at, CURRENT_TIMESTAMP),
                      archived_by='round_manager',
                      updated_at=CURRENT_TIMESTAMP
                WHERE session_id=? AND is_active=1""",
            (session_id,),
        )
        db.execute(
            """INSERT INTO project_rounds
               (id, project_session_id, round_number, directive, status)
               VALUES (?, ?, ?, ?, 'active')""",
            (round_id, session_id, number, directive),
        )
        db.commit()
    return current_round(session_id, path) or {}


def active_round_id(session_id: str, path: Path | None = None) -> str | None:
    item = current_round(session_id, path)
    return str(item["id"]) if item and item["status"] == "active" else None


def try_lock_round(
    session_id: str,
    run_id: int,
    summary: str,
    path: Path | None = None,
) -> dict:
    item = current_round(session_id, path)
    if not item or item["status"] != "active":
        return {"locked": False, "reason": "no_active_round"}
    round_id = item["id"]
    with connect(path) as db:
        todos = [
            dict(row) for row in db.execute(
                """SELECT * FROM session_todos
                    WHERE session_id=? AND round_id=? AND is_active=1""",
                (session_id, round_id),
            )
        ]
        unfinished = [
            todo for todo in todos
            if todo.get("status") != "completed"
            or todo.get("human_attention_status") == "attention_required"
        ]
        active_executions = db.execute(
            """SELECT COUNT(*) AS count FROM agent_execution_state
                WHERE project_session_id=?
                  AND status IN ('queued', 'running', 'busy')""",
            (session_id,),
        ).fetchone()["count"]
        if unfinished:
            return {"locked": False, "reason": "unfinished_todos", "count": len(unfinished)}
        if active_executions:
            return {"locked": False, "reason": "active_executions", "count": active_executions}
        snapshot = {
            "round_id": round_id,
            "directive": item["directive"],
            "todos": todos,
            "completion_run_id": run_id,
            "completion_summary": summary,
        }
        db.execute(
            """UPDATE project_rounds
                  SET status='locked', completion_run_id=?,
                      completion_summary=?, snapshot_json=?,
                      locked_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='active'""",
            (run_id, summary, json.dumps(snapshot, ensure_ascii=False, default=str), round_id),
        )
        db.commit()
    return {"locked": True, "round": current_round(session_id, path)}
