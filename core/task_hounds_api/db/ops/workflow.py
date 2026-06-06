"""DB ops for workflow-related tables.

Tables covered:
  session_plan          — current plan text per session
  suggestion_queue      — manager's next step proposals
  worker_reports        — worker execution reports
  manager_messages      — manager message history
  project_handoff       — manager memory/handoff
  workflow_runs         — flow_01 run tracking
  flow_checkpoints      — flow_01 pause/resume state
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from task_hounds_api.db import connect


# ── plan ─────────────────────────────────────────────────────────────────────

def get_plan(session_id: str, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM session_plan WHERE session_id=? ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def set_plan(
    session_id: str,
    content: str,
    updated_by: str = "manager",
    path: Path | None = None,
) -> None:
    with connect(path) as db:
        db.execute(
            """
            INSERT INTO session_plan (session_id, content, updated_by, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id) DO UPDATE SET
                content=excluded.content,
                updated_by=excluded.updated_by,
                updated_at=CURRENT_TIMESTAMP
            """,
            (session_id, content, updated_by),
        )
        db.commit()


# ── suggestion_queue ─────────────────────────────────────────────────────────

def create_suggestion(
    session_id: str,
    content: str,
    verification: str | None = None,
    status: str = "released",
    handoff_version: int | None = None,
    path: Path | None = None,
) -> int:
    with connect(path) as db:
        cur = db.execute(
            """
            INSERT INTO suggestion_queue
                (content, status, verification, handoff_version, session_id, created_at, updated_at, released_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (content, status, verification, handoff_version, session_id),
        )
        db.commit()
    return int(cur.lastrowid)


def get_active_suggestion(session_id: str, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            """
            SELECT * FROM suggestion_queue
             WHERE session_id=? AND status NOT IN ('done','cancelled')
             ORDER BY id DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def update_suggestion_status(suggestion_id: int, status: str, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute(
            "UPDATE suggestion_queue SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, suggestion_id),
        )
        db.commit()


def list_unscoped_suggestions(path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM suggestion_queue WHERE session_id IS NULL OR session_id='' ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── worker_reports ───────────────────────────────────────────────────────────

def append_worker_report(
    session_id: str,
    report: str,
    files_changed: list[str] | None = None,
    test_result: str = "",
    known_issues: list[str] | None = None,
    worker_opencode_session_id: str | None = None,
    path: Path | None = None,
) -> int:
    with connect(path) as db:
        cur = db.execute(
            """
            INSERT INTO worker_reports
                (session_id, worker_opencode_session_id, report,
                 files_changed_json, test_result, known_issues_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                session_id,
                worker_opencode_session_id,
                report,
                json.dumps(files_changed or []),
                test_result,
                json.dumps(known_issues or []),
            ),
        )
        db.commit()
    return int(cur.lastrowid)


def latest_worker_report(session_id: str, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM worker_reports WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("files_changed_json"):
        d["files_changed"] = json.loads(d["files_changed_json"])
    if d.get("known_issues_json"):
        d["known_issues"] = json.loads(d["known_issues_json"])
    return d


def list_worker_reports(session_id: str, limit: int = 50, path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM worker_reports WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("files_changed_json"):
            d["files_changed"] = json.loads(d["files_changed_json"])
        if d.get("known_issues_json"):
            d["known_issues"] = json.loads(d["known_issues_json"])
        out.append(d)
    return out


# ── reviewer_sessions ───────────────────────────────────────────────────────


def create_reviewer_session(
    suggestion_id: int,
    status: str = "pending",
    path: Path | None = None,
) -> int:
    """Insert a new reviewer_sessions row. Returns the new id.

    status: pending | running | completed | failed | needs_review
    The row's started_at is set to CURRENT_TIMESTAMP. completed_at
    stays NULL until update_reviewer_session sets it on completion."""
    with connect(path) as db:
        cur = db.execute(
            """
            INSERT INTO reviewer_sessions
                (suggestion_id, status, started_at, created_at)
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (suggestion_id, status),
        )
        db.commit()
    return int(cur.lastrowid)


def update_reviewer_session(
    reviewer_session_id: int,
    *,
    status: str,
    review_notes: str = "",
    bugs_json: str = "[]",
    style_feedback: str = "",
    scripts_documented: str = "",
    completed: bool = True,
    error: str = "",
    path: Path | None = None,
) -> None:
    """Update a reviewer_sessions row with the final outcome.

    Sets completed_at = CURRENT_TIMESTAMP when completed=True (the
    Reviewer LLM call returned a parseable verdict). On failure
    (status='failed' or 'needs_review'), completed_at is still set
    so the operator can see WHEN the review concluded; error is
    stored for debugging.

    This is the authoritative persistence path for the Reviewer
    outcome. If a row is not found, the call is a silent no-op
    (caller can check by joining on suggestion_id if needed)."""
    with connect(path) as db:
        if completed:
            db.execute(
                """
                UPDATE reviewer_sessions
                SET status=?, review_notes=?, usability_issues=?,
                    style_feedback=?, scripts_documented=?,
                    completed_at=CURRENT_TIMESTAMP, error=?
                WHERE id=?
                """,
                (status, review_notes, bugs_json, style_feedback,
                 scripts_documented, error, reviewer_session_id),
            )
        else:
            db.execute(
                """
                UPDATE reviewer_sessions
                SET status=?, review_notes=?, error=?,
                    completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, review_notes, error, reviewer_session_id),
            )
        db.commit()


def get_latest_reviewer_session(
    session_id: str, path: Path | None = None
) -> dict | None:
    """Return the most recent reviewer_sessions row for the session's
    active suggestion, or None. Joins suggestion_queue to filter by
    session_id. Used by the UI to display the latest Reviewer verdict."""
    with connect(path) as db:
        row = db.execute(
            """
            SELECT rs.id, rs.suggestion_id, rs.status, rs.review_notes,
                   rs.usability_issues, rs.style_feedback, rs.scripts_documented,
                   rs.started_at, rs.completed_at, rs.error
            FROM reviewer_sessions rs
            JOIN suggestion_queue sq ON rs.suggestion_id = sq.id
            WHERE sq.session_id=?
            ORDER BY rs.id DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("usability_issues"):
        try:
            d["usability_issues"] = json.loads(d["usability_issues"])
        except json.JSONDecodeError:
            d["usability_issues"] = []
    return d


# ── manager_messages ────────────────────────────────────────────────────────

def append_manager_message(
    session_id: str,
    content: str,
    path: Path | None = None,
) -> int:
    with connect(path) as db:
        cur = db.execute(
            "INSERT INTO manager_messages (session_id, content, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (session_id, content),
        )
        db.commit()
    return int(cur.lastrowid)


def list_manager_messages(session_id: str, limit: int = 20, path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM manager_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def latest_manager_message(session_id: str, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM manager_messages WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


# ── project_handoff ─────────────────────────────────────────────────────────

def get_handoff(session_id: str, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM project_handoff WHERE session_id=? ORDER BY version DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    for k in ("current_micro_flow", "known_bugs", "completion_criteria", "tested_files"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except (TypeError, ValueError):
                pass
    return d


def upsert_handoff(session_id: str, path: Path | None = None, **fields) -> None:
    """Create or update the handoff row. fields keys: human_requirements, working_direction,
    current_task, current_micro_flow (list), human_concerns, known_bugs (list), completion_criteria (list)."""
    if not fields:
        return
    payload = {}
    for k, v in fields.items():
        if k in ("current_micro_flow", "known_bugs", "completion_criteria", "tested_files") and isinstance(v, list):
            payload[k] = json.dumps(v)
        else:
            payload[k] = v

    with connect(path) as db:
        existing = db.execute(
            "SELECT id FROM project_handoff WHERE session_id=? ORDER BY version DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if existing:
            sets = ", ".join(f"{k}=?" for k in payload)
            values = list(payload.values()) + [existing["id"]]
            db.execute(
                f"UPDATE project_handoff SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                values,
            )
        else:
            cols = ", ".join(payload.keys())
            placeholders = ", ".join("?" for _ in payload)
            values = list(payload.values())
            db.execute(
                f"INSERT INTO project_handoff (session_id, {cols}, updated_by) VALUES (?, {placeholders}, 'manager')",
                [session_id] + values,
            )
        db.commit()


# ── workflow_runs + flow_checkpoints ────────────────────────────────────────

def create_workflow_run(
    session_id: str,
    power_team_project_id: str,
    loop_index: int,
    status: str,
    input_json: str,
    output_json: str,
    manager_session_id: str | None = None,
    worker_session_id: str | None = None,
    reviewer_session_id: str | None = None,
    path: Path | None = None,
) -> int:
    with connect(path) as db:
        cur = db.execute(
            """
            INSERT INTO workflow_runs
                (power_team_project_id, project_session_id, loop_index, status,
                 manager_opencode_session_id, worker_opencode_session_id, reviewer_opencode_session_id,
                 input_json, output_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                power_team_project_id, session_id, loop_index, status,
                manager_session_id, worker_session_id, reviewer_session_id,
                input_json, output_json,
            ),
        )
        db.commit()
    return int(cur.lastrowid)


def get_workflow_run(run_id: int, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_workflow_runs(session_id: str, limit: int = 20, path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM workflow_runs WHERE project_session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def save_checkpoint(
    run_id: int,
    session_id: str,
    power_team_project_id: str,
    step_name: str,
    step_index: int,
    state_json: str,
    path: Path | None = None,
) -> None:
    with connect(path) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO flow_checkpoints
                (power_team_project_id, project_session_id, run_id, step_name, step_index, state_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (power_team_project_id, session_id, run_id, step_name, step_index, state_json),
        )
        db.commit()


def load_checkpoint(run_id: int, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM flow_checkpoints WHERE run_id=? ORDER BY step_index DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    return dict(row) if row else None
