"""
db_skill.py — Task Hounds DB Skill v1

Controlled DB access for agents. Agents MUST NOT directly read/write SQLite.
All access goes through validated skill functions here.

Identity validation:
- role_session_id format: {project_session_id}:{role}
- Backend validates that role_session_id matches the claimed role
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Force UTF-8 on Windows ────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Logging ─────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOG_DIR = PROJECT_ROOT / "core" / "runtime" / "logs"
LOG_FILE = LOG_DIR / "db_skill_errors.log"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_skill_log = logging.getLogger("db_skill")
_skill_log.setLevel(logging.DEBUG)
if not _skill_log.handlers:
    _fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _fh.setLevel(logging.WARNING)
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%%Y-%%m-%%d %%H:%%M:%%S"))
    _skill_log.addHandler(_fh)
    _ch = logging.StreamHandler(sys.stdout)
    _ch.setLevel(logging.INFO)
    _ch.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))
    _skill_log.addHandler(_ch)


# ── DB Path (backend-only, never exposed to agents) ──────────────────────────
_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = _ROOT / "core" / "db"
_DB_PATH = Path(os.environ.get("POWER_TEAMS_DB", str(_DATA_DIR / "power_teams.db")))


# ── Allowlists ────────────────────────────────────────────────────────────────

READABLE_TABLES = frozenset([
    "project_handoff",
    "suggestion_queue",
    "manager_messages",
    "session_plan",
    "session_todos",
    "reviewer_sessions",
    "project_sessions",
    "opencode_server_instances",
])

TABLE_SCOPE_COLUMNS = {
    "project_handoff": "session_id",
    "suggestion_queue": "session_id",
    "manager_messages": "session_id",
    "session_plan": "session_id",
    "session_todos": "session_id",
    "project_sessions": "id",
    "opencode_server_instances": "power_teams_session_id",
}

# role -> set of allowed write operation names
WRITE_OPS: dict[str, frozenset[str]] = {
    "manager": frozenset([
        "append_manager_message",
        "create_suggestion",
        "update_suggestion_status",
        "update_handoff",
        "update_plan",
        "update_todos",
    ]),
    "worker": frozenset([
        "append_worker_report",
        "update_suggestion_status",
        "update_worker_todos",
    ]),
    "reviewer": frozenset([
        "record_reviewer_feedback",
        "create_followup_suggestion",
        "update_reviewer_session",
    ]),
    "chat": frozenset([
        "append_chat_message",
        "create_user_directive",
        "update_chat_todos",
    ]),
}

ALL_WRITE_OPS = frozenset().union(*WRITE_OPS.values())


# ── Internal helpers ──────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(_DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_agent_error(role: str, error_msg: str) -> None:
    """Set agent state to error and record last_error."""
    short = error_msg[:500]
    try:
        with _connect() as db:
            try:
                db.execute(
                    "UPDATE agent_registry SET state='error', last_error=?, updated_at=CURRENT_TIMESTAMP WHERE role=?",
                    (short, role),
                )
            except sqlite3.OperationalError:
                db.execute(
                    "UPDATE agent_registry SET state='error', updated_at=CURRENT_TIMESTAMP WHERE role=?",
                    (role,),
                )
            db.commit()
    except Exception as exc:
        _skill_log.error("Failed to set agent error state for %s: %s", role, exc)


def _log_error(kind: str, role: str, project_session_id: str, role_session_id: str, operation: str, detail: str) -> None:
    msg = f"[{kind}] role={role} project={project_session_id} role_session={role_session_id} op={operation} — {detail}"
    _skill_log.error(msg)


def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    try:
        result = db.execute(f"PRAGMA table_info({table})")
        if result is None:
            return set()
        return {row[1] for row in result.fetchall()}
    except Exception:
        return set()


def _order_clause(db: sqlite3.Connection, table: str) -> str:
    cols = _table_columns(db, table)
    if "id" in cols:
        return " ORDER BY id DESC"
    if "updated_at" in cols:
        return " ORDER BY updated_at DESC"
    if "created_at" in cols:
        return " ORDER BY created_at DESC"
    return ""


# ── Core skill functions ──────────────────────────────────────────────────────

def validate_identity(project_session_id: str, role: str, role_session_id: str) -> tuple[bool, str]:
    """
    Validate that role_session_id matches {project_session_id}:{role}.

    Returns (ok, error_message).
    """
    expected = f"{project_session_id}:{role}"
    if role_session_id != expected:
        msg = f"Identity mismatch: got '{role_session_id}', expected '{expected}'"
        _log_error("VALIDATE_FAIL", role, project_session_id, role_session_id, "validate_identity", msg)
        _set_agent_error(role, f"Identity validation failed: {msg}")
        return False, msg
    return True, ""


def read_project_context(project_session_id: str, role: str, role_session_id: str) -> dict[str, Any]:
    """
    Read project context: latest handoff, active suggestion, manager messages.
    Session-scoped — only returns data for the given project_session_id.
    """
    ok, err = validate_identity(project_session_id, role, role_session_id)
    if not ok:
        return {"ok": False, "error": {"type": "IdentityError", "message": err}}

    try:
        with _connect() as db:
            # Latest handoff for this project
            handoff = db.execute(
                "SELECT * FROM project_handoff WHERE session_id=? ORDER BY version DESC LIMIT 1",
                (project_session_id,)
            ).fetchone()

            # Active suggestion for this project
            suggestion = db.execute(
                "SELECT * FROM suggestion_queue WHERE session_id=? AND status NOT IN ('done','cancelled') ORDER BY id DESC LIMIT 1",
                (project_session_id,)
            ).fetchone()

            # Recent manager messages
            messages = db.execute(
                "SELECT id, content, created_at FROM manager_messages WHERE session_id=? ORDER BY id DESC LIMIT 10",
                (project_session_id,)
            ).fetchall()

            # Plan and todos for this project session
            plan_row = db.execute(
                "SELECT * FROM session_plan WHERE session_id=? ORDER BY updated_at DESC LIMIT 1",
                (project_session_id,)
            ).fetchone()
            todos_rows = db.execute(
                "SELECT * FROM session_todos WHERE session_id=? ORDER BY id DESC LIMIT 50",
                (project_session_id,)
            ).fetchall()

        return {
            "ok": True,
            "data": {
                "handoff": dict(handoff) if handoff else None,
                "active_suggestion": dict(suggestion) if suggestion else None,
                "manager_messages": [dict(r) for r in messages],
                "session_plan": dict(plan_row) if plan_row else None,
                "session_todos": [dict(r) for r in todos_rows],
            }
        }
    except Exception as exc:
        _log_error("READ_ERROR", role, project_session_id, role_session_id, "read_project_context", str(exc))
        _set_agent_error(role, f"read_project_context failed: {exc}")
        return {"ok": False, "error": {"type": "ReadError", "message": str(exc)}}


def read_table(
    project_session_id: str,
    role: str,
    role_session_id: str,
    table: str,
    filters: dict[str, Any] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """
    Read a table with session_id scoping applied automatically.
    Only tables in READABLE_TABLES are allowed.
    """
    ok, err = validate_identity(project_session_id, role, role_session_id)
    if not ok:
        return {"ok": False, "error": {"type": "IdentityError", "message": err}}

    if table not in READABLE_TABLES:
        msg = f"Table '{table}' not in READABLE_TABLES allowlist"
        _log_error("READ_DENY", role, project_session_id, role_session_id, f"read_table:{table}", msg)
        _set_agent_error(role, f"read_table denied: {table}")
        return {"ok": False, "error": {"type": "PermissionError", "message": msg}}

    limit = max(1, min(limit, 200))

    try:
        with _connect() as db:
            session_filter = filters.copy() if filters else {}
            scope_col = TABLE_SCOPE_COLUMNS.get(table)
            if scope_col:
                session_filter[scope_col] = project_session_id

            if table == "reviewer_sessions":
                suggestion_ids = [
                    row["id"] for row in db.execute(
                        "SELECT id FROM suggestion_queue WHERE session_id=?",
                        (project_session_id,),
                    ).fetchall()
                ]
                if not suggestion_ids:
                    return {"ok": True, "data": []}
                placeholders = ", ".join("?" for _ in suggestion_ids)
                extra_filters = session_filter.copy()
                extra_filters.pop("session_id", None)
                where_parts = [f"suggestion_id IN ({placeholders})"] + [f"{k}=?" for k in extra_filters]
                values = suggestion_ids + list(extra_filters.values()) + [limit]
                rows = db.execute(
                    f"SELECT * FROM reviewer_sessions WHERE {' AND '.join(where_parts)}{_order_clause(db, table)} LIMIT ?",
                    values,
                ).fetchall()
                return {"ok": True, "data": [dict(r) for r in rows]}

            where_parts = [f"{k}=?" for k in session_filter]
            where_clause = " AND ".join(where_parts) if where_parts else "1=1"
            values = list(session_filter.values()) + [limit]

            rows = db.execute(
                f"SELECT * FROM {table} WHERE {where_clause}{_order_clause(db, table)} LIMIT ?",
                values,
            ).fetchall()

        return {"ok": True, "data": [dict(r) for r in rows]}
    except Exception as exc:
        _log_error("READ_ERROR", role, project_session_id, role_session_id, f"read_table:{table}", str(exc))
        _set_agent_error(role, f"read_table failed: {exc}")
        return {"ok": False, "error": {"type": "ReadError", "message": str(exc)}}


def write_operation(
    project_session_id: str,
    role: str,
    role_session_id: str,
    operation: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute a role-specific allowed write operation.
    session_id is always enforced to project_session_id.
    """
    ok, err = validate_identity(project_session_id, role, role_session_id)
    if not ok:
        return {"ok": False, "error": {"type": "IdentityError", "message": err}}

    with _connect() as db:
        arch = db.execute("SELECT session_key FROM sessions_arch").fetchall()
        archived_keys = {row["session_key"] for row in arch}
        session_key = f"{project_session_id}:{role}"
        if session_key in archived_keys:
            raise PermissionError(f"session {session_key} is archived")

    allowed = WRITE_OPS.get(role, frozenset())
    if operation not in allowed:
        msg = f"Operation '{operation}' not allowed for role '{role}'. Allowed: {sorted(allowed)}"
        _log_error("WRITE_DENY", role, project_session_id, role_session_id, operation, msg)
        _set_agent_error(role, f"write_operation denied: {operation}")
        return {"ok": False, "error": {"type": "PermissionError", "message": msg}}

    try:
        result = _execute_operation(role, project_session_id, operation, payload)
        return {"ok": True, "data": result}
    except PermissionError as exc:
        _log_error("WRITE_DENY", role, project_session_id, role_session_id, operation, str(exc))
        _set_agent_error(role, f"write_operation denied: {operation}: {exc}")
        return {"ok": False, "error": {"type": "PermissionError", "message": str(exc)}}
    except Exception as exc:
        _log_error("WRITE_ERROR", role, project_session_id, role_session_id, operation, str(exc))
        _set_agent_error(role, f"write_operation failed: {operation}: {exc}")
        return {"ok": False, "error": {"type": "WriteError", "message": str(exc)}}


def _execute_operation(role: str, project_session_id: str, operation: str, payload: dict[str, Any]) -> Any:
    """Dispatch to the correct DB write function."""
    import json as _json

    with _connect() as db:
        archived = db.execute(
            "SELECT 1 FROM sessions_arch WHERE session_key=? LIMIT 1",
            (project_session_id,),
        ).fetchone()
        if archived:
            raise PermissionError(f"session {project_session_id} is archived, writes not accepted")

        if operation == "append_manager_message":
            content = payload.get("content", "")
            cur = db.execute(
                "INSERT INTO manager_messages (content, session_id) VALUES (?, ?)",
                (content, project_session_id),
            )
            db.commit()
            return {"id": cur.lastrowid}

        elif operation == "create_suggestion":
            content = payload.get("content", "")
            verification = payload.get("verification")
            handoff_version = payload.get("handoff_version")
            cur = db.execute(
                "INSERT INTO suggestion_queue (content, status, verification, handoff_version, session_id) "
                "VALUES (?, 'released', ?, ?, ?)",
                (content, verification, handoff_version, project_session_id),
            )
            db.commit()
            return {"id": cur.lastrowid}

        elif operation == "update_suggestion_status":
            suggestion_id = int(payload["suggestion_id"])
            new_status = payload["status"]
            db.execute(
                "UPDATE suggestion_queue SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND session_id=?",
                (new_status, suggestion_id, project_session_id),
            )
            db.commit()
            return {"updated": suggestion_id}

        elif operation == "update_handoff":
            handoff_id = payload.get("id")
            if handoff_id:
                updates = {k: v for k, v in payload.items() if k in (
                    "human_requirements", "working_direction", "macro_flow",
                    "current_task", "current_micro_flow", "human_concerns",
                )}
                if updates:
                    sets = ", ".join(f"{k}=?" for k in updates)
                    vals = list(updates.values()) + [handoff_id, project_session_id]
                    db.execute(
                        f"UPDATE project_handoff SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=? AND session_id=?",
                        vals,
                    )
                    db.commit()
            return {"updated_handoff": handoff_id}

        elif operation == "update_plan":
            plan_text = payload.get("plan", "")
            plan_row = db.execute(
                "SELECT session_id FROM session_plan WHERE session_id=? LIMIT 1",
                (project_session_id,)
            ).fetchone()
            if plan_row:
                db.execute(
                    "UPDATE session_plan SET content=?, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE session_id=?",
                    (plan_text, role, project_session_id),
                )
            else:
                db.execute(
                    "INSERT INTO session_plan (session_id, content, updated_by) VALUES (?, ?, ?)",
                    (project_session_id, plan_text, role),
                )
            db.commit()
            return {"plan_updated": True}

        elif operation == "update_todos":
            todos = payload.get("todos", [])
            for pos, item in enumerate(todos):
                item_id = item.get("id") or str(uuid.uuid4())
                db.execute(
                    """INSERT INTO session_todos
                         (id, session_id, parent_id, content, status, priority, position, owner)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         content=excluded.content,
                         status=excluded.status,
                         priority=excluded.priority,
                         position=excluded.position,
                         owner=excluded.owner,
                         updated_at=CURRENT_TIMESTAMP""",
                    (
                        item_id,
                        project_session_id,
                        item.get("parent_id"),
                        item.get("content", ""),
                        item.get("status", "pending"),
                        item.get("priority", "medium"),
                        item.get("position", pos),
                        item.get("owner", role),
                    ),
                )
            db.commit()
            return {"todos_updated": len(todos)}

        elif operation == "append_worker_report":
            report = payload.get("report", "")
            db.execute(
                "INSERT INTO worker_reports (session_id, report) VALUES (?, ?)",
                (project_session_id, report),
            )
            db.commit()
            return {"report_saved": True}

        elif operation == "update_worker_todos":
            # Worker updates its own todo items
            todos = payload.get("todos", [])
            for item in todos:
                if "id" in item:
                    db.execute(
                        "UPDATE session_todos SET status=? WHERE id=? AND session_id=?",
                        (item.get("status", "pending"), item["id"], project_session_id),
                    )
            db.commit()
            return {"worker_todos_updated": len(todos)}

        elif operation == "record_reviewer_feedback":
            suggestion_id = int(payload["suggestion_id"])
            feedback = payload.get("feedback", {})
            cur = db.execute(
                "UPDATE suggestion_queue SET status='done' WHERE id=? AND session_id=?",
                (suggestion_id, project_session_id),
            )
            if cur.rowcount == 0:
                raise PermissionError(
                    f"suggestion {suggestion_id} is not owned by project_session_id {project_session_id}"
                )
            session_row = db.execute(
                "SELECT id FROM reviewer_sessions WHERE suggestion_id=? ORDER BY id DESC LIMIT 1",
                (suggestion_id,),
            ).fetchone()
            if session_row:
                db.execute(
                    """UPDATE reviewer_sessions
                       SET status='completed',
                           review_notes=?,
                           usability_issues=?,
                           style_feedback=?,
                           scripts_documented=?,
                           completed_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (
                        feedback.get("review_notes", ""),
                        feedback.get("usability_issues"),
                        feedback.get("style_feedback"),
                        feedback.get("scripts_documented"),
                        session_row["id"],
                    ),
                )
            db.commit()
            return {"feedback_recorded": suggestion_id}

        elif operation == "create_followup_suggestion":
            content = payload.get("content", "")
            verification = payload.get("verification", "")
            cur = db.execute(
                "INSERT INTO suggestion_queue (content, status, verification, session_id) "
                "VALUES (?, 'pending', ?, ?)",
                (content, verification, project_session_id),
            )
            db.commit()
            return {"followup_id": cur.lastrowid}

        elif operation == "update_reviewer_session":
            session_id = int(payload["session_id"])
            status = payload.get("status", "completed")
            notes = payload.get("review_notes", "")
            owned = db.execute(
                """SELECT 1 FROM reviewer_sessions rs
                   JOIN suggestion_queue sq ON rs.suggestion_id = sq.id
                   WHERE rs.id=? AND sq.session_id=?""",
                (session_id, project_session_id),
            ).fetchone()
            if not owned:
                raise PermissionError(
                    f"reviewer_session {session_id} is not owned by project_session_id {project_session_id}"
                )
            db.execute(
                "UPDATE reviewer_sessions SET status=?, review_notes=? WHERE id=?",
                (status, notes, session_id),
            )
            db.commit()
            return {"reviewer_session_updated": session_id}

        elif operation == "append_chat_message":
            content = payload.get("content", "")
            sender = payload.get("sender", "chat")
            cur = db.execute(
                "INSERT INTO chat_messages (session_id, content, sender) VALUES (?, ?, ?)",
                (project_session_id, content, sender),
            )
            db.commit()
            return {"chat_message_id": cur.lastrowid}

        elif operation == "create_user_directive":
            directive = payload.get("directive", "")
            cur = db.execute(
                "INSERT INTO user_directives (session_id, directive, status) VALUES (?, ?, 'pending')",
                (project_session_id, directive),
            )
            db.commit()
            return {"directive_id": cur.lastrowid}

        elif operation == "update_chat_todos":
            todos = payload.get("todos", [])
            for item in todos:
                if "id" in item:
                    db.execute(
                        "UPDATE session_todos SET status=? WHERE id=? AND session_id=?",
                        (item.get("status", "pending"), item["id"], project_session_id),
                    )
            db.commit()
            return {"chat_todos_updated": len(todos)}

        else:
            raise ValueError(f"Unknown operation: {operation}")
