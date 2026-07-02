"""Standalone durable GraphFlow worker.

This process owns graph execution. FastAPI only creates jobs, so a backend
reload cannot terminate an active Manager/Worker/Reviewer run.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
import uuid

from task_hounds_api.db.ops import graphflow_jobs as jobs
from task_hounds_api.db.ops import workflow as db_wf
from task_hounds_api.workflow import graph
from task_hounds_api.workflow import models as M

logger = logging.getLogger(__name__)


def _flow_input(run: dict) -> M.FlowInput:
    try:
        payload = json.loads(run.get("input_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    return M.FlowInput(
        power_team_project_id=str(run["power_team_project_id"]),
        project_session_id=str(run["project_session_id"]),
        human_directive=str(payload.get("human_directive") or ""),
        workspace_path=str(payload.get("workspace_path") or ""),
        manager_opencode_session_id=run.get("manager_opencode_session_id"),
        worker_opencode_session_id=run.get("worker_opencode_session_id"),
        reviewer_opencode_session_id=run.get("reviewer_opencode_session_id"),
        server_instance_id=run.get("server_instance_id"),
        run_id=int(run["id"]),
    )


class Heartbeat:
    def __init__(self, job_id: int, worker_id: str, interval: float = 1.0) -> None:
        self.job_id = job_id
        self.worker_id = worker_id
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name=f"graphflow-heartbeat-{job_id}",
            daemon=True,
        )

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                if not jobs.heartbeat(self.job_id, self.worker_id):
                    logger.error("lost lease for GraphFlow job %s", self.job_id)
                    return
            except Exception:
                logger.exception("GraphFlow heartbeat failed for job %s", self.job_id)

    def __enter__(self) -> "Heartbeat":
        jobs.heartbeat(self.job_id, self.worker_id)
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop_event.set()
        self.thread.join(timeout=self.interval * 2)


def execute(job: dict, worker_id: str) -> None:
    job_id = int(job["id"])
    run_id = int(job["run_id"])
    mode = str(job["mode"])
    try:
        with Heartbeat(job_id, worker_id):
            if mode == "resume":
                result = graph.resume_loop(run_id)
                if not result.get("ok", True):
                    raise RuntimeError(str(result.get("error") or "resume failed"))
            else:
                run = db_wf.get_workflow_run(run_id)
                if not run:
                    raise RuntimeError(f"workflow run {run_id} no longer exists")
                graph.run_loop(_flow_input(run), None)
        current = db_wf.get_workflow_run(run_id) or {}
        run_status = str(current.get("status") or "").lower()
        if run_status in {"cancelled", "stopped"}:
            terminal = "cancelled"
        elif run_status in {"failed", "technical_error"}:
            terminal = "failed"
        else:
            terminal = "completed"
        jobs.finish(job_id, worker_id, terminal)
    except Exception as exc:
        logger.exception("GraphFlow job %s failed", job_id)
        current = db_wf.get_workflow_run(run_id) or {}
        if str(current.get("status") or "").lower() in {"running", "pending"}:
            resumable = db_wf.load_checkpoint(run_id) is not None
            db_wf.update_workflow_run_status(
                run_id,
                "technical_error",
                output_json=json.dumps({
                    "status": "technical_error",
                    "interruption": {
                        "kind": "worker_error",
                        "title": "GraphFlow worker interrupted",
                        "reason": str(exc) or type(exc).__name__,
                        "source": "graphflow_worker",
                        "resumable": resumable,
                    },
                }, ensure_ascii=False),
            )
        jobs.finish(job_id, worker_id, "failed", str(exc))


def run(poll_interval: float = 0.5) -> int:
    worker_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    last_recovery = 0.0
    while True:
        now = time.monotonic()
        if now - last_recovery >= 5.0:
            recovered = jobs.recover_stale()
            if recovered:
                logger.warning("requeued stale GraphFlow runs: %s", recovered)
            last_recovery = now
        job = jobs.claim(worker_id, os.getpid())
        if job is None:
            time.sleep(poll_interval)
            continue
        execute(job, worker_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task Hounds GraphFlow worker")
    parser.add_argument("--poll-interval", type=float, default=0.5)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return run(args.poll_interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
