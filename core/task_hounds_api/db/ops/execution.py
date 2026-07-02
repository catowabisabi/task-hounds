"""Execution-scoped agent state and OpenCode session ownership."""
from __future__ import annotations

from pathlib import Path

from task_hounds_api.db import connect
from task_hounds_api.db.ops import graphflow_jobs as db_jobs

ROLES = {"manager", "worker", "reviewer", "manager_chat", "chat"}


def execution_id(project_session_id: str, workflow_run_id: int | None, role: str) -> str:
    run_part = str(workflow_run_id) if workflow_run_id is not None else "interactive"
    return f"exe_{project_session_id}_{run_part}_{role}"


def upsert_execution(
    *,
    execution_id: str,
    project_session_id: str,
    role: str,
    status: str,
    workflow_run_id: int | None = None,
    agent_registry_name: str | None = None,
    opencode_session_id: str | None = None,
    server_instance_id: int | None = None,
    current_step: str | None = None,
    process_id: int | None = None,
    error: str | None = None,
    path: Path | None = None,
) -> None:
    if role not in ROLES:
        raise ValueError(f"invalid execution role: {role}")
    from task_hounds_api.db.write_queue import write

    def perform() -> None:
        with connect(path) as db:
            db.execute(
                """INSERT INTO agent_execution_state
                   (execution_id, project_session_id, workflow_run_id, role,
                    agent_registry_name, opencode_session_id, server_instance_id,
                    status, current_step, process_id, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(execution_id) DO UPDATE SET
                     status=excluded.status,
                     current_step=COALESCE(excluded.current_step, current_step),
                     process_id=excluded.process_id,
                     error=excluded.error,
                     opencode_session_id=COALESCE(excluded.opencode_session_id, opencode_session_id),
                     server_instance_id=COALESCE(excluded.server_instance_id, server_instance_id),
                     updated_at=CURRENT_TIMESTAMP""",
                (
                    execution_id, project_session_id, workflow_run_id, role,
                    agent_registry_name, opencode_session_id, server_instance_id,
                    status, current_step, process_id, error,
                ),
            )
            db.commit()

    write(perform, priority=20)


def list_executions(
    project_session_id: str | None = None,
    workflow_run_id: int | None = None,
    path: Path | None = None,
) -> list[dict]:
    clauses, values = [], []
    if project_session_id:
        clauses.append("project_session_id=?")
        values.append(project_session_id)
    if workflow_run_id is not None:
        clauses.append("workflow_run_id=?")
        values.append(workflow_run_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect(path) as db:
        rows = db.execute(
            f"SELECT * FROM agent_execution_state {where} ORDER BY updated_at DESC",
            values,
        ).fetchall()
    return [dict(row) for row in rows]


def session_runtime_statuses(path: Path | None = None) -> dict[str, dict]:
    """Return one UI runtime projection per project session.

    This intentionally derives status from session-owned records rather than
    agent_registry, whose rows are only a global/current UI projection.
    """
    db_jobs.reconcile_terminal_jobs(path=path)
    with connect(path) as db:
        sessions = db.execute("SELECT id FROM project_sessions").fetchall()
        executions = db.execute(
            """SELECT * FROM agent_execution_state
               ORDER BY updated_at DESC, created_at DESC"""
        ).fetchall()
        job_rows = db.execute(
            """SELECT gj.*, wr.status AS workflow_status
                 FROM graphflow_jobs gj
                 JOIN workflow_runs wr ON wr.id=gj.run_id
                ORDER BY gj.updated_at DESC, gj.id DESC"""
        ).fetchall()
        questions = db.execute(
            """SELECT project_session_id, role, asked_at
                 FROM opencode_questions
                WHERE status IN ('pending', 'answering')
                ORDER BY asked_at DESC"""
        ).fetchall()

    by_execution: dict[str, list[dict]] = {}
    for row in executions:
        item = dict(row)
        by_execution.setdefault(str(item["project_session_id"]), []).append(item)
    by_job: dict[str, list[dict]] = {}
    for row in job_rows:
        item = dict(row)
        by_job.setdefault(str(item["project_session_id"]), []).append(item)
    by_question = {
        str(row["project_session_id"]): dict(row)
        for row in questions
        if row["project_session_id"]
    }

    result: dict[str, dict] = {}
    for session in sessions:
        session_id = str(session["id"])
        session_executions = by_execution.get(session_id, [])
        session_jobs = by_job.get(session_id, [])
        question = by_question.get(session_id)
        active_job = next(
            (job for job in session_jobs if job.get("status") in {"queued", "running"}),
            None,
        )
        latest_job = session_jobs[0] if session_jobs else None
        active_execution = next(
            (
                execution for execution in session_executions
                if execution.get("status") in {"queued", "busy", "running", "waiting"}
            ),
            None,
        )
        latest_execution = session_executions[0] if session_executions else None
        workflow_status = str(
            (active_job or latest_job or {}).get("workflow_status") or ""
        ).lower()

        if question:
            state = "waiting_for_answer"
            role = question.get("role")
            started_at = question.get("asked_at")
            detail = "Waiting for answer"
        elif workflow_status in {"stopping", "cancelling"}:
            state = "stopping"
            role = (active_execution or latest_execution or {}).get("role")
            started_at = (active_job or latest_job or {}).get("started_at")
            detail = "Stopping"
        elif workflow_status in {"paused", "pausing"}:
            state = "paused"
            role = (active_execution or latest_execution or {}).get("role")
            started_at = (active_job or latest_job or {}).get("started_at")
            detail = "Paused"
        elif active_job or active_execution:
            state = "running"
            role = (active_execution or latest_execution or {}).get("role")
            started_at = (
                (active_execution or {}).get("created_at")
                or (active_job or {}).get("started_at")
                or (active_job or {}).get("created_at")
            )
            detail = (
                (active_execution or {}).get("current_step")
                or ("Starting" if (active_job or {}).get("status") == "queued" else "Running")
            )
        elif workflow_status in {"failed", "error"} or (
            latest_execution and latest_execution.get("status") == "error"
        ):
            state = "error"
            role = (latest_execution or {}).get("role")
            started_at = (
                (latest_execution or {}).get("updated_at")
                or (latest_job or {}).get("finished_at")
            )
            detail = (
                (latest_execution or {}).get("error")
                or (latest_job or {}).get("last_error")
                or "Run failed"
            )
        else:
            state = "idle"
            role = None
            started_at = None
            detail = ""

        result[session_id] = {
            "state": state,
            "role": role,
            "detail": detail,
            "started_at": started_at,
            "run_id": (active_job or latest_job or {}).get("run_id"),
        }
    return result


def finish_run(
    workflow_run_id: int,
    status: str,
    path: Path | None = None,
) -> None:
    from task_hounds_api.db.write_queue import write

    def perform() -> None:
        with connect(path) as db:
            db.execute(
                """UPDATE agent_execution_state
                   SET status=?, process_id=NULL, updated_at=CURRENT_TIMESTAMP
                   WHERE workflow_run_id=? AND status IN ('queued', 'busy', 'running')""",
                (status, workflow_run_id),
            )
            db.commit()

    write(perform, priority=15)


def bind_opencode_session(
    project_session_id: str,
    role: str,
    opencode_session_id: str,
    server_instance_id: int | None = None,
    path: Path | None = None,
) -> None:
    if role not in ROLES:
        raise ValueError(f"invalid binding role: {role}")
    with connect(path) as db:
        owner = db.execute(
            "SELECT project_session_id, role FROM opencode_session_bindings WHERE opencode_session_id=?",
            (opencode_session_id,),
        ).fetchone()
        if owner and (
            owner["project_session_id"] != project_session_id or owner["role"] != role
        ):
            raise ValueError(
                f"OpenCode session {opencode_session_id!r} belongs to "
                f"{owner['project_session_id']}:{owner['role']}"
            )
        db.execute(
            """INSERT INTO opencode_session_bindings
               (opencode_session_id, project_session_id, role, server_instance_id)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(project_session_id, role) DO UPDATE SET
                 opencode_session_id=excluded.opencode_session_id,
                 server_instance_id=excluded.server_instance_id,
                 last_used_at=CURRENT_TIMESTAMP""",
            (opencode_session_id, project_session_id, role, server_instance_id),
        )
        db.commit()


def get_bound_session(
    project_session_id: str,
    role: str,
    path: Path | None = None,
) -> str | None:
    with connect(path) as db:
        row = db.execute(
            """SELECT opencode_session_id FROM opencode_session_bindings
               WHERE project_session_id=? AND role=?""",
            (project_session_id, role),
        ).fetchone()
    return str(row["opencode_session_id"]) if row else None
