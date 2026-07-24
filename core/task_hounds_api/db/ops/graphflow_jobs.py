"""Durable queue operations for the standalone GraphFlow worker."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from task_hounds_api.db import connect


ACTIVE = ("queued", "running")
JOB_COMPATIBLE_RUN_STATUSES = (
    "pending",
    "running",
    "recovering",
    "pausing",
    "stopping",
    "cancelling",
)


def reconcile_terminal_jobs(
    project_session_id: str | None = None,
    path: Path | None = None,
) -> list[int]:
    """Close active queue rows whose parent workflow run is already terminal."""
    session_clause = " AND gj.project_session_id=?" if project_session_id else ""
    params = [project_session_id] if project_session_id else []
    compatible = ",".join("?" for _ in JOB_COMPATIBLE_RUN_STATUSES)
    params.extend(JOB_COMPATIBLE_RUN_STATUSES)
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            f"""SELECT gj.id, gj.run_id, wr.status AS workflow_status
                  FROM graphflow_jobs gj
                  JOIN workflow_runs wr ON wr.id=gj.run_id
                 WHERE gj.status IN ('queued', 'running')
                   {session_clause}
                   AND LOWER(COALESCE(wr.status, '')) NOT IN ({compatible})""",
            params,
        ).fetchall()
        for row in rows:
            workflow_status = str(row["workflow_status"] or "").lower()
            job_status = (
                "completed"
                if workflow_status == "completed"
                else "cancelled"
                if workflow_status in {"cancelled", "stopped", "paused"}
                or workflow_status.startswith("paused_before_")
                else "failed"
            )
            db.execute(
                """UPDATE graphflow_jobs
                      SET status=?, worker_id=NULL, worker_pid=NULL,
                          heartbeat_at=NULL, finished_at=CURRENT_TIMESTAMP,
                          last_error=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND status IN ('queued', 'running')""",
                (
                    job_status,
                    f"Parent workflow run is {workflow_status or 'terminal'}.",
                    int(row["id"]),
                ),
            )
        db.commit()
    return [int(row["run_id"]) for row in rows]


def enqueue(
    run_id: int,
    project_session_id: str,
    mode: str,
    path: Path | None = None,
) -> dict:
    if mode not in {"start", "resume"}:
        raise ValueError(f"invalid GraphFlow job mode: {mode}")
    reconcile_terminal_jobs(project_session_id, path)
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        active = db.execute(
            """SELECT * FROM graphflow_jobs
                 WHERE project_session_id=? AND status IN ('queued', 'running')
                 ORDER BY id DESC LIMIT 1""",
            (project_session_id,),
        ).fetchone()
        if active:
            db.rollback()
            return dict(active)
        db.execute(
            """INSERT INTO graphflow_jobs
               (run_id, project_session_id, mode, status, available_at, created_at, updated_at)
               VALUES (?, ?, ?, 'queued', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(run_id) DO UPDATE SET
                   mode=excluded.mode,
                   status='queued',
                   worker_id=NULL,
                   worker_pid=NULL,
                   heartbeat_at=NULL,
                   available_at=CURRENT_TIMESTAMP,
                   finished_at=NULL,
                   last_error=NULL,
                   updated_at=CURRENT_TIMESTAMP""",
            (run_id, project_session_id, mode),
        )
        row = db.execute(
            "SELECT * FROM graphflow_jobs WHERE run_id=?", (run_id,)
        ).fetchone()
        db.commit()
    return dict(row)


def claim(worker_id: str, worker_pid: int, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            """SELECT * FROM graphflow_jobs
                WHERE status='queued' AND available_at <= CURRENT_TIMESTAMP
                ORDER BY id LIMIT 1"""
        ).fetchone()
        if not row:
            db.rollback()
            return None
        cur = db.execute(
            """UPDATE graphflow_jobs
                  SET status='running', worker_id=?, worker_pid=?,
                      attempts=attempts+1, heartbeat_at=CURRENT_TIMESTAMP,
                      started_at=COALESCE(started_at, CURRENT_TIMESTAMP),
                      updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='queued'""",
            (worker_id, worker_pid, row["id"]),
        )
        if cur.rowcount != 1:
            db.rollback()
            return None
        claimed = db.execute(
            "SELECT * FROM graphflow_jobs WHERE id=?", (row["id"],)
        ).fetchone()
        db.commit()
    return dict(claimed)


def heartbeat(job_id: int, worker_id: str, path: Path | None = None) -> bool:
    with connect(path) as db:
        cur = db.execute(
            """UPDATE graphflow_jobs
                  SET heartbeat_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND worker_id=? AND status='running'""",
            (job_id, worker_id),
        )
        db.commit()
    return cur.rowcount == 1


def finish(
    job_id: int,
    worker_id: str,
    status: str,
    error: str = "",
    path: Path | None = None,
) -> None:
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError(f"invalid terminal GraphFlow job status: {status}")
    with connect(path) as db:
        db.execute(
            """UPDATE graphflow_jobs
                  SET status=?, last_error=?, heartbeat_at=CURRENT_TIMESTAMP,
                      finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND worker_id=? AND status='running'""",
            (status, error or None, job_id, worker_id),
        )
        db.commit()


def control_run(
    run_id: int,
    project_session_id: str,
    action: str,
    output_json: str,
    path: Path | None = None,
) -> dict:
    """Atomically align the workflow run and its durable worker job."""
    if action not in {"pause", "resume", "stop"}:
        raise ValueError(f"invalid GraphFlow control action: {action}")
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        run = db.execute(
            "SELECT * FROM workflow_runs WHERE id=? AND project_session_id=?",
            (run_id, project_session_id),
        ).fetchone()
        if not run:
            db.rollback()
            raise ValueError(f"run {run_id} not found for session {project_session_id}")

        current = str(run["status"] or "").lower()
        if action == "pause":
            if current not in {"running", "recovering", "pausing"}:
                db.rollback()
                raise ValueError(f"run {run_id} cannot pause from status {current!r}")
            run_status = "paused"
            db.execute(
                "UPDATE workflow_runs SET status=?, output_json=? WHERE id=?",
                (run_status, output_json, run_id),
            )
            db.execute(
                """UPDATE graphflow_jobs
                      SET status='cancelled', finished_at=CURRENT_TIMESTAMP,
                          heartbeat_at=NULL, last_error='Paused by user.',
                          updated_at=CURRENT_TIMESTAMP
                    WHERE run_id=? AND status IN ('queued', 'running')""",
                (run_id,),
            )
        elif action == "stop":
            if current in {"completed", "cancelled", "stopped"}:
                db.rollback()
                raise ValueError(f"run {run_id} is already finished ({current})")
            run_status = "cancelled"
            db.execute(
                "UPDATE workflow_runs SET status=?, output_json=? WHERE id=?",
                (run_status, output_json, run_id),
            )
            db.execute(
                """UPDATE graphflow_jobs
                      SET status='cancelled', finished_at=CURRENT_TIMESTAMP,
                          heartbeat_at=NULL, last_error='Stopped by user.',
                          updated_at=CURRENT_TIMESTAMP
                    WHERE run_id=? AND status IN ('queued', 'running')""",
                (run_id,),
            )
        else:
            if not (
                current == "paused"
                or current.startswith("paused_before_")
                or current == "technical_error"
            ):
                db.rollback()
                raise ValueError(f"run {run_id} cannot resume from status {current!r}")
            run_status = "recovering"
            db.execute(
                "UPDATE workflow_runs SET status=?, output_json=? WHERE id=?",
                (run_status, output_json, run_id),
            )
            db.execute(
                """INSERT INTO graphflow_jobs
                   (run_id, project_session_id, mode, status, available_at,
                    created_at, updated_at)
                   VALUES (?, ?, 'resume', 'queued', CURRENT_TIMESTAMP,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                   ON CONFLICT(run_id) DO UPDATE SET
                       mode='resume', status='queued', worker_id=NULL,
                       worker_pid=NULL, heartbeat_at=NULL,
                       available_at=CURRENT_TIMESTAMP, finished_at=NULL,
                       last_error=NULL, updated_at=CURRENT_TIMESTAMP""",
                (run_id, project_session_id),
            )

        job = db.execute(
            "SELECT * FROM graphflow_jobs WHERE run_id=?", (run_id,)
        ).fetchone()
        updated_run = db.execute(
            "SELECT * FROM workflow_runs WHERE id=?", (run_id,)
        ).fetchone()
        db.commit()
    return {
        "run": dict(updated_run),
        "job": dict(job) if job else None,
    }


def active(path: Path | None = None) -> list[dict]:
    reconcile_terminal_jobs(path=path)
    with connect(path) as db:
        rows = db.execute(
            """SELECT * FROM graphflow_jobs
                WHERE status IN ('queued', 'running') ORDER BY id"""
        ).fetchall()
    return [dict(row) for row in rows]


def active_for_session(
    project_session_id: str,
    path: Path | None = None,
) -> dict | None:
    reconcile_terminal_jobs(project_session_id, path)
    with connect(path) as db:
        row = db.execute(
            """SELECT gj.*, wr.status AS workflow_status, wr.loop_index
                 FROM graphflow_jobs gj
                 JOIN workflow_runs wr ON wr.id=gj.run_id
                WHERE gj.project_session_id=?
                  AND gj.status IN ('queued', 'running')
                ORDER BY gj.id DESC
                LIMIT 1""",
            (project_session_id,),
        ).fetchone()
    return dict(row) if row else None


def get_for_run(run_id: int, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM graphflow_jobs WHERE run_id=?", (run_id,)
        ).fetchone()
    return dict(row) if row else None


def suspend_active_for_cold_start(path: Path | None = None) -> list[int]:
    """Stop pre-existing GraphFlow work when the app starts cold.

    Durable jobs are important for explicit resume, but silently continuing a
    previous desktop session after boot is surprising and can write to the
    wrong project while the user is looking at another session. Mark those
    runs as operator-visible technical interruptions and leave resume as an
    explicit action when a checkpoint exists.
    """
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            """SELECT gj.id, gj.run_id
                 FROM graphflow_jobs gj
                 JOIN workflow_runs wr ON wr.id=gj.run_id
                WHERE gj.status IN ('queued', 'running')
                  AND wr.status IN ('pending', 'running', 'recovering',
                                    'pausing', 'stopping', 'cancelling')"""
        ).fetchall()
        run_ids = [int(row["run_id"]) for row in rows]
        for row in rows:
            run_id = int(row["run_id"])
            resumable = db.execute(
                "SELECT 1 FROM flow_checkpoints WHERE run_id=? LIMIT 1",
                (run_id,),
            ).fetchone() is not None
            output = json.dumps({
                "status": "technical_error",
                "interruption": {
                    "kind": "restart_required",
                    "title": "GraphFlow paused after app restart",
                    "reason": (
                        "Task Hounds found this run active during startup and "
                        "did not resume it automatically."
                    ),
                    "source": "supervisor_startup",
                    "resumable": resumable,
                },
            }, ensure_ascii=False)
            db.execute(
                "UPDATE workflow_runs SET status='technical_error', output_json=? WHERE id=?",
                (output, run_id),
            )
            db.execute(
                """UPDATE graphflow_jobs
                      SET status='cancelled', worker_id=NULL, worker_pid=NULL,
                          heartbeat_at=NULL, finished_at=CURRENT_TIMESTAMP,
                          last_error='Suspended after app restart; resume manually.',
                          updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND status IN ('queued', 'running')""",
                (int(row["id"]),),
            )
        db.commit()
    return run_ids


def recover_stale(
    stale_after_seconds: int = 15,
    path: Path | None = None,
) -> list[int]:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    ).strftime("%Y-%m-%d %H:%M:%S")
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            """SELECT gj.id, gj.run_id
                 FROM graphflow_jobs gj
                 JOIN workflow_runs wr ON wr.id=gj.run_id
                WHERE gj.status='running'
                  AND wr.status IN ('running', 'recovering', 'pausing', 'stopping')
                  AND (gj.heartbeat_at IS NULL OR gj.heartbeat_at < ?)""",
            (cutoff,),
        ).fetchall()
        run_ids = [int(row["run_id"]) for row in rows]
        if rows:
            placeholders = ",".join("?" for _ in rows)
            db.execute(
                f"""UPDATE graphflow_jobs
                       SET status='queued', mode='resume', worker_id=NULL,
                           worker_pid=NULL, heartbeat_at=NULL,
                           available_at=CURRENT_TIMESTAMP,
                           last_error='Worker heartbeat expired; resuming from checkpoint.',
                           updated_at=CURRENT_TIMESTAMP
                     WHERE id IN ({placeholders})""",
                [int(row["id"]) for row in rows],
            )
            run_placeholders = ",".join("?" for _ in run_ids)
            db.execute(
                f"""UPDATE workflow_runs
                       SET status='recovering'
                     WHERE id IN ({run_placeholders})
                       AND status IN ('running', 'pausing', 'stopping')""",
                run_ids,
            )
        db.commit()
    return run_ids
