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


def append_chat(
    session_id: str,
    content: str,
    sender: str = "chat",
    path: Path | None = None,
    directive_proposal: str | None = None,
) -> int:
    with connect(path) as db:
        cur = db.execute(
            """INSERT INTO chat_messages
               (session_id, content, sender, directive_proposal, proposal_status, created_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                session_id,
                content,
                sender,
                directive_proposal,
                "proposed" if directive_proposal else None,
            ),
        )
        db.commit()
    return int(cur.lastrowid)


def accept_directive_proposal(
    session_id: str,
    message_id: int,
    path: Path | None = None,
) -> dict:
    """Claim a Chat proposal and save it as Task Hounds directive state."""
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            """SELECT directive_proposal, proposal_status
                 FROM chat_messages
                WHERE id=? AND session_id=? AND sender='chat'""",
            (message_id, session_id),
        ).fetchone()
        if not row or not str(row["directive_proposal"] or "").strip():
            db.rollback()
            raise ValueError("Chat message has no Human Directive proposal")
        if row["proposal_status"] == "saved":
            db.rollback()
            current = get_latest_directive(session_id, status=None, path=path)
            return {
                "already_saved": True,
                "directive_id": current.get("id") if current else None,
            }
        updated = db.execute(
            """UPDATE chat_messages
                  SET proposal_status='saving'
                WHERE id=? AND session_id=? AND proposal_status='proposed'""",
            (message_id, session_id),
        )
        if updated.rowcount != 1:
            db.rollback()
            raise ValueError("Human Directive proposal is no longer available")
        proposal = str(row["directive_proposal"]).strip()
        db.commit()

    try:
        saved = save_user_directive(session_id, proposal, path)
    except Exception:
        with connect(path) as db:
            db.execute(
                """UPDATE chat_messages SET proposal_status='proposed'
                   WHERE id=? AND session_id=? AND proposal_status='saving'""",
                (message_id, session_id),
            )
            db.commit()
        raise

    with connect(path) as db:
        db.execute(
            """UPDATE chat_messages SET proposal_status='saved'
               WHERE id=? AND session_id=?""",
            (message_id, session_id),
        )
        db.commit()
    return {**saved, "already_saved": False}


# ── user_directives ─────────────────────────────────────────────────────────

def create_directive(session_id: str, directive: str, path: Path | None = None) -> int:
    import logging
    logger = logging.getLogger(__name__)
    from task_hounds_api.db import _resolve_db_path
    resolved = _resolve_db_path(path)
    logger.warning(f"[CREATE-DIRECTIVE] session_id={session_id}, directive_len={len(directive)}, db_path={resolved}")
    from task_hounds_api.db.ops.rounds import active_round_id
    round_id = active_round_id(session_id, path)
    with connect(path) as db:
        cur = db.execute(
            "INSERT INTO user_directives (session_id, directive, status, round_id, created_at, updated_at) VALUES (?, ?, 'pending', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (session_id, directive, round_id),
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


def replace_latest_directive(session_id: str, directive: str, path: Path | None = None) -> int:
    """Amend the visible directive without creating a new pending loop item."""
    with connect(path) as db:
        row = db.execute(
            "SELECT id FROM user_directives WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row:
            db.execute(
                "UPDATE user_directives SET directive=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (directive, row["id"]),
            )
            db.commit()
            return int(row["id"])
    return create_directive(session_id, directive, path)


def save_user_directive(
    session_id: str,
    directive: str,
    path: Path | None = None,
) -> dict:
    """Save the one authoritative pending directive for the active round."""
    from task_hounds_api.db.ops import rounds as db_rounds

    text = (directive or "").strip()
    current = db_rounds.current_round(session_id, path)
    created_round = False
    if current and current.get("status") == "locked" and text:
        if text != str(current.get("directive") or "").strip():
            current = db_rounds.create_next_round(session_id, text, path)
            created_round = True

    round_id = (
        str(current["id"])
        if current and current.get("status") == "active"
        else None
    )
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """UPDATE user_directives
                  SET status='superseded', updated_at=CURRENT_TIMESTAMP
                WHERE session_id=? AND status='pending'""",
            (session_id,),
        )
        if round_id:
            db.execute(
                "UPDATE project_rounds SET directive=? WHERE id=? AND status='active'",
                (text, round_id),
            )
        cur = db.execute(
            """INSERT INTO user_directives
               (session_id, directive, status, round_id, created_at, updated_at)
               VALUES (?, ?, 'pending', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (session_id, text, round_id),
        )
        db.commit()
    return {
        "id": int(cur.lastrowid),
        "round_id": round_id,
        "round_number": current.get("round_number") if current else None,
        "created_round": created_round,
    }


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
