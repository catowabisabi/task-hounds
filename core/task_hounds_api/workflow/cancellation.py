"""workflow.cancellation - CancellationToken + _PauseRequestedError.

Ported from pre-rebuild flow_01 (0c44ba2:core/api/fastapi_server.py:2805
and core/power_teams/agentic_workflows/flow_01/graph.py:16).

The pre-rebuild design used a DB-row-backed CancellationToken so the
background thread and the FastAPI request handler could coordinate
through the SQLite whiteboard without sharing a Python object. We
keep the same shape:

  - Flow01CancellationToken: wraps workflow_runs.status polling.
      .cancelled() -> True iff status is "cancelling"
      .paused()    -> True iff status starts with "paused_before_"
                       (i.e. the operator pressed pause between nodes)

  - _PauseRequestedError: raised inside a graph node when paused().
    Carries step_name so the graph router can record the precise
    pause point. Caught by the outer run_loop to set status="paused"
    (NOT "failed") and write a checkpoint at the pause boundary.

The BackgroundLoop's per-run cancellation flows through these
primitives: a /api/workflows/flow_01/runs/{id}/pause request
flips workflow_runs.status to "paused_before_{step_name}"; the
next graph node that checks the token sees it and raises
_PauseRequestedError, which run_loop catches and persists a
checkpoint at the exact step.
"""
from __future__ import annotations

from typing import Optional

from task_hounds_api.db.ops import workflow as db_wf


class _PauseRequestedError(RuntimeError):
    """Raised inside a graph node when the operator paused the run.

    The graph router catches this and converts the run to a
    paused state with a checkpoint at the current step. It is
    NOT an error in the operator-facing sense; the loop resumes
    cleanly from the same checkpoint when the operator hits
    /resume.
    """

    def __init__(self, step_name: str):
        super().__init__(f"pause requested before {step_name!r}")
        self.step_name = step_name


class Flow01CancellationToken:
    """Polling-based cancellation token backed by workflow_runs.status.

    The pre-rebuild token was a class that wrapped a single run;
    callers in graph nodes called .cancelled() and .paused()
    before each significant step. The new implementation polls
    the same row the loop's BackgroundLoop updates, so any
    FastAPI request that mutates workflow_runs.status is
    immediately visible to the running graph.

    ``run_id`` is the workflow_runs.id; the constructor does NOT
    validate it (it may be a freshly-allocated id that has not
    been written yet, in which case .cancelled() and .paused()
    both return False).
    """

    def __init__(self, run_id: Optional[int]):
        self.run_id = run_id

    def cancelled(self) -> bool:
        if self.run_id is None:
            return False
        try:
            row = db_wf.get_workflow_run(self.run_id)
        except Exception:
            return False
        if row is None:
            return False
        return str(row.get("status", "")).lower() in {
            "cancelling", "cancelled", "stopping", "stopped",
        }

    def paused(self, step_name: str | None = None) -> bool:
        if self.run_id is None:
            return False
        try:
            row = db_wf.get_workflow_run(self.run_id)
        except Exception:
            return False
        if row is None:
            return False
        status = str(row.get("status", "")).lower()
        if step_name is not None:
            return status == f"paused_before_{step_name}".lower()
        return status.startswith("paused_before_") or status in ("paused", "pausing")

    def pausing(self) -> bool:
        if self.run_id is None:
            return False
        try:
            row = db_wf.get_workflow_run(self.run_id)
        except Exception:
            return False
        if row is None:
            return False
        return str(row.get("status", "")).lower() == "pausing"

    def current_status(self) -> str:
        if self.run_id is None:
            return "unknown"
        try:
            row = db_wf.get_workflow_run(self.run_id)
        except Exception:
            return "unknown"
        return str(row.get("status", "unknown")) if row else "unknown"
