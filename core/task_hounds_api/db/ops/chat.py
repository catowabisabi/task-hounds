"""DB ops for chat_messages and user_directives.

These are simple append-and-read tables used by the Chat agent and
the dashboard input box.
"""
from __future__ import annotations

from pathlib import Path
from task_hounds_api.db import connect


# ── chat_messages ────────────────────────────────────────────────────────────

def list_chat(session_id: str, limit: int = 100, path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM chat_messages WHERE session_id=? ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def append_chat(session_id: str, content: str, sender: str = "chat", path: Path | None = None) -> int:
    with connect(path) as db:
        cur = db.execute(
            "INSERT INTO chat_messages (session_id, content, sender, created_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (session_id, content, sender),
        )
        db.commit()
    return int(cur.lastrowid)


# ── user_directives ─────────────────────────────────────────────────────────

def create_directive(session_id: str, directive: str, path: Path | None = None) -> int:
    with connect(path) as db:
        cur = db.execute(
            "INSERT INTO user_directives (session_id, directive, status, created_at, updated_at) VALUES (?, ?, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (session_id, directive),
        )
        db.commit()
    return int(cur.lastrowid)


def get_latest_directive(session_id: str, status: str | None = "pending", path: Path | None = None) -> dict | None:
    with connect(path) as db:
        if status:
            row = db.execute(
                "SELECT * FROM user_directives WHERE session_id=? AND status=? ORDER BY id DESC LIMIT 1",
                (session_id, status),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT * FROM user_directives WHERE session_id=? ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
    return dict(row) if row else None


def mark_directive_status(
    directive_id: int,
    status: str,
    error: str | None = None,
    path: Path | None = None,
) -> None:
    with connect(path) as db:
        if error is not None:
            db.execute(
                "UPDATE user_directives SET status=?, error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, error, directive_id),
            )
        else:
            db.execute(
                "UPDATE user_directives SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, directive_id),
            )
        db.commit()


def mark_directive_processed(directive_id: int, path: Path | None = None) -> None:
    mark_directive_status(directive_id, "processed", error=None, path=path)


def claim_pending_directive(
    session_id: str,
    path: Path | None = None,
) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            "SELECT id FROM user_directives WHERE session_id=? AND status='pending' ORDER BY id ASC LIMIT 1",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        cur = db.execute(
            "UPDATE user_directives SET status='running', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
            (row["id"],),
        )
        if cur.rowcount == 0:
            return None
        updated = db.execute(
            "SELECT * FROM user_directives WHERE id=?",
            (row["id"],),
        ).fetchone()
        db.commit()
    return dict(updated) if updated else None


def list_directives(session_id: str, limit: int = 20, path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM user_directives WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
