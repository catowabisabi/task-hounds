"""api.routes.workflow — workflow control: start/stop loop, plan, suggestion, reports.

session_id is optional on all session-scoped routes — defaults to the
active project session.

Loop control is delegated to a single BackgroundLoop singleton
(workflow.loop.BackgroundLoop). There is intentionally no second
controller here.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Query

from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.db.ops import workflow as db_wf
from task_hounds_api.db.ops import execution as db_execution
from task_hounds_api.db.ops import graphflow_jobs as db_jobs
from task_hounds_api.db.ops import rounds as db_rounds
from task_hounds_api.api.deps import resolve_session_id, require_session_id
from task_hounds_api.api import schemas
from task_hounds_api.workflow.loop import BackgroundLoop, run_once
from task_hounds_api.workflow import capacity as wf_capacity
from task_hounds_api.opencode import registry as oc_registry

router = APIRouter(prefix="/api/workflow", tags=["workflow"])


_bg = BackgroundLoop()


def _scoped_run(run_id: int, project_session_id: str | None = None) -> tuple[dict | None, str | None]:
    run = db_wf.get_workflow_run(run_id)
    if run is None:
        return None, f"run {run_id} not found"
    expected_session = project_session_id or resolve_session_id(None)
    actual_session = str(run.get("project_session_id") or "")
    if expected_session and actual_session != expected_session:
        return None, (
            f"run {run_id} belongs to session {actual_session!r}, "
            f"not current session {expected_session!r}"
        )
    return run, None


# ── Loop control helpers (imported by compat.py too) ───────────────────────


def workflow_loop_status() -> dict:
    """Current loop state plus the legacy `running`/`loop_running`
    fields. The UI consumes this to decide whether to show the
    loop as healthy, starting, or failed (with a retry button)."""
    durable_jobs = db_jobs.active()
    running = bool(durable_jobs) or _bg.is_running()
    return {
        "running": running,
        "loop_running": running,
        "loop_state": "running" if durable_jobs else _bg.get_state(),
        "pid": (
            durable_jobs[0].get("worker_pid")
            if durable_jobs else _bg.get_pid()
        ),
        "last_start_error": _bg.get_last_start_error(),
        "last_error_at": _bg.get_last_error_at(),
        "active_runs": [
            {
                "run_id": job["run_id"],
                "project_session_id": job["project_session_id"],
                "status": job["status"],
                "heartbeat_at": job.get("heartbeat_at"),
                "worker_pid": job.get("worker_pid"),
            }
            for job in durable_jobs
        ],
    }


def workflow_start_loop() -> dict:
    """Start the loop and BLOCK on the startup handshake.

    Returns the shape produced by `BackgroundLoop.start()`:
      ok, started, running, state, pid, error, reason.
    `started` is only True when ensure_managed_running actually
    succeeded. A failed handshake returns started=False with a
    populated `error` so the UI can surface the failure to the
    operator instead of silently accepting a dead loop."""
    return _bg.start()


def workflow_stop_loop() -> dict:
    # Migration audit symbol 199: restore legacy {ok, stopped, state, pid}
    # response envelope alongside the new shape. Old callers expected
    # these four fields; new callers get the full stop() result.
    result = _bg.stop()
    state = _bg._state
    return {
        **result,
        "ok": True,
        "stopped": True,
        "state": state,
        "pid": _bg.get_pid(),
    }


def workflow_run_once() -> dict:
    result = run_once()
    if result is None:
        return {"ok": True, "ran": False, "result": None}
    return {"ok": True, "ran": True, "result": result}


# ── Background loop control ────────────────────────────────────────────────


@router.get("/status", response_model=schemas.LoopStatusOut)
def status() -> dict:
    """Migration audit symbol 148: GET /api/loop/status returns the
    typed LoopStatusOut shape."""
    return workflow_loop_status()


@router.post("/start-loop")
def start_loop() -> dict:
    return workflow_start_loop()


@router.post("/stop-loop")
def stop_loop() -> dict:
    return workflow_stop_loop()


@router.post("/run-once")
def run_once_route() -> dict:
    return workflow_run_once()


# ── Plan ────────────────────────────────────────────────────────────────────

@router.get("/plan")
def get_plan(session_id: str | None = Query(default=None)) -> dict | None:
    sid = resolve_session_id(session_id)
    if not sid:
        return {}
    return db_wf.get_plan(sid) or {}


@router.put("/plan")
def put_plan(body: dict, session_id: str | None = Query(default=None)) -> dict:
    sid = require_session_id(session_id)
    db_wf.set_plan(sid, body.get("content", ""), updated_by="manager")
    return {"updated": True}


# ── Suggestion ─────────────────────────────────────────────────────────────

@router.get("/suggestion", response_model=schemas.SuggestionOut)
def get_suggestion(session_id: str | None = Query(default=None)) -> dict | None:
    """Migration audit symbol 149: GET /api/suggestion returns the
    typed SuggestionOut shape (extras allowed for DB-derived fields)."""
    sid = resolve_session_id(session_id)
    if not sid:
        return {}
    return db_wf.get_active_suggestion(sid) or {}


@router.post("/suggestion")
def create_suggestion(body: dict, session_id: str | None = Query(default=None)) -> dict:
    sid = require_session_id(session_id)
    sugg_id = db_wf.create_suggestion(
        session_id=sid,
        content=body.get("content", ""),
        verification=body.get("verification"),
        status=body.get("status", "released"),
    )
    return {"id": sugg_id}


@router.post("/suggestion/{suggestion_id}/status")
def update_suggestion_status(suggestion_id: int, body: dict) -> dict:
    db_wf.update_suggestion_status(suggestion_id, body.get("status", "done"))
    return {"updated": suggestion_id}


# ── Worker reports ─────────────────────────────────────────────────────────

@router.get("/reports")
def list_reports(
    session_id: str | None = Query(default=None),
    limit: int = 20,
) -> list[dict]:
    sid = resolve_session_id(session_id)
    if not sid:
        return []
    return db_wf.list_worker_reports(sid, limit=limit)


# ── Manager messages ──────────────────────────────────────────────────────

@router.get("/manager-messages", response_model=list[schemas.ManagerMessageOut])
def manager_messages(
    session_id: str | None = Query(default=None),
    limit: int = 20,
) -> list[dict]:
    """Migration audit symbol 151: GET /api/manager-messages returns a
    typed list of ManagerMessageOut objects."""
    sid = resolve_session_id(session_id)
    if not sid:
        return []
    return db_wf.list_manager_messages(sid, limit=limit)


# Legacy aliases (Phase 6) -- the UI ternary flow01Mode ? ... : ...
# still uses /api/manager-messages and /api/workflows/flow_01/... .
# The compat duplicates were deleted; the authoritative versions
# live below as proper APIRouter modules.

manager_messages_root = APIRouter(tags=["manager-messages-legacy"])


@manager_messages_root.get("/api/manager-messages")
def legacy_manager_messages_root() -> list[dict]:
    sid = resolve_session_id(None)
    if not sid:
        return []
    return db_wf.list_manager_messages(sid)


@manager_messages_root.post("/api/manager-messages")
async def legacy_post_manager_message_root(body: schemas.ManagerMessageCreate) -> dict:
    """Migration audit symbol 152 (P7 id 152) + P8 id 219.

    The legacy /api/manager-messages POST now uses the typed
    ManagerMessageCreate request body. Old `extra="forbid"`
    Pydantic validation ensures unknown fields are rejected
    at the HTTP layer (vs. the prior raw-dict behavior that
    silently dropped them). Non-empty content is required.
    session_id is optional; when absent the active session is
    used (consistent with the rest of the manager-messages
    surface).

    P8 id 219: the legacy contract added a "Human message
    to manager: " prefix to the content before persisting.
    The fix: prefix the content before appending so the
    downstream Manager node sees the legacy-prefixed text.

    No response_model: the response shape is the legacy
    {id, ok} envelope, not the ManagerMessageCreate class
    (which represents a request body, not a response).
    """
    sid = require_session_id(body.session_id)
    # P8 id 219: legacy prefix. The new authoritative route
    # (workflow.py:201) does NOT prefix; the prefix is
    # preserved here on the legacy compat path.
    prefixed = f"Human message to manager: {body.content}"
    mid = db_wf.append_manager_message(sid, prefixed)
    return {"id": mid, "ok": True}


flow01_router = APIRouter(prefix="/api/workflows/flow_01", tags=["flow_01_legacy"])


@flow01_router.get("/manager-messages")
def legacy_flow01_manager_messages() -> list[dict]:
    sid = resolve_session_id(None)
    if not sid:
        return []
    return db_wf.list_manager_messages(sid)


@flow01_router.post("/manager-messages")
async def legacy_flow01_post_manager_message(request: Request) -> dict:
    body = await request.json()
    sid = require_session_id(body.get("session_id"))
    mid = db_wf.append_manager_message(sid, body.get("content", ""))
    return {"id": mid}


@flow01_router.get("/runs")
def legacy_flow01_runs(
    limit: int = Query(default=20),
) -> list[dict]:
    sid = resolve_session_id(None)
    if not sid:
        return []
    try:
        runs = db_wf.list_workflow_runs(sid, limit=limit)
        active_ids = {int(item["run_id"]) for item in db_jobs.active()}
        for run in runs:
            run_status = str(run.get("status", "")).lower()
            if run_status == "completed":
                try:
                    output = json.loads(run.get("output_json") or "{}")
                except Exception:
                    output = {}
                todos = output.get("todo_list") if isinstance(output, dict) else []
                unresolved = [
                    todo for todo in (todos or [])
                    if isinstance(todo, dict)
                    and todo.get("status") == "completed"
                    and (
                        todo.get("worker_task_status") in {"skipped", "error"}
                        or todo.get("reviewer_task_status") in {"fail", "needs_review", "skipped", "error"}
                        or (
                            int(todo.get("attempt_count", 0) or 0) == 0
                            and todo.get("worker_task_status", "pending") == "pending"
                            and todo.get("reviewer_task_status", "pending") == "pending"
                        )
                    )
                ]
                if unresolved and isinstance(output, dict) and not output.get("interruption"):
                    output["interruption"] = {
                        "kind": "manager_completion_with_unresolved_evidence",
                        "title": "Manager ended GraphFlow with unresolved evidence",
                        "reason": (
                            output.get("manager_message")
                            or "Manager marked the run completed despite unresolved Worker or Reviewer evidence."
                        ),
                        "source": "manager",
                        "resumable": False,
                        "affected_todos": [
                            {"id": todo.get("id"), "content": todo.get("content")}
                            for todo in unresolved
                        ],
                    }
                    encoded = json.dumps(output, ensure_ascii=False, default=str)
                    output["status"] = "completed_with_unresolved_evidence"
                    encoded = json.dumps(output, ensure_ascii=False, default=str)
                    db_wf.update_workflow_run_status(
                        run["id"],
                        "completed_with_unresolved_evidence",
                        output_json=encoded,
                    )
                    run["status"] = "completed_with_unresolved_evidence"
                    run["output_json"] = encoded
                continue
            if run_status != "running":
                continue
            run_id = int(run["id"])
            if run_id in active_ids:
                continue
            interruption = {
                "kind": "orphaned_run",
                "title": "GraphFlow was interrupted",
                "reason": "The app or workflow process stopped while this run was active.",
                "source": "process_lifecycle",
                "resumable": bool(db_wf.load_checkpoint(run_id)),
            }
            output = {"status": "technical_error", "interruption": interruption}
            db_wf.update_workflow_run_status(
                run_id,
                "technical_error",
                output_json=json.dumps(output, ensure_ascii=False),
            )
            run["status"] = "technical_error"
            run["output_json"] = json.dumps(output, ensure_ascii=False)
        return runs
    except Exception:
        return []


@flow01_router.post("/runs/{run_id}/stop")
def flow01_stop_run(run_id: int, body: dict | None = None) -> dict:
    """Mark a flow_01 run as 'stopping'. The next graph tick that sees
    this status will route to END without running further nodes.

    The graph nodes themselves do not poll the DB on every step
    (that would be a hot loop). Stop semantics are cooperative: the
    manager is expected to read the run status on the next loop
    boundary (manager_digest). For an immediate abort, callers should
    also stop the BackgroundLoop via /api/workflow/stop-loop.
    """
    run, scope_error = _scoped_run(run_id, (body or {}).get("project_session_id"))
    if scope_error:
        return {"ok": False, "error": scope_error}
    output = {
        "status": "cancelled",
        "interruption": {
            "kind": "user_stop",
            "title": "GraphFlow stopped",
            "reason": "The run was stopped by the user.",
            "source": "user",
            "resumable": False,
        },
    }
    try:
        snapshot = db_jobs.control_run(
            run_id, str(run["project_session_id"]), "stop", json.dumps(output)
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "run_id": run_id,
        "status": "cancelled",
        "snapshot": snapshot,
        "killed_processes": oc_registry.kill_workflow_run(run_id),
    }


@flow01_router.post("/runs/{run_id}/cancel")
def flow01_cancel_run(run_id: int, body: dict | None = None) -> dict:
    """Mark a flow_01 run as 'cancelled'. The next graph tick will
    route to END; _record_directive_lifecycle maps this status to
    directive=processed (clean exit on user intent) with an
    explanatory error.

    Migration audit symbol 323: accept an optional {reason} body and
    persist it into output_json so operators can see WHY a run was
    cancelled without grepping the server log.
    """
    run, scope_error = _scoped_run(run_id, (body or {}).get("project_session_id"))
    if scope_error:
        return {"ok": False, "error": scope_error}
    reason = ""
    if isinstance(body, dict):
        reason = (body.get("reason") or "").strip()
    output_json: dict = {"status": "cancelled"}
    output_json["reason"] = reason or "cancelled_by_user"
    output_json["interruption"] = {
        "kind": "user_cancel",
        "title": "GraphFlow cancelled",
        "reason": reason or "The run was cancelled by the user.",
        "source": "user",
        "resumable": False,
    }
    try:
        snapshot = db_jobs.control_run(
            run_id, str(run["project_session_id"]), "stop", json.dumps(output_json)
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    killed_processes = oc_registry.kill_workflow_run(run_id)
    return {
        "ok": True,
        "run_id": run_id,
        "status": "cancelled",
        "output_json": output_json,
        "snapshot": snapshot,
        "killed_processes": killed_processes,
    }


# tA1d: pause / resume per-run routes

@flow01_router.post("/runs/{run_id}/pause")
def flow01_pause_run(run_id: int, body: dict | None = None) -> dict:
    run, scope_error = _scoped_run(run_id, (body or {}).get("project_session_id"))
    if scope_error:
        return {"ok": False, "error": scope_error}
    step_name = (body or {}).get("step_name") if body else None
    output = {
            "status": "paused",
            "interruption": {
                "kind": "user_pause",
                "title": "GraphFlow paused",
                "reason": f"Paused by user{f' before {step_name}' if step_name else ''}.",
                "source": "user",
                "resumable": True,
            },
        }
    try:
        snapshot = db_jobs.control_run(
            run_id, str(run["project_session_id"]), "pause", json.dumps(output)
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "run_id": run_id, "status": "paused", "snapshot": snapshot}


@flow01_router.post("/runs/{run_id}/resume")
def flow01_resume_run(run_id: int, body: dict | None = None) -> dict:
    """Resume a paused flow_01 run by loading its latest checkpoint.

    Unlike the previous status-flip stub, this route delegates to
    ``graph.resume_loop(run_id)`` which:
      1. Loads the latest flow_checkpoints row for this run
      2. Reconstructs FlowInput from the workflow_runs row +
         project_sessions
      3. Queues the durable worker to run the graph starting at the
         node AFTER checkpoint.step_name,
         so already-completed nodes are NOT re-executed.
    The HTTP response is returned immediately (202-style);
    the actual graph runs in the background.

    Repeated calls on a non-paused run return
    ``ok: False`` with a descriptive error (no DB mutation).
    """
    run, scope_error = _scoped_run(run_id, (body or {}).get("project_session_id"))
    if scope_error:
        return {"ok": False, "error": scope_error}
    status = str(run.get("status", "")).lower()
    if not (
        status == "paused"
        or status.startswith("paused_before_")
        or (status == "technical_error" and db_wf.load_checkpoint(run_id) is not None)
    ):
        return {
            "ok": False,
            "error": f"run {run_id} not in paused state (status={run.get('status')!r})",
            "run_id": run_id,
            "current_status": run.get("status"),
        }
    output = json.dumps({"status": "recovering"}, ensure_ascii=False)
    try:
        snapshot = db_jobs.control_run(
            run_id, str(run["project_session_id"]), "resume", output
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    job = snapshot["job"]
    return {
        "ok": True,
        "run_id": run_id,
        "status": job["status"],
        "job_id": job["id"],
        "snapshot": snapshot,
    }


@flow01_router.post("/runs/{run_id}/start")
def flow01_start_run(body: dict) -> dict:
    """Start a new flow_01 run in its own thread (tA1f).

    Creates a workflow_runs row, then enqueues it for the standalone
    GraphFlow worker. The HTTP response returns immediately.
    """
    project_session_id = str(body.get("project_session_id", "")).strip()
    human_directive = str(body.get("human_directive", "")).strip()
    if not project_session_id or not human_directive:
        return {"ok": False, "error": "project_session_id and human_directive are required"}
    round_state = db_rounds.ensure_round(project_session_id, human_directive)
    if round_state.get("status") == "locked":
        if not bool(body.get("create_new_round")):
            return {
                "ok": False,
                "error_code": "round_locked",
                "error": "The previous directive is complete. Start requires a new directive.",
                "round": round_state,
            }
        if human_directive.strip() == str(round_state.get("directive") or "").strip():
            return {
                "ok": False,
                "error_code": "new_directive_required",
                "error": "The new directive must differ from the completed directive.",
                "round": round_state,
            }
        round_state = db_rounds.create_next_round(project_session_id, human_directive)
    active_statuses = {
        "pending", "running", "recovering", "pausing", "paused",
        "stopping", "cancelling",
    }
    active_for_project = [
        run for run in db_wf.list_workflow_runs(project_session_id, limit=50)
        if str(run.get("status") or "").lower() in active_statuses
    ]
    if active_for_project:
        return {
            "ok": False,
            "error": f"session {project_session_id} already has active run {active_for_project[0]['id']}",
            "run_id": active_for_project[0]["id"],
        }
    capacity = wf_capacity.snapshot()
    if not capacity.ok:
        return {
            "ok": False,
            "error_code": "capacity_unavailable",
            "error": capacity.reason or "GraphFlow capacity is unavailable.",
            "capacity": capacity.as_dict(),
        }
    workspace_path = str(body.get("workspace_path", "") or "")
    try:
        run_id = db_wf.create_workflow_run(
            session_id=project_session_id,
            power_team_project_id=str(body.get("power_team_project_id", f"pt_{project_session_id}")),
            loop_index=int(body.get("loop_index", 0)),
            status="running",
            input_json=json.dumps(
                {"human_directive": human_directive, "workspace_path": workspace_path},
                ensure_ascii=False,
            ),
            output_json="{}",
            manager_session_id=body.get("manager_session_id"),
            worker_session_id=body.get("worker_session_id"),
            reviewer_session_id=body.get("reviewer_session_id"),
        )
    except Exception as exc:
        return {"ok": False, "error": f"could not create workflow_runs row: {exc!r}"}

    job = db_jobs.enqueue(run_id, project_session_id, "start")
    if int(job["run_id"]) != run_id:
        db_wf.update_workflow_run_status(
            run_id,
            "cancelled",
            output_json=json.dumps({
                "status": "cancelled",
                "interruption": {
                    "kind": "duplicate_start",
                    "title": "GraphFlow start was deduplicated",
                    "reason": f"Session already has active run {job['run_id']}.",
                    "source": "job_queue",
                    "resumable": False,
                },
            }),
        )
        return {
            "ok": False,
            "error": f"session {project_session_id} already has active run {job['run_id']}",
            "run_id": job["run_id"],
            "job_id": job["id"],
        }
    return {
        "ok": True,
        "run_id": run_id,
        "status": job["status"],
        "job_id": job["id"],
    }


@flow01_router.post("/execution/command")
def flow01_execution_command(body: dict) -> dict:
    """Apply one UI execution intent and return the authoritative state."""
    action = str(body.get("action") or "").strip().lower()
    if action in {"start_fresh", "run_once"}:
        result = flow01_start_run(body)
        if not result.get("ok"):
            return result
        run = db_wf.get_workflow_run(int(result["run_id"]))
        job = db_jobs.get_for_run(int(result["run_id"]))
        return {
            **result,
            "action": action,
            "message": (
                "GraphFlow run started."
                if action == "start_fresh"
                else "One GraphFlow run started."
            ),
            "snapshot": {
                "timeline_state": "start_fresh",
                "run": run,
                "job": job,
            },
        }

    run_id = int(body.get("run_id") or 0)
    if run_id <= 0:
        return {"ok": False, "error": "run_id is required"}
    command_body = {"project_session_id": body.get("project_session_id")}
    if action == "pause":
        result = flow01_pause_run(run_id, command_body)
        message = "GraphFlow paused at the latest complete checkpoint."
        timeline_state = "resume"
    elif action == "resume":
        result = flow01_resume_run(run_id, command_body)
        message = "GraphFlow queued to resume from its checkpoint."
        timeline_state = "resume"
    elif action == "stop":
        command_body["reason"] = str(body.get("reason") or "ui_stop_button")
        result = flow01_cancel_run(run_id, command_body)
        message = "GraphFlow stopped."
        timeline_state = "finished"
    else:
        return {
            "ok": False,
            "error": "action must be start_fresh, run_once, pause, resume, or stop",
        }
    if not result.get("ok"):
        return result
    return {
        **result,
        "action": action,
        "message": message,
        "snapshot": {
            **(result.get("snapshot") or {}),
            "timeline_state": timeline_state,
        },
    }


@flow01_router.get("/rounds/current")
def flow01_current_round(session_id: str | None = Query(default=None)) -> dict:
    sid = resolve_session_id(session_id)
    if not sid:
        return {}
    return db_rounds.current_round(sid) or {}


@flow01_router.post("/rounds/new")
def flow01_new_round(body: dict) -> dict:
    sid = require_session_id(body.get("project_session_id"))
    directive = str(body.get("directive") or "").strip()
    if not directive:
        return {"ok": False, "error": "directive is required"}
    current = db_rounds.current_round(sid)
    if current and current.get("status") != "locked":
        return {"ok": False, "error": "current round is not locked"}
    if current and directive == str(current.get("directive") or "").strip():
        return {"ok": False, "error": "new directive must differ from the completed directive"}
    return {"ok": True, "round": db_rounds.create_next_round(sid, directive)}


@flow01_router.get("/runs/active")
def flow01_active_runs() -> list[dict]:
    """List durable runs currently queued or owned by the worker."""
    return db_jobs.active()


@flow01_router.get("/executions")
def flow01_executions(
    project_session_id: str | None = Query(default=None),
    workflow_run_id: int | None = Query(default=None),
) -> list[dict]:
    sid = resolve_session_id(project_session_id)
    if not sid:
        return []
    return db_execution.list_executions(sid, workflow_run_id)


@flow01_router.post("/executions/{execution_id}/stop")
def flow01_stop_execution(execution_id: str) -> dict:
    executions = db_execution.list_executions(resolve_session_id(None))
    target = next((item for item in executions if item["execution_id"] == execution_id), None)
    if target is None:
        return {"ok": False, "error": "execution does not belong to current session"}
    killed = oc_registry.kill_execution(execution_id)
    db_execution.upsert_execution(
        execution_id=execution_id,
        project_session_id=target["project_session_id"],
        workflow_run_id=target.get("workflow_run_id"),
        role=target["role"],
        status="stopped",
    )
    return {"ok": True, "execution_id": execution_id, "killed": killed}


@flow01_router.get("/runs/{run_id}")
def flow01_get_run(run_id: int) -> dict:
    run = db_wf.get_workflow_run(run_id)
    if not run:
        return {"ok": False, "error": "flow_01 run not found", "run_id": run_id}
    try:
        input_json = json.loads(run.get("input_json") or "{}")
    except Exception:
        input_json = {}
    try:
        output_json = json.loads(run.get("output_json") or "{}")
    except Exception:
        output_json = {}
    return {
        "ok": True,
        "flow": "flow_01",
        "run": run,
        "input": input_json,
        "output": output_json,
    }


@flow01_router.get("/runs/{run_id}/status")
def flow01_run_status(run_id: int) -> dict:
    run = db_wf.get_workflow_run(run_id)
    if not run:
        return {"ok": False, "error": f"run {run_id} not found"}
    status = str(run.get("status", "")).lower()
    session_id = run.get("project_session_id") or run.get("session_id")
    manager_msgs = db_wf.list_manager_messages(session_id, limit=1) if session_id else []
    worker_reports = db_wf.list_worker_reports(session_id, limit=1) if session_id else []
    reviewer = db_wf.get_latest_reviewer_session(session_id) if session_id else None
    checkpoint = db_wf.load_checkpoint(run_id)
    checkpoint_state = {}
    if checkpoint and checkpoint.get("state_json"):
        try:
            parsed = json.loads(checkpoint.get("state_json") or "{}")
            checkpoint_state = parsed if isinstance(parsed, dict) else {}
        except Exception:
            checkpoint_state = {}
    worker_report = worker_reports[0] if worker_reports else None
    error_summary = ""
    if reviewer and (reviewer.get("error") or reviewer.get("review_notes")):
        error_summary = str(reviewer.get("error") or reviewer.get("review_notes") or "")
    elif checkpoint_state.get("reviewer_feedback"):
        error_summary = str(checkpoint_state.get("reviewer_feedback") or "")
    elif checkpoint_state.get("worker_test_result"):
        error_summary = f"worker_test_result={checkpoint_state.get('worker_test_result')}"
    return {
        "ok": True,
        "run_id": run_id,
        "status": status,
        "progress": None,
        "manager_message": manager_msgs[0].get("content", "") if manager_msgs else None,
        "worker_report": worker_report,
        "reviewer": reviewer,
        "diagnostics": {
            "session_id": session_id,
            "last_step": checkpoint.get("step_name") if checkpoint else None,
            "last_checkpoint_id": checkpoint.get("id") if checkpoint else None,
            "worker_test_result": (
                worker_report.get("test_result") if worker_report
                else checkpoint_state.get("worker_test_result")
            ),
            "reviewer_qa_result": checkpoint_state.get("reviewer_qa_result"),
            "reviewer_feedback": checkpoint_state.get("reviewer_feedback"),
            "error_summary": error_summary[:1000] if error_summary else "",
        },
        "last_updated": run.get("updated_at"),
    }


@flow01_router.get("/runs/{run_id}/stream")
async def flow01_run_stream(run_id: int):
    from fastapi.responses import StreamingResponse
    import asyncio

    run = db_wf.get_workflow_run(run_id)
    if not run:
        return {"ok": False, "error": f"run {run_id} not found"}

    async def event_generator():
        session_id = run.get("session_id")
        last_msg_id = 0
        last_report_id = 0

        while True:
            if asyncio.current_task().cancelled():
                break
            run = db_wf.get_workflow_run(run_id)
            if not run or run.get("status") in ("completed", "failed", "cancelled"):
                yield f"data: {{'type': 'status_update', 'status': '{run.get('status') if run else 'unknown'}', 'done': True}}\n\n"
                break
            manager_msgs = db_wf.list_manager_messages(session_id, limit=5)
            for msg in manager_msgs[last_msg_id:]:
                yield f"data: {{'type': 'manager_message', 'content': {repr(msg.get('content', '')[:500])}, 'timestamp': '{msg.get('created_at', '')}'}}\n\n"
            if manager_msgs:
                last_msg_id = len(manager_msgs)
            worker_reports = db_wf.list_worker_reports(session_id, limit=5)
            for rep in worker_reports[last_report_id:]:
                yield f"data: {{'type': 'worker_output', 'content': {repr(str(rep)[:500])}, 'timestamp': '{rep.get('created_at', '')}'}}\n\n"
            if worker_reports:
                last_report_id = len(worker_reports)
            yield f"data: {{'type': 'status_update', 'status': '{run.get('status')}', 'done': False}}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@flow01_router.post("/start-loop")
def flow01_start_loop() -> dict:
    return workflow_start_loop()


# ── Handoff ────────────────────────────────────────────────────────────────

@router.get("/handoff")
def get_handoff(session_id: str | None = Query(default=None)) -> dict | None:
    sid = resolve_session_id(session_id)
    if not sid:
        return {}
    return db_wf.get_handoff(sid) or {}


@router.put("/handoff")
def put_handoff(body: dict, session_id: str | None = Query(default=None)) -> dict:
    sid = require_session_id(session_id)
    db_wf.upsert_handoff(sid, **body)
    handoff = db_wf.get_handoff(sid)
    version = handoff.get("version") if handoff else None
    return {"ok": True, "version": version, "session_id": sid}


# ── Directives ─────────────────────────────────────────────────────────────

@router.post("/directive")
def create_directive(body: schemas.DirectiveCreate) -> dict:
    sid = require_session_id(body.session_id)
    did = db_chat.create_directive(sid, body.directive)
    return {"id": did, "session_id": sid}


@router.get("/directives")
def list_directives(
    session_id: str | None = Query(default=None),
    limit: int = 20,
) -> list[dict]:
    sid = resolve_session_id(session_id)
    if not sid:
        return []
    return db_chat.list_directives(sid, limit=limit)
