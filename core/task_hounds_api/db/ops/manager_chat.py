"""Persistence and confirmed mutation handling for Manager Chat."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from task_hounds_api.db import connect
from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.db.ops import todo as db_todo
from task_hounds_api.db.ops import workflow as db_workflow


def list_messages(session_id: str, limit: int = 100, path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM manager_chat_messages WHERE session_id=? ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def list_amendments(session_id: str, path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM manager_chat_amendments WHERE session_id=? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        result.append(item)
    return result


def append_message(
    session_id: str,
    sender: str,
    content: str,
    *,
    response_id: str | None = None,
    message_type: str = "suggestion",
    path: Path | None = None,
) -> int:
    with connect(path) as db:
        cursor = db.execute(
            """INSERT INTO manager_chat_messages
               (response_id, session_id, sender, message_type, content)
               VALUES (?, ?, ?, ?, ?)""",
            (response_id, session_id, sender, message_type, content),
        )
        db.commit()
    return int(cursor.lastrowid)


def save_response(
    session_id: str,
    content: str,
    amendments: list[dict],
    path: Path | None = None,
) -> str:
    response_id = f"mgr_{uuid.uuid4().hex[:12]}"
    with connect(path) as db:
        for amendment in amendments:
            amendment_type = str(amendment.get("type") or "")
            if amendment_type == "suggestion":
                continue
            amendment_id = f"amd_{uuid.uuid4().hex[:12]}"
            db.execute(
                """INSERT INTO manager_chat_amendments
                   (id, response_id, session_id, amendment_type, title, description, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    amendment_id,
                    response_id,
                    session_id,
                    amendment_type,
                    amendment.get("title") or "Manager amendment",
                    amendment.get("description") or "",
                    json.dumps(amendment.get("payload") or {}, ensure_ascii=False),
                ),
            )
        db.commit()
    return response_id


def apply_amendments(session_id: str, amendment_ids: list[str], path: Path | None = None) -> list[str]:
    applied: list[str] = []
    for amendment_id in amendment_ids:
        with connect(path) as db:
            row = db.execute(
                """SELECT * FROM manager_chat_amendments
                   WHERE id=? AND session_id=? AND status='proposed'""",
                (amendment_id, session_id),
            ).fetchone()
        if not row:
            continue
        item = dict(row)
        payload = json.loads(item["payload_json"] or "{}")
        amendment_type = item["amendment_type"]
        changed = False
        if amendment_type == "user-directive-amend":
            directive = str(payload.get("directive") or "").strip()
            if directive:
                db_chat.save_user_directive(session_id, directive, path)
                changed = True
        elif amendment_type == "handoff-amend":
            if payload:
                db_workflow.upsert_handoff(session_id, path=path, **payload)
                changed = True
        elif amendment_type == "todo-amendment":
            todos = payload.get("todos")
            if isinstance(todos, list):
                db_todo.sync_manager_todos(
                    session_id,
                    todos,
                    payload.get("archive_updates") or [],
                    path,
                )
                changed = True
        if not changed:
            continue
        with connect(path) as db:
            db.execute(
                """UPDATE manager_chat_amendments
                   SET status='applied', applied_at=CURRENT_TIMESTAMP
                   WHERE id=? AND session_id=?""",
                (amendment_id, session_id),
            )
            db.commit()
        applied.append(amendment_id)
    return applied
