"""DB ops for workflow-related tables.

Tables covered:
  session_plan          — current plan text per session
  suggestion_queue      — manager's next step proposals
  worker_reports        — worker execution reports
  manager_messages      — manager message history
  project_handoff       — manager memory/handoff
  workflow_runs         — flow_01 run tracking
  flow_checkpoints      — flow_01 pause/resume state
  reviewer_issues       — reviewer findings (bugs, ui_ux, risks)
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


def update_suggestion(
    suggestion_id: int,
    path: Path | None = None,
    **fields,
) -> bool:
    """P7 id 212 + 335: legacy update_suggestion helper.

    Updates an existing suggestion by id, with any subset of
    {content, status, verification, related_files}. Returns
    True if a row was updated. The legacy 0c44ba2 helper
    accepted these plus `notes` (omitted here — notes is not
    a column in the new schema)."""
    if not fields:
        return False
    keys = list(fields)
    sets = ", ".join(f"{k}=?" for k in keys) + ", updated_at=CURRENT_TIMESTAMP"
    values = [fields[k] for k in keys] + [suggestion_id]
    with connect(path) as db:
        cur = db.execute(
            f"UPDATE suggestion_queue SET {sets} WHERE id=?",
            values,
        )
        db.commit()
    return cur.rowcount > 0


def get_suggestion(suggestion_id: int, path: Path | None = None) -> dict | None:
    """P7 id 212: fetch a single suggestion by id.

    Returns the suggestion row or None if the id is not
    found. Mirrors the legacy 0c44ba2 get_suggestion (used
    in PATCH /api/suggestion response assembly)."""
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM suggestion_queue WHERE id=?",
            (suggestion_id,),
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
    for k in (
        "current_task",
        "working_direction",
        "human_requirements",
        "references_demos",
        "file_structure",
        "important_files",
        "available_scripts",
        "existing_solutions",
        "macro_flow",
        "human_concerns",
        "project_folder_location",
        "updated_by",
    ):
        if d.get(k) is None:
            d[k] = ""
    for k in ("current_micro_flow", "known_bugs", "completion_criteria", "tested_files"):
        value = d.get(k)
        if not value:
            d[k] = []
            continue
        if isinstance(value, list):
            continue
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = value
        if isinstance(parsed, list):
            d[k] = [str(item) for item in parsed if item is not None]
        elif isinstance(parsed, str):
            d[k] = [parsed] if parsed.strip() else []
        else:
            d[k] = [str(parsed)]
    return d


HANDOFF_FIELDS: frozenset[str] = frozenset(
    {
        "human_requirements",
        "working_direction",
        "references_demos",
        "file_structure",
        "important_files",
        "available_scripts",
        "existing_solutions",
        "macro_flow",
        "current_task",
        "current_micro_flow",
        "human_concerns",
        "tested_files",
        "known_bugs",
        "completion_criteria",
        "project_folder_location",
        "updated_by",
    }
)


def upsert_handoff(session_id: str, path: Path | None = None, **fields) -> None:
    """Create or update the handoff row. fields keys: human_requirements, working_direction,
    current_task, current_micro_flow (list), human_concerns, known_bugs (list), completion_criteria (list)."""
    if not fields:
        return
    updated_by = str(fields.pop("updated_by", None) or "manager")
    payload = {}
    for k, v in fields.items():
        if k not in HANDOFF_FIELDS:
            continue
        if k in ("current_micro_flow", "known_bugs", "completion_criteria", "tested_files") and isinstance(v, list):
            payload[k] = json.dumps(v)
        else:
            payload[k] = v
    if not payload:
        return

    from task_hounds_api.db.ops.rounds import active_round_id
    round_id = active_round_id(session_id, path)
    with connect(path) as db:
        existing = db.execute(
            "SELECT id FROM project_handoff WHERE session_id=? AND round_id IS ? ORDER BY version DESC LIMIT 1",
            (session_id, round_id),
        ).fetchone()
        if existing:
            sets = ", ".join(f"{k}=?" for k in payload)
            if sets:
                sets = f"{sets}, updated_by=?"
                values = list(payload.values()) + [updated_by, existing["id"]]
            else:
                sets = "updated_by=?"
                values = [updated_by, existing["id"]]
            db.execute(
                f"UPDATE project_handoff SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                values,
            )
        else:
            cols = ", ".join(payload.keys())
            placeholders = ", ".join("?" for _ in payload)
            values = list(payload.values())
            db.execute(
                f"INSERT INTO project_handoff (session_id, round_id, {cols}, updated_by) VALUES (?, ?, {placeholders}, ?)",
                [session_id, round_id] + values + [updated_by],
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
    from task_hounds_api.db.ops.rounds import active_round_id
    round_id = active_round_id(session_id, path)
    with connect(path) as db:
        cur = db.execute(
            """
            INSERT INTO workflow_runs
                (power_team_project_id, project_session_id, loop_index, status,
                 manager_opencode_session_id, worker_opencode_session_id, reviewer_opencode_session_id,
                 input_json, output_json, round_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                power_team_project_id, session_id, loop_index, status,
                manager_session_id, worker_session_id, reviewer_session_id,
                input_json, output_json, round_id,
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


def update_workflow_run_status(
    run_id: int,
    status: str,
    output_json: str | None = None,
    path: Path | None = None,
) -> bool:
    """Update a workflow_runs row's status (and optionally output_json).

    Returns True if a row was updated, False if the run_id was not found.
    The caller (API route) is expected to translate the boolean into
    a 404 vs 200 response. Status is opaque text (the audit's tA4c
    lifecycle uses: pending, running, completed, failed, stopping,
    stopped, cancelled) — the API layer enforces which transitions
    are legal; the DB just stores the value.
    """
    from task_hounds_api.db.write_queue import write

    def perform() -> bool:
        if output_json is not None:
            with connect(path) as db:
                cur = db.execute(
                    "UPDATE workflow_runs SET status=?, output_json=? WHERE id=?",
                    (status, output_json, run_id),
                )
                db.commit()
            return cur.rowcount > 0
        with connect(path) as db:
            cur = db.execute(
                "UPDATE workflow_runs SET status=? WHERE id=?",
                (status, run_id),
            )
            db.commit()
        return cur.rowcount > 0

    return write(perform, priority=10)


def normalize_stale_statuses(path: Path | None = None) -> dict[str, int]:
    """Repair statuses that cannot survive a backend process restart."""
    with connect(path) as db:
        orphaned_runs = db.execute(
            """
            UPDATE workflow_runs
            SET status='technical_error',
                output_json=json_object(
                    'status', 'technical_error',
                    'interruption', json_object(
                        'kind', 'orphaned_run',
                        'title', 'GraphFlow was interrupted',
                        'reason', 'The backend restarted while this run was active.',
                        'source', 'process_lifecycle',
                        'resumable', json(
                            CASE WHEN EXISTS(
                                SELECT 1 FROM flow_checkpoints cp
                                WHERE cp.run_id=workflow_runs.id
                            ) THEN 'true' ELSE 'false' END
                        )
                    )
                )
            WHERE status IN ('running', 'stopping', 'cancelling', 'pausing')
              AND NOT EXISTS (
                  SELECT 1 FROM graphflow_jobs job
                   WHERE job.run_id=workflow_runs.id
                     AND job.status IN ('queued', 'running')
              )
            """
        ).rowcount
        stale_reviewers = db.execute(
            """
            UPDATE reviewer_sessions
            SET status='needs_review',
                error=COALESCE(error, 'reviewer interrupted by backend restart'),
                completed_at=COALESCE(completed_at, CURRENT_TIMESTAMP)
            WHERE status='running'
              AND NOT EXISTS (
                  SELECT 1 FROM agent_execution_state aes
                  JOIN graphflow_jobs job ON job.run_id=aes.workflow_run_id
                  WHERE aes.role='reviewer'
                    AND job.status IN ('queued', 'running')
              )
            """
        ).rowcount
        stale_executions = db.execute(
            """UPDATE agent_execution_state
               SET status='interrupted',
                   error=COALESCE(error, 'backend restarted during execution'),
                   process_id=NULL,
                   updated_at=CURRENT_TIMESTAMP
               WHERE status IN ('queued', 'busy', 'running')
                 AND NOT EXISTS (
                     SELECT 1 FROM graphflow_jobs job
                      WHERE job.run_id=agent_execution_state.workflow_run_id
                        AND job.status IN ('queued', 'running')
                 )"""
        ).rowcount
        reset_completed = db.execute(
            """
            UPDATE session_todos
            SET status='pending'
            WHERE is_active=1 AND status='completed' AND (
                worker_task_status IN ('skipped', 'error')
                OR reviewer_task_status IN ('fail', 'needs_review', 'skipped', 'error')
                OR (
                    attempt_count=0
                    AND worker_task_status='pending'
                    AND reviewer_task_status='pending'
                )
            )
            """
        ).rowcount
        attention_required = db.execute(
            """
            UPDATE session_todos
            SET human_attention_status='attention_required'
            WHERE is_active=1 AND status IN ('pending', 'in_progress')
              AND attempt_count >= 3
              AND human_attention_status='none'
            """
        ).rowcount
        db.commit()
    return {
        "orphaned_runs": orphaned_runs,
        "stale_reviewers": stale_reviewers,
        "stale_executions": stale_executions,
        "reset_completed_todos": reset_completed,
        "attention_required_todos": attention_required,
    }


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


def get_checkpoint(cp_id: int, path: Path | None = None) -> dict | None:
    """Load a single flow_checkpoints row by its id.

    Used by compat_resume_checkpoint(cp_id) to resume from a specific
    checkpoint rather than the latest one for a run.
    """
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM flow_checkpoints WHERE id=?",
            (cp_id,),
        ).fetchone()
    return dict(row) if row else None


def list_checkpoints_for_session(
    session_id: str, limit: int = 50, path: Path | None = None,
) -> list[dict]:
    """All checkpoints for a project session, newest first.

    Joins on workflow_runs so the caller doesn't need to chase
    run_id separately. Used by /api/runtime/checkpoints.

    P10 id 119: excludes archived rows (archived_at IS NULL) to
    match the legacy delete_checkpoint soft-delete contract.
    """
    with connect(path) as db:
        rows = db.execute(
            """
            SELECT cp.id, cp.run_id, cp.step_name, cp.step_index, cp.created_at,
                   cp.archived_at,
                   wr.loop_index, wr.status as run_status
              FROM flow_checkpoints cp
              JOIN workflow_runs wr ON wr.id = cp.run_id
             WHERE cp.project_session_id = ?
               AND cp.archived_at IS NULL
              ORDER BY cp.id DESC
              LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def archive_checkpoint(cp_id: int, path: Path | None = None) -> bool:
    """Mark a flow_checkpoint as archived (sets archived_at).

    Migration audit id 267: the compat route was a stub returning
    success without touching the DB. This restores the side effect.
    Returns True if a row was updated, False if the id was not found.
    Idempotent: re-archiving a checkpoint updates the timestamp
    (does not error).
    """
    with connect(path) as db:
        cur = db.execute(
            "UPDATE flow_checkpoints SET archived_at=CURRENT_TIMESTAMP WHERE id=?",
            (cp_id,),
        )
        db.commit()
    return cur.rowcount > 0


def has_active_work(session_id: str | None, path: Path | None = None) -> bool:
    """True if the active session has a pending OR running directive.

    Migration audit id 262: the compat route was a stub always
    returning False. This restores the DB-backed check by looking
    at the user_directives table for the active session. Returns
    False if session_id is None or empty.
    """
    if not session_id:
        return False
    with connect(path) as db:
        row = db.execute(
            "SELECT 1 FROM user_directives WHERE session_id=? "
            "AND status IN ('pending','running') LIMIT 1",
            (session_id,),
        ).fetchone()
    return row is not None


def list_checkpoints_for_session_including_archived(
    session_id: str, limit: int = 50, path: Path | None = None,
) -> list[dict]:
    """All checkpoints for a project session, newest first, including
    archived. Same as list_checkpoints_for_session but does not
    filter on archived_at. Used by /api/runtime/checkpoints to
    match the legacy contract that returned archived rows too."""
    with connect(path) as db:
        rows = db.execute(
            """
            SELECT cp.id, cp.run_id, cp.step_name, cp.step_index, cp.created_at,
                   cp.archived_at,
                   wr.loop_index, wr.status as run_status
              FROM flow_checkpoints cp
              JOIN workflow_runs wr ON wr.id = cp.run_id
              WHERE cp.project_session_id = ?
              ORDER BY cp.id DESC
              LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── session reset ────────────────────────────────────────────────────────────

def reset_session(session_id: str, path: Path | None = None) -> dict:
    """Reset a session: clear all workflow/runtime state while keeping the
    project and workspace intact.

    Scope:
      - Stops the background loop (if running) and kills all agent runs.
      - Clears: manager_messages, worker_reports, chat_messages,
        session_todos, suggestion_queue, session_plan,
        project_handoff, reviewer_sessions, flow_checkpoints,
        workflow_runs.
      - Nulls out opencode_session_id in project_session_role_sessions.
      - Resets all agents linked to this session to state=idle.

    Kept intact: project_sessions (the session itself),
    user_directives (the directive is the session mission).
    """
    from task_hounds_api.db.ops import todo as db_todo
    from task_hounds_api.db.ops import agent as db_agent

    with connect(path) as db:
        db.execute("DELETE FROM manager_messages WHERE session_id=?", (session_id,))
        db.execute("DELETE FROM worker_reports WHERE session_id=?", (session_id,))
        db.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
        db.execute("DELETE FROM session_plan WHERE session_id=?", (session_id,))
        db.execute("DELETE FROM project_handoff WHERE session_id=?", (session_id,))
        db.execute(
            "DELETE FROM reviewer_sessions WHERE suggestion_id IN "
            "(SELECT id FROM suggestion_queue WHERE session_id=?)",
            (session_id,),
        )
        db.execute("DELETE FROM suggestion_queue WHERE session_id=?", (session_id,))
        db.execute(
            "DELETE FROM flow_checkpoints WHERE project_session_id=?",
            (session_id,),
        )
        db.execute(
            "DELETE FROM workflow_runs WHERE project_session_id=?",
            (session_id,),
        )
        db.execute(
            "UPDATE project_session_role_sessions "
            "SET opencode_session_id=NULL, updated_at=CURRENT_TIMESTAMP "
            "WHERE project_session_id=?",
            (session_id,),
        )
        db.commit()

    db_todo.delete_session_todos(session_id, path)

    with connect(path) as db:
        rows = db.execute(
            "SELECT name FROM agent_registry "
            "WHERE project_session_id=? OR role_session_id LIKE ?",
            (session_id, session_id + ":%"),
        ).fetchall()
    for row in rows:
        db_agent.update_agent(
            row["name"],
            state="idle",
            last_error=None,
            current_step=None,
            current_step_started_at=None,
            project_session_id=None,
            role_session_id=None,
            path=path,
        )

    return {"session_id": session_id, "reset": True}


def save_runtime_checkpoint(
    project_session_id: str,
    reason: str,
    notes: str = "",
    workspace_path: str | None = None,
    workspace_id: str | None = None,
    path: Path | None = None,
) -> int:
    """Persist a full runtime checkpoint to run_checkpoints.

    Captures the current state of all agent sessions (manager, worker,
    reviewer, chat), the agent registry, opencode servers, runtime
    bindings, todos, and plan — providing a restart/resume point for
    the entire OpenCode lifecycle.

    Used by POST /api/runtime/checkpoint (compat layer, ID 264).
    """
    import json

    with connect(path) as db:
        role_rows = db.execute(
            """
            SELECT role, opencode_session_id, server_instance_id, workspace_path
              FROM project_session_role_sessions
             WHERE project_session_id=?
            """,
            (project_session_id,),
        ).fetchall()
        role_state = {r["role"]: dict(r) for r in role_rows}

        def _role_json(role: str) -> str | None:
            state = role_state.get(role)
            return json.dumps(state) if state else None

        manager_state = _role_json("manager")
        worker_state = _role_json("worker")
        reviewer_state = _role_json("reviewer")
        chat_state = _role_json("chat")

        # Snapshot agent registry.
        agent_rows = db.execute("SELECT * FROM agent_registry").fetchall()
        agent_registry_snapshot = json.dumps([dict(r) for r in agent_rows]) if agent_rows else "[]"

        # Snapshot todos for the session.
        todo_rows = db.execute(
            "SELECT * FROM session_todos WHERE session_id=?", (project_session_id,)
        ).fetchall()
        todos_snapshot = json.dumps([dict(r) for r in todo_rows]) if todo_rows else "[]"

        # Snapshot runtime bindings.
        binding_rows = db.execute("SELECT * FROM agent_runtime_bindings").fetchall()
        bindings_snapshot = json.dumps([dict(r) for r in binding_rows]) if binding_rows else "[]"

        # Snapshot opencode server instances.
        server_rows = db.execute("SELECT * FROM opencode_server_instances").fetchall()
        servers_snapshot = json.dumps([dict(r) for r in server_rows]) if server_rows else "[]"

        # Snapshot active suggestion.
        suggestion_rows = db.execute(
            "SELECT id FROM suggestion_queue WHERE session_id=? AND status='pending' LIMIT 1",
            (project_session_id,),
        ).fetchone()
        active_suggestion_id = suggestion_rows[0] if suggestion_rows else None

        # Snapshot plan.
        plan_rows = db.execute(
            "SELECT content FROM session_plan WHERE session_id=? LIMIT 1",
            (project_session_id,),
        ).fetchone()
        plan_snapshot = plan_rows[0] if plan_rows else None

        # Snapshot handoff version.
        handoff_rows = db.execute(
            "SELECT version FROM project_handoff WHERE session_id=? LIMIT 1",
            (project_session_id,),
        ).fetchone()
        handoff_version = handoff_rows[0] if handoff_rows else None

        cur = db.execute(
            """
            INSERT INTO run_checkpoints
                (project_session_id, workspace_id, reason, notes, status,
                 manager_state_json, worker_state_json, reviewer_state_json, chat_state_json,
                 agent_registry_snapshot_json, todos_snapshot_json,
                 runtime_bindings_snapshot_json, opencode_servers_snapshot_json,
                 workspace_path, active_suggestion_id, handoff_version, plan_snapshot)
            VALUES (?, ?, ?, ?, 'complete', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_session_id, workspace_id, reason, notes,
                manager_state, worker_state, reviewer_state, chat_state,
                agent_registry_snapshot, todos_snapshot,
                bindings_snapshot, servers_snapshot,
                workspace_path, active_suggestion_id, handoff_version, plan_snapshot,
            ),
        )
        db.commit()
    return int(cur.lastrowid)


# ── reviewer_issues ───────────────────────────────────────────────────────────

def create_reviewer_issue(
    project_session_id: str,
    issue_type: str,
    description: str,
    suggestion_id: int | None = None,
    severity: int = 3,
    file_path: str | None = None,
    line_number: int | None = None,
    path: Path | None = None,
) -> int:
    with connect(path) as db:
        cur = db.execute(
            """
            INSERT INTO reviewer_issues
                (project_session_id, suggestion_id, issue_type, severity,
                 description, file_path, line_number, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (project_session_id, suggestion_id, issue_type, severity,
             description, file_path, line_number),
        )
        db.commit()
    return int(cur.lastrowid)


def create_reviewer_issues_batch(
    issues: list[dict],
    project_session_id: str,
    suggestion_id: int | None = None,
    path: Path | None = None,
) -> int:
    if not issues:
        return 0
    with connect(path) as db:
        for issue in issues:
            db.execute(
                """
                INSERT INTO reviewer_issues
                    (project_session_id, suggestion_id, issue_type, severity,
                     description, file_path, line_number, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
                """,
                (
                    project_session_id,
                    suggestion_id,
                    issue.get("issue_type", "other"),
                    issue.get("severity", 3),
                    issue.get("description", ""),
                    issue.get("file_path"),
                    issue.get("line_number"),
                ),
            )
        db.commit()
    return len(issues)


def get_open_reviewer_issues(
    project_session_id: str,
    path: Path | None = None,
) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            """
            SELECT * FROM reviewer_issues
             WHERE project_session_id=? AND status='open'
             ORDER BY severity ASC, created_at ASC
            """,
            (project_session_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_reviewer_issue_status(
    issue_id: int,
    status: str,
    path: Path | None = None,
) -> bool:
    with connect(path) as db:
        cur = db.execute(
            """
            UPDATE reviewer_issues
               SET status=?, updated_at=CURRENT_TIMESTAMP
             WHERE id=?
            """,
            (status, issue_id),
        )
        db.commit()
    return cur.rowcount > 0


def get_reviewer_issues_summary(
    project_session_id: str,
    path: Path | None = None,
) -> dict:
    with connect(path) as db:
        rows = db.execute(
            """
            SELECT status, COUNT(*) as count
              FROM reviewer_issues
             WHERE project_session_id=?
             GROUP BY status
            """,
            (project_session_id,),
        ).fetchall()
    result = {status: 0 for status in ["open", "acknowledged", "fixed", "dismissed"]}
    for row in rows:
        result[row["status"]] = row["count"]
    return result
