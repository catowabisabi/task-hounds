from __future__ import annotations

from datetime import datetime, timedelta, timezone

from task_hounds_api.db import connect, init_db
from task_hounds_api.db.ops import graphflow_jobs as jobs


def _run(session_id: str) -> int:
    with connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO project_sessions(id, name, is_active) VALUES (?, ?, 1)",
            (session_id, session_id),
        )
        cur = db.execute(
            """INSERT INTO workflow_runs
               (power_team_project_id, project_session_id, loop_index, status,
                input_json, output_json)
               VALUES (?, ?, 0, 'running', '{}', '{}')""",
            (f"pt_{session_id}", session_id),
        )
        db.commit()
    return int(cur.lastrowid)


def test_queue_claim_heartbeat_and_finish(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "queue.db"))
    init_db()
    run_id = _run("session-a")

    queued = jobs.enqueue(run_id, "session-a", "start")
    claimed = jobs.claim("worker-a", 123)

    assert queued["status"] == "queued"
    assert claimed["run_id"] == run_id
    assert claimed["status"] == "running"
    assert jobs.heartbeat(claimed["id"], "worker-a")

    jobs.finish(claimed["id"], "worker-a", "completed")
    assert jobs.get_for_run(run_id)["status"] == "completed"


def test_one_active_job_per_session(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "single.db"))
    init_db()
    first_run = _run("session-a")
    second_run = _run("session-a")

    first = jobs.enqueue(first_run, "session-a", "start")
    second = jobs.enqueue(second_run, "session-a", "start")

    assert second["id"] == first["id"]
    assert len(jobs.active()) == 1


def test_active_job_is_visible_by_project_session(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "active.db"))
    init_db()
    run_id = _run("session-a")
    jobs.enqueue(run_id, "session-a", "start")
    claimed = jobs.claim("worker-a", 123)

    active = jobs.active_for_session("session-a")

    assert active["id"] == claimed["id"]
    assert active["run_id"] == run_id
    assert active["workflow_status"] == "running"
    assert jobs.active_for_session("session-b") is None


def test_stale_worker_requeues_resume_from_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "recovery.db"))
    init_db()
    run_id = _run("session-a")
    queued = jobs.enqueue(run_id, "session-a", "start")
    claimed = jobs.claim("dead-worker", 456)
    old = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as db:
        db.execute(
            "UPDATE graphflow_jobs SET heartbeat_at=? WHERE id=?",
            (old, claimed["id"]),
        )
        db.commit()

    recovered = jobs.recover_stale(stale_after_seconds=15)

    assert recovered == [run_id]
    job = jobs.get_for_run(run_id)
    assert job["status"] == "queued"
    assert job["mode"] == "resume"
    with connect() as db:
        status = db.execute(
            "SELECT status FROM workflow_runs WHERE id=?", (run_id,)
        ).fetchone()["status"]
    assert status == "recovering"


def test_cold_start_suspends_existing_active_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "cold-start.db"))
    init_db()
    running_run = _run("session-a")
    queued_run = _run("session-b")
    jobs.enqueue(running_run, "session-a", "start")
    jobs.claim("old-worker", 456)
    jobs.enqueue(queued_run, "session-b", "resume")

    suspended = jobs.suspend_active_for_cold_start()

    assert set(suspended) == {running_run, queued_run}
    assert jobs.active() == []
    with connect() as db:
        rows = db.execute(
            "SELECT id, status, output_json FROM workflow_runs ORDER BY id"
        ).fetchall()
        job_rows = db.execute(
            "SELECT run_id, status, last_error FROM graphflow_jobs ORDER BY run_id"
        ).fetchall()
    assert [row["status"] for row in rows] == ["technical_error", "technical_error"]
    assert all("restart_required" in row["output_json"] for row in rows)
    assert [row["status"] for row in job_rows] == ["cancelled", "cancelled"]
    assert all("Suspended after app restart" in row["last_error"] for row in job_rows)


def test_pause_and_resume_keep_run_and_job_aligned(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "controls.db"))
    init_db()
    run_id = _run("session-a")
    queued = jobs.enqueue(run_id, "session-a", "start")
    jobs.claim("worker-a", 123)

    paused = jobs.control_run(
        run_id, "session-a", "pause", '{"status":"paused"}'
    )
    assert paused["run"]["status"] == "paused"
    assert paused["job"]["status"] == "cancelled"

    resumed = jobs.control_run(
        run_id, "session-a", "resume", '{"status":"recovering"}'
    )
    assert resumed["run"]["status"] == "recovering"
    assert resumed["job"]["status"] == "queued"
    assert resumed["job"]["mode"] == "resume"
    assert resumed["job"]["id"] == queued["id"]


def test_old_worker_cannot_overwrite_a_paused_job(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "late-finish.db"))
    init_db()
    run_id = _run("session-a")
    jobs.enqueue(run_id, "session-a", "start")
    claimed = jobs.claim("worker-a", 123)

    jobs.control_run(run_id, "session-a", "pause", '{"status":"paused"}')
    jobs.finish(claimed["id"], "worker-a", "completed")

    assert jobs.get_for_run(run_id)["status"] == "cancelled"


def test_terminal_run_cleans_orphaned_active_job(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "terminal-orphan.db"))
    init_db()
    run_id = _run("session-a")
    jobs.enqueue(run_id, "session-a", "start")
    jobs.claim("dead-worker", 456)
    with connect() as db:
        db.execute(
            "UPDATE workflow_runs SET status='failed' WHERE id=?",
            (run_id,),
        )
        db.commit()

    assert jobs.active_for_session("session-a") is None
    job = jobs.get_for_run(run_id)
    assert job["status"] == "failed"
    assert job["worker_id"] is None
    assert job["worker_pid"] is None


def test_enqueue_ignores_orphan_from_terminal_run(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "enqueue-orphan.db"))
    init_db()
    old_run = _run("session-a")
    jobs.enqueue(old_run, "session-a", "start")
    jobs.claim("dead-worker", 456)
    with connect() as db:
        db.execute(
            "UPDATE workflow_runs SET status='failed' WHERE id=?",
            (old_run,),
        )
        db.commit()
    new_run = _run("session-a")

    queued = jobs.enqueue(new_run, "session-a", "start")

    assert queued["run_id"] == new_run
    assert queued["status"] == "queued"


def test_capacity_blocks_when_active_job_limit_is_reached(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "capacity-full.db"))
    monkeypatch.setenv("TASK_HOUNDS_GRAPHFLOW_WORKER_COUNT", "2")
    monkeypatch.setenv("TASK_HOUNDS_MAX_ACTIVE_JOBS", "1")
    monkeypatch.setenv("POWER_TEAMS_OPENCODE_CONCURRENCY", "2")
    monkeypatch.setenv("TASK_HOUNDS_MAX_CPU_PERCENT", "100")
    monkeypatch.setenv("TASK_HOUNDS_MAX_MEMORY_PERCENT", "100")
    init_db()
    run_id = _run("session-a")
    jobs.enqueue(run_id, "session-a", "start")

    from task_hounds_api.workflow import capacity

    snapshot = capacity.snapshot()

    assert snapshot.ok is False
    assert snapshot.active_jobs == 1
    assert snapshot.max_active_jobs == 1
    assert "active job capacity reached" in str(snapshot.reason)


def test_capacity_requires_matching_worker_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "capacity-workers.db"))
    monkeypatch.setenv("TASK_HOUNDS_GRAPHFLOW_WORKER_COUNT", "1")
    monkeypatch.setenv("TASK_HOUNDS_MAX_ACTIVE_JOBS", "2")
    monkeypatch.setenv("POWER_TEAMS_OPENCODE_CONCURRENCY", "2")
    monkeypatch.setenv("TASK_HOUNDS_MAX_CPU_PERCENT", "100")
    monkeypatch.setenv("TASK_HOUNDS_MAX_MEMORY_PERCENT", "100")
    init_db()

    from task_hounds_api.workflow import capacity

    snapshot = capacity.snapshot()

    assert snapshot.ok is False
    assert snapshot.worker_count == 1
    assert snapshot.max_active_jobs == 2
    assert "exceeds GraphFlow workers" in str(snapshot.reason)


def test_capacity_blocks_when_cpu_is_over_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "capacity-cpu.db"))
    monkeypatch.setenv("TASK_HOUNDS_GRAPHFLOW_WORKER_COUNT", "2")
    monkeypatch.setenv("TASK_HOUNDS_MAX_ACTIVE_JOBS", "2")
    monkeypatch.setenv("POWER_TEAMS_OPENCODE_CONCURRENCY", "2")
    monkeypatch.setenv("TASK_HOUNDS_MAX_CPU_PERCENT", "75")
    monkeypatch.setenv("TASK_HOUNDS_MAX_MEMORY_PERCENT", "100")
    init_db()

    from task_hounds_api.workflow import capacity

    monkeypatch.setattr(capacity, "_cpu_percent", lambda: 91.5)
    monkeypatch.setattr(capacity, "_memory_percent", lambda: 20.0)
    snapshot = capacity.snapshot()

    assert snapshot.ok is False
    assert snapshot.cpu_percent == 91.5
    assert "CPU is too busy" in str(snapshot.reason)


def test_start_run_rejects_new_job_when_capacity_is_full(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "capacity-route.db"))
    monkeypatch.setenv("TASK_HOUNDS_GRAPHFLOW_WORKER_COUNT", "1")
    monkeypatch.setenv("TASK_HOUNDS_MAX_ACTIVE_JOBS", "1")
    monkeypatch.setenv("POWER_TEAMS_OPENCODE_CONCURRENCY", "1")
    monkeypatch.setenv("TASK_HOUNDS_MAX_CPU_PERCENT", "100")
    monkeypatch.setenv("TASK_HOUNDS_MAX_MEMORY_PERCENT", "100")
    init_db()
    run_id = _run("session-a")
    jobs.enqueue(run_id, "session-a", "start")

    from task_hounds_api.api.routes.workflow import flow01_start_run

    result = flow01_start_run({
        "project_session_id": "session-b",
        "human_directive": "build another thing",
    })

    assert result["ok"] is False
    assert result["error_code"] == "capacity_unavailable"
    assert result["capacity"]["active_jobs"] == 1
    with connect() as db:
        count = db.execute(
            "SELECT COUNT(*) AS n FROM workflow_runs WHERE project_session_id='session-b'"
        ).fetchone()["n"]
    assert count == 0
