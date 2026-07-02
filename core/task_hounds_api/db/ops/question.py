"""Persistence for interactive OpenCode question-tool requests."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from task_hounds_api.db import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_pending(
    *,
    request_id: str,
    opencode_session_id: str,
    project_session_id: str | None,
    role: str,
    host: str,
    port: int,
    workspace_path: str | None,
    questions: list[dict[str, Any]],
    asked_at: str,
    deadline_at: str,
    path: Path | None = None,
) -> bool:
    """Insert a question once. Returns True only for a newly seen request."""
    now = _now()
    with connect(path) as db:
        cur = db.execute(
            """INSERT OR IGNORE INTO opencode_questions
               (request_id, opencode_session_id, project_session_id, role,
                host, port, workspace_path, questions_json, status,
                asked_at, deadline_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (
                request_id,
                opencode_session_id,
                project_session_id,
                role,
                host,
                port,
                workspace_path,
                json.dumps(questions, ensure_ascii=False),
                asked_at,
                deadline_at,
                now,
            ),
        )
        db.commit()
        return cur.rowcount > 0


def list_pending(
    project_session_id: str | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    with connect(path) as db:
        if project_session_id:
            rows = db.execute(
                """SELECT * FROM opencode_questions
                   WHERE status='pending' AND project_session_id=?
                   ORDER BY asked_at, request_id""",
                (project_session_id,),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT * FROM opencode_questions
                   WHERE status='pending' ORDER BY asked_at, request_id"""
            ).fetchall()
    return [_decode(dict(row)) for row in rows]


def get(request_id: str, path: Path | None = None) -> dict[str, Any] | None:
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM opencode_questions WHERE request_id=?",
            (request_id,),
        ).fetchone()
    return _decode(dict(row)) if row else None


def claim(request_id: str, path: Path | None = None) -> bool:
    with connect(path) as db:
        cur = db.execute(
            """UPDATE opencode_questions
               SET status='answering', updated_at=?
               WHERE request_id=? AND status='pending'""",
            (_now(), request_id),
        )
        db.commit()
        return cur.rowcount == 1


def finish(
    request_id: str,
    *,
    status: str,
    answers: list[list[str]] | None,
    source: str,
    error: str | None = None,
    path: Path | None = None,
) -> None:
    now = _now()
    with connect(path) as db:
        db.execute(
            """UPDATE opencode_questions
               SET status=?, answers_json=?, answer_source=?, error=?,
                   answered_at=?, updated_at=?
               WHERE request_id=?""",
            (
                status,
                json.dumps(answers, ensure_ascii=False) if answers is not None else None,
                source,
                error,
                now,
                now,
                request_id,
            ),
        )
        db.commit()


def release(request_id: str, error: str, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute(
            """UPDATE opencode_questions
               SET status='pending', error=?, updated_at=?
               WHERE request_id=? AND status='answering'""",
            (error, _now(), request_id),
        )
        db.commit()


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    row["questions"] = json.loads(row.pop("questions_json") or "[]")
    raw_answers = row.pop("answers_json")
    row["answers"] = json.loads(raw_answers) if raw_answers else None
    return row
