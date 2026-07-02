"""workflow.graph — LangGraph state machine.

Nodes (in order):
  1. start           — read DB, load existing context, build initial FlowState
  2. manager_digest  — read directive + existing progress, write digest
  3. manager_plan    — write plan
  4. manager_todo    — write todo list
  5. manager_select  — pick one task
  6. manager_release — write manager message + handoff
  7. worker_execute  — execute one task, write report
  8. reviewer_check  — review, write feedback

Each node reads DB at start and writes DB at end.

Resume semantics: the initial state may carry a ``__resume_state__``
key holding a deserialized checkpoint dict. ``_node_start`` checks
for this and routes directly to the node AFTER
``__resume_state__["step_name"]`` instead of always re-running
manager_digest. The resumed graph runs from the resume point forward,
honoring the same _check_pause / _check_cancel / stop-signal
semantics as a fresh run.
"""
from __future__ import annotations

import json
import logging
from typing import Any

try:
    from langgraph.graph import END, StateGraph
except ImportError:
    END = None
    StateGraph = None

from task_hounds_api.db.ops import workflow as db_wf
from task_hounds_api.workflow import executor as ex
from task_hounds_api.workflow import models as M
from task_hounds_api.workflow.cancellation import (
    Flow01CancellationToken,
    _PauseRequestedError,
)

logger = logging.getLogger(__name__)


# Order in which the graph runs the step nodes. Used by
# ``_next_node_after`` to compute the resume target.
_NODE_ORDER = [
    "manager_digest",
    "manager_plan",
    "manager_todo",
    "manager_select",
    "manager_release",
    "worker_execute",
    "reviewer_check",
    "manager_reconcile",
    "manager_brainstorm",
]


def _check_pause(state: M.FlowState, step_name: str) -> None:
    """tA1b: if the operator paused this run before the named step,
    raise _PauseRequestedError so the graph stops cleanly with a
    checkpoint. The step-name match lets the operator pause at a
    specific node boundary (``paused_before_manager_todo`` only
    fires at manager_todo, not at manager_digest).

    Side effect: writes a checkpoint row tagged with the pause-
    boundary step_name BEFORE raising. Without this side effect,
    load_checkpoint would return the PREVIOUS step's row and
    resume_loop would land on the same step it just paused at,
    looping forever.
    """
    token = Flow01CancellationToken(state.flow_input.run_id)
    if token.paused(step_name=step_name):
        ex.checkpoint(state, step_name)
        raise _PauseRequestedError(step_name)


def _check_cancel(state: M.FlowState) -> None:
    """tA1c: hard stop on cancellation. Distinct from pause (which is
    resumable); cancel ends the run permanently with status="cancelled".
    """
    token = Flow01CancellationToken(state.flow_input.run_id)
    if token.cancelled():
        state.status = "cancelled"
        raise _PauseRequestedError("__cancelled__")  # same shape; caller distinguishes


# Graph state — mirrors FlowState but as a TypedDict for LangGraph
GraphState = dict[str, Any]


def _state_to_dict(s: M.FlowState) -> GraphState:
    return M.state_to_dict(s)


def _state_from_dict(d: GraphState) -> M.FlowState:
    return M.state_from_dict(d)


# ── Node functions ──────────────────────────────────────────────────────────

MAX_DIGEST_RETRIES = 3


def _bump_retry(state: M.FlowState) -> None:
    state.__digest_retry__ += 1
    if state.__digest_retry__ >= MAX_DIGEST_RETRIES:
        state.status = "failed"


def _next_node_after(step_name: str) -> str:
    """The graph node to run after ``step_name`` in the linear
    pipeline. Unknown step names fall back to manager_digest (the
    worst case is a full redo, which is safe but wasteful).
    """
    if step_name in _NODE_ORDER:
        idx = _NODE_ORDER.index(step_name)
        if idx + 1 < len(_NODE_ORDER):
            return _NODE_ORDER[idx + 1]
    return "manager_digest"


def _deserialize_resume_state(resume_dict: dict, fi: M.FlowInput) -> M.FlowState:
    """Reconstruct a FlowState from a saved checkpoint dict.

    The checkpoint's ``state_json`` was written by
    ``executor.checkpoint``; it contains the inner state fields
    (plan, manager_message, todo_list, etc.) but not the
    FlowInput. The FlowInput is reconstructed from
    workflow_runs + project_sessions in
    ``_reconstruct_flow_input_from_run``; here we just hydrate
    the FlowState's instance attributes.
    """
    state = M.FlowState(
        flow_input=fi,
        loop_input=M.FlowLoopInput(),
        status=str(resume_dict.get("status", "pending")),
        input_digest=str(resume_dict.get("input_digest", "")),
        decision=resume_dict.get("decision") or {},
        manager_message=str(resume_dict.get("manager_message", "")),
        plan=str(resume_dict.get("plan", "")),
        todo_list=resume_dict.get("todo_list") or [],
        todo_update_json=resume_dict.get("todo_update_json") or {"items": []},
        suggestion_content=str(resume_dict.get("suggestion_content", "")),
        suggestion_verification=str(resume_dict.get("suggestion_verification", "")),
        current_todo_id=str(resume_dict.get("current_todo_id", "")),
        archive_updates=resume_dict.get("archive_updates") or [],
        reopen_todos=resume_dict.get("reopen_todos") or [],
        handoff_update=resume_dict.get("handoff_update") or {},
        worker_report=str(resume_dict.get("worker_report", "")),
        worker_files_changed=resume_dict.get("worker_files_changed") or [],
        worker_test_result=str(resume_dict.get("worker_test_result", "")),
        worker_known_issues=resume_dict.get("worker_known_issues") or [],
        reviewer_feedback=str(resume_dict.get("reviewer_feedback", "")),
        reviewer_qa_result=str(resume_dict.get("reviewer_qa_result", "needs_review")),
        reviewer_bugs=resume_dict.get("reviewer_bugs") or [],
        reviewer_uiux=resume_dict.get("reviewer_uiux") or [],
        reviewer_possible_problems=resume_dict.get("reviewer_possible_problems") or [],
        reviewer_safety_security_risks=resume_dict.get("reviewer_safety_security_risks") or [],
        suggestion_id=resume_dict.get("suggestion_id"),
    )
    state.step_index = int(resume_dict.get("step_index", 0))
    state.step_name = str(resume_dict.get("step_name", ""))
    return state


def _reconstruct_flow_input_from_run(run: dict) -> M.FlowInput:
    """Rebuild a FlowInput from a workflow_runs row.

    ``input_json`` carries the human_directive (and any other
    fields the original run_loop() caller stored at create time).
    workspace_path comes from project_sessions because the run
    row does not persist it. Other FlowInput fields
    (human_new_thought_and_suggestion, etc.) default to "" —
    they were consumed by the early nodes we will SKIP on resume,
    so the values no longer matter for downstream nodes.
    """
    try:
        input_dict = json.loads(run.get("input_json", "") or "{}")
    except (json.JSONDecodeError, TypeError):
        input_dict = {}

    workspace_path = ""
    try:
        from task_hounds_api.db.ops.project import get_session
        project = get_session(run["project_session_id"])
        if project:
            workspace_path = project.get("workspace_path") or ""
    except Exception:
        pass
    if not workspace_path:
        workspace_path = str(input_dict.get("workspace_path", "") or "")

    return M.FlowInput(
        power_team_project_id=str(run.get("power_team_project_id", "")),
        project_session_id=str(run.get("project_session_id", "")),
        human_directive=str(input_dict.get("human_directive", "")),
        workspace_path=workspace_path,
        manager_opencode_session_id=run.get("manager_opencode_session_id"),
        worker_opencode_session_id=run.get("worker_opencode_session_id"),
        reviewer_opencode_session_id=run.get("reviewer_opencode_session_id"),
        run_id=int(run["id"]),
    )


def _node_start(raw: GraphState) -> GraphState:
    """Initial node. On a fresh run, reads DB to build FlowState.
    On a resume, the initial state carries ``__resume_state__`` —
    we deserialize that, then ``__route__`` to the next node after
    ``step_name`` so already-completed nodes are skipped.
    """
    fi = M.FlowInput(**raw["flow_input"]) if isinstance(raw.get("flow_input"), dict) else raw["flow_input"]

    resume_state = raw.get("__resume_state__")
    if resume_state:
        state = _deserialize_resume_state(resume_state, fi)
        next_node = _next_node_after(str(resume_state.get("step_name", "")))
        return {
            "__route__": next_node,
            "step_name": state.step_name,
            "step_index": state.step_index,
            **_state_to_dict(state),
        }

    li = M.FlowLoopInput(**raw.get("loop_input", {}))
    state = ex.state_from_db(fi, li)
    return _state_to_dict(state)


def _node_manager_digest(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    _check_pause(state, "manager_digest")
    _check_cancel(state)
    fi = state.flow_input
    ex.set_agent_state_safe(
        "manager", "busy", "digest",
        project_session_id=fi.project_session_id,
        role_session_id=fi.manager_opencode_session_id,
        workflow_run_id=fi.run_id,
    )
    state = ex.manager_digest(state)
    ex.checkpoint(state, "manager_digest")
    return _state_to_dict(state)


def _node_manager_plan(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    _check_pause(state, "manager_plan")
    _check_cancel(state)
    state = ex.manager_plan(state)
    ex.checkpoint(state, "manager_plan")
    if not state.plan.strip():
        _bump_retry(state)
        return {"__route__": "manager_digest", **_state_to_dict(state)}
    return _state_to_dict(state)


def _node_manager_todo(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    _check_pause(state, "manager_todo")
    _check_cancel(state)
    if not state.plan.strip():
        _bump_retry(state)
        return {"__route__": "manager_digest", **_state_to_dict(state)}
    state = ex.manager_todo(state)
    ex.checkpoint(state, "manager_todo")
    if not state.todo_list:
        _bump_retry(state)
        return {"__route__": "manager_digest", **_state_to_dict(state)}
    return _state_to_dict(state)


def _node_manager_select(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    _check_pause(state, "manager_select")
    _check_cancel(state)
    if not state.todo_list:
        _bump_retry(state)
        return {"__route__": "manager_digest", **_state_to_dict(state)}
    state = ex.manager_select_task(state)
    ex.checkpoint(state, "manager_select")
    if str(state.status).lower() == "paused":
        return {"__route__": "__stop_signal__", **_state_to_dict(state)}
    if not state.suggestion_content.strip():
        _bump_retry(state)
        return {"__route__": "manager_digest", **_state_to_dict(state)}
    return _state_to_dict(state)


def _node_manager_release(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    _check_pause(state, "manager_release")
    _check_cancel(state)
    fi = state.flow_input
    if not state.suggestion_content.strip():
        _bump_retry(state)
        return {"__route__": "manager_digest", **_state_to_dict(state)}
    state = ex.manager_release(state)
    ex.set_agent_state_safe(
        "manager", "idle",
        project_session_id=fi.project_session_id,
        role_session_id=fi.manager_opencode_session_id,
        workflow_run_id=fi.run_id,
    )
    ex.checkpoint(state, "manager_release")
    # Stop signal check: manager_plan (called transitively via manager_release's
    # underlying _call_manager) may have set state.status = "completed" with
    # a stop_signal in handoff. If so, route to END so we skip worker_execute
    # and reviewer_check.
    if str(state.status).lower() == "completed":
        return {"__route__": "__stop_signal__", **_state_to_dict(state)}
    return _state_to_dict(state)


def _node_worker_execute(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    _check_pause(state, "worker_execute")
    _check_cancel(state)
    fi = state.flow_input
    ex.set_agent_state_safe(
        "worker", "busy", state.suggestion_content[:80],
        project_session_id=fi.project_session_id,
        role_session_id=fi.worker_opencode_session_id,
        workflow_run_id=fi.run_id,
    )
    state = ex.worker_execute(state)
    ex.set_agent_state_safe(
        "worker", "idle",
        project_session_id=fi.project_session_id,
        role_session_id=fi.worker_opencode_session_id,
        workflow_run_id=fi.run_id,
    )
    ex.checkpoint(state, "worker_execute")
    return _state_to_dict(state)


def _node_reviewer_check(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    _check_pause(state, "reviewer_check")
    _check_cancel(state)
    fi = state.flow_input
    ex.set_agent_state_safe(
        "reviewer", "busy", "checking",
        project_session_id=fi.project_session_id,
        role_session_id=fi.reviewer_opencode_session_id,
        workflow_run_id=fi.run_id,
    )
    state = ex.reviewer_check(state)
    ex.set_agent_state_safe(
        "reviewer", "idle",
        project_session_id=fi.project_session_id,
        role_session_id=fi.reviewer_opencode_session_id,
        workflow_run_id=fi.run_id,
    )
    ex.checkpoint(state, "reviewer_check")
    return _state_to_dict(state)


def _node_manager_reconcile(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    _check_pause(state, "manager_reconcile")
    _check_cancel(state)
    state = ex.manager_reconcile(state)
    ex.checkpoint(state, "manager_reconcile")
    return _state_to_dict(state)


def _node_manager_brainstorm(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    _check_pause(state, "manager_brainstorm")
    _check_cancel(state)
    state = ex.manager_brainstorm(state)
    ex.checkpoint(state, "manager_brainstorm")
    if state.__done_signal__:
        return {"__route__": END, **_state_to_dict(state)}
    if state.todo_list:
        return {"__route__": "manager_todo", **_state_to_dict(state)}
    state.__done_signal__ = True
    return {"__route__": END, **_state_to_dict(state)}


# ── Router ──────────────────────────────────────────────────────────────────

def _route_after(raw: GraphState) -> str:
    """If a node returned __route__, follow it. Otherwise continue.

    If we've been looping back to manager_digest more than
    MAX_DIGEST_RETRIES times and the next node still wants to loop,
    give up and route to END with status='failed' so the caller can
    mark the directive as failed.
    """
    if raw.get("__route__") == "manager_digest" and raw.get("__digest_retry__", 0) >= MAX_DIGEST_RETRIES:
        return "__give_up__"
    if raw.get("__route__"):
        return raw["__route__"]
    return "continue"


# Phase-8 (P0-2): short-circuit the graph when the Worker fails.
# The Reviewer's defensive check is a second line of defense, but
# the graph-level gate is the primary fix for the audit's
# reproduced silent-failure bug.
def _route_after_worker(raw: GraphState) -> str:
    return "reviewer_check"


def _route_after_reviewer(raw: GraphState) -> str:
    return "manager_reconcile"


def _route_after_manager_reconcile(raw: GraphState) -> str:
    if str(raw.get("status", "")).lower() in {"paused", "completed", "cancelled"}:
        return END
    todo_list = raw.get("todo_list", [])
    has_pending = any(
        t.get("status") in ("pending", "in_progress")
        for t in todo_list
    )
    if has_pending:
        return "manager_select"
    return END


# ── Build graph ─────────────────────────────────────────────────────────────

def build_graph():
    if StateGraph is None:
        raise RuntimeError(
            "flow_01 requires langgraph. Install with `pip install langgraph`."
        )
    g = StateGraph(GraphState)
    g.add_node("start", _node_start)
    g.add_node("manager_digest", _node_manager_digest)
    g.add_node("manager_plan", _node_manager_plan)
    g.add_node("manager_todo", _node_manager_todo)
    g.add_node("manager_select", _node_manager_select)
    g.add_node("manager_release", _node_manager_release)
    g.add_node("worker_execute", _node_worker_execute)
    g.add_node("reviewer_check", _node_reviewer_check)
    g.add_node("manager_reconcile", _node_manager_reconcile)
    g.add_node("manager_brainstorm", _node_manager_brainstorm)
    g.set_entry_point("start")
    # tA2d: conditional edge from start (not the unconditional one
    # used previously) so _node_start can __route__ to ANY node
    # when resuming from a checkpoint. The "continue" case (no
    # __route__) is the normal fresh-run path.
    g.add_conditional_edges("start", _route_after, {
        "manager_digest": "manager_digest",
        "manager_plan": "manager_plan",
        "manager_todo": "manager_todo",
        "manager_select": "manager_select",
        "manager_release": "manager_release",
        "worker_execute": "worker_execute",
        "reviewer_check": "reviewer_check",
        "manager_reconcile": "manager_reconcile",
        "__give_up__": END,
        "continue": "manager_digest",
    })
    g.add_conditional_edges("manager_digest", _route_after, {
        "manager_plan": "manager_plan",
        "manager_digest": "manager_digest",
        "__give_up__": END,
        "continue": "manager_plan",
    })
    g.add_conditional_edges("manager_plan", _route_after, {
        "manager_todo": "manager_todo",
        "manager_digest": "manager_digest",
        "__give_up__": END,
        "continue": "manager_todo",
    })
    g.add_conditional_edges("manager_todo", _route_after, {
        "manager_select": "manager_select",
        "manager_digest": "manager_digest",
        "__give_up__": END,
        "continue": "manager_select",
    })
    g.add_conditional_edges("manager_select", _route_after, {
        "manager_release": "manager_release",
        "manager_digest": "manager_digest",
        "__give_up__": END,
        "__stop_signal__": END,
        "continue": "manager_release",
    })
    # Stop-signal short-circuit: manager_release is the only branch
    # point between the Manager chain and the Worker. The conditional
    # edge handles BOTH the normal "continue" case (route to
    # worker_execute) AND the __stop_signal__ case (route to END
    # when the manager emitted TASK_HOUNDS_STOP_LOOP or
    # DIRECTIVE_COMPLETE). Do NOT also declare a plain
    # g.add_edge("manager_release", "worker_execute") — that would
    # be a second source-of-truth for the manager_release->worker
    # transition and would short-circuit the conditional, defeating
    # the stop-signal semantics.
    g.add_conditional_edges("manager_release", _route_after, {
        "worker_execute": "worker_execute",
        "__stop_signal__": END,
        "manager_digest": "manager_digest",
        "__give_up__": END,
        "continue": "worker_execute",
    })
    g.add_conditional_edges("worker_execute", _route_after_worker, {
        "reviewer_check": "reviewer_check",
    })
    g.add_conditional_edges("reviewer_check", _route_after_reviewer, {
        "manager_reconcile": "manager_reconcile",
    })
    g.add_conditional_edges("manager_reconcile", _route_after_manager_reconcile, {
        "manager_select": "manager_select",
        END: END,
    })
    g.add_conditional_edges("manager_brainstorm", _route_after, {
        "manager_todo": "manager_todo",
        END: END,
        "__give_up__": END,
        "continue": END,
        "manager_digest": "manager_digest",
    })
    return g.compile()


# ── Public entry points ────────────────────────────────────────────────────

def run_loop(flow_input: M.FlowInput, loop_input: M.FlowLoopInput | None = None) -> dict:
    """Run one full Manager/Worker/Reviewer loop. Returns the final state dict.

    Creates a workflow_runs row so each run has a stable run_id for
    checkpoint persistence.

    If the operator pauses the run mid-graph (status flips to
    'paused_before_*'), a node raises _PauseRequestedError. We
    catch it here, write a checkpoint at the pause boundary
    (already done by ex.checkpoint before the raise), and return
    the partial result dict with status='paused'. The API layer
    can return 202 Accepted in that case; the operator then hits
    /resume to continue from the checkpoint.
    """
    M.validate_flow_input(flow_input)
    if flow_input.run_id is None:
        try:
            flow_input.run_id = db_wf.create_workflow_run(
                session_id=flow_input.project_session_id,
                power_team_project_id=flow_input.power_team_project_id,
                loop_index=(loop_input.loop_index if loop_input else 0),
                status="running",
                input_json=json.dumps(
                    {"human_directive": flow_input.human_directive},
                    ensure_ascii=False,
                ),
                output_json="{}",
                manager_session_id=flow_input.manager_opencode_session_id,
                worker_session_id=flow_input.worker_opencode_session_id,
                reviewer_session_id=flow_input.reviewer_opencode_session_id,
            )
        except Exception:
            # Best-effort: continue without run_id (checkpoints are
            # skipped when run_id is None; the run still completes).
            pass
    graph_inst = build_graph()
    li = loop_input or M.FlowLoopInput()
    initial = _state_to_dict(M.FlowState(flow_input=flow_input, loop_input=li))
    try:
        result = graph_inst.invoke(initial)
    except _PauseRequestedError as pause:
        step = pause.step_name
        if step == "__cancelled__":
            return {
                "status": "cancelled",
                "reason": "cancelled_by_user",
                "run_id": flow_input.run_id,
                "project_session_id": flow_input.project_session_id,
            }
        # Mark the run as 'paused' (drop the paused_before_ prefix
        # so the token's paused() check fires on subsequent ticks).
        if flow_input.run_id is not None:
            try:
                db_wf.update_workflow_run_status(
                    flow_input.run_id,
                    "paused",
                    output_json=json.dumps({
                        "status": "paused",
                        "interruption": {
                            "kind": "mechanism_pause",
                            "title": "GraphFlow paused",
                            "reason": f"Paused before step: {step}",
                            "source": "loop_mechanism",
                            "resumable": True,
                        },
                    }),
                )
            except Exception:
                pass
        return {
            "status": "paused",
            "paused_before": step,
            "run_id": flow_input.run_id,
            "project_session_id": flow_input.project_session_id,
        }
    except Exception as exc:
        if flow_input.run_id is not None:
            try:
                db_wf.update_workflow_run_status(
                    flow_input.run_id,
                    "technical_error",
                    output_json=json.dumps({
                        "status": "technical_error",
                        "interruption": {
                            "kind": "technical_error",
                            "title": "GraphFlow interrupted by an error",
                            "reason": str(exc) or type(exc).__name__,
                            "source": "loop_mechanism",
                            "resumable": bool(db_wf.load_checkpoint(flow_input.run_id)),
                        },
                    }, ensure_ascii=False),
                )
            except Exception:
                pass
        raise
    # Final state write: mark the run as completed/failed.
    if flow_input.run_id is not None:
        try:
            handoff = result.get("handoff_update") or {}
            stop_signal = handoff.get("stop_signal") if isinstance(handoff, dict) else None
            if stop_signal:
                result["interruption"] = {
                    "kind": "manager_stop",
                    "title": "Manager ended GraphFlow",
                    "reason": result.get("manager_message") or f"Manager emitted {stop_signal}",
                    "source": "manager",
                    "resumable": False,
                    "stop_signal": stop_signal,
                }
            elif str(result.get("status", "")).lower() == "completed":
                unresolved = [
                    todo for todo in (result.get("todo_list") or [])
                    if todo.get("status") == "completed"
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
                if unresolved:
                    result["status"] = "completed_with_unresolved_evidence"
                    result["interruption"] = {
                        "kind": "manager_completion_with_unresolved_evidence",
                        "title": "Manager ended GraphFlow with unresolved evidence",
                        "reason": (
                            result.get("manager_message")
                            or "Manager marked the run completed despite unresolved Worker or Reviewer evidence."
                        ),
                        "source": "manager",
                        "resumable": False,
                        "affected_todos": [
                            {"id": todo.get("id"), "content": todo.get("content")}
                            for todo in unresolved
                        ],
                    }
            elif str(result.get("status", "")).lower() == "paused":
                result["interruption"] = {
                    "kind": "attention_exhausted",
                    "title": "GraphFlow needs human attention",
                    "reason": "Every remaining todo is marked for human attention; there is no runnable work left.",
                    "source": "loop_mechanism",
                    "resumable": True,
                }
            elif str(result.get("status", "")).lower() in {"failed", "technical_error", "needs_review"}:
                result["interruption"] = {
                    "kind": "workflow_failure",
                    "title": "GraphFlow stopped before completion",
                    "reason": (
                        result.get("reviewer_feedback")
                        or result.get("manager_message")
                        or f"Final workflow status: {result.get('status')}"
                    ),
                    "source": "loop_mechanism",
                    "resumable": bool(db_wf.load_checkpoint(flow_input.run_id)),
                }
            db_wf.update_workflow_run_status(
                flow_input.run_id,
                status=str(result.get("status", "completed")),
                output_json=json.dumps(result, default=str),
            )
            if str(result.get("status", "")).lower() in {
                "completed",
                "completed_with_unresolved_evidence",
            }:
                from task_hounds_api.db.ops import rounds as db_rounds
                result["round_lock"] = db_rounds.try_lock_round(
                    flow_input.project_session_id,
                    flow_input.run_id,
                    str(result.get("manager_message") or "Directive completed."),
                )
        except Exception:
            pass
    return result


def resume_loop(run_id: int) -> dict:
    """Resume a paused run from its latest checkpoint (tA2d).

    Pipeline:
      1. ``db_wf.load_checkpoint(run_id)`` -- latest row in
         flow_checkpoints for this run, ordered by step_index DESC.
         Returns None if no checkpoint exists (caller's fault).
      2. ``db_wf.get_workflow_run(run_id)`` -- workflow_runs row
         that gives us project_session_id, opencode session ids,
         and the original human_directive (via input_json).
      3. Validate the run is in a resumable state: status starts
         with ``paused`` (covers both ``paused`` and
         ``paused_before_*``).
      4. Build a fresh ``FlowInput`` from the run + project row.
      5. Flip status back to ``running`` so the token's
         ``paused()`` check no longer fires.
      6. Build the graph, seed initial state with
         ``__resume_state__`` = the deserialized checkpoint's
         state_json. ``_node_start`` will see that key, deserialize,
         and ``__route__`` to the node AFTER checkpoint.step_name.
         Already-completed nodes (manager_digest, manager_plan,
         manager_todo, ...) are SKIPPED -- their side effects do
         not run a second time.
      7. Catch ``_PauseRequestedError`` again (operator may pause
         a second time during resume) and write status=paused.

    Returns the same shape as ``run_loop``: a dict with ``status``
    in {completed, failed, paused, needs_review, stopped,
    cancelled}. Callers should treat ``paused`` as "still in
    progress, may pause again" and any other status as final.
    """
    # Validation order matters: check status FIRST so a terminal-
    # state run (completed / failed / cancelled) gets a clear
    # "not in paused state" error before we even look at the
    # checkpoint. Otherwise a "no checkpoint" error on a
    # completed run would be confusing — the operator pressed
    # resume on something that's already done, and the real
    # problem is the state, not the missing row.
    run = db_wf.get_workflow_run(run_id)
    if run is None:
        return {
            "ok": False,
            "status": "failed",
            "error": f"run {run_id} not found",
            "run_id": run_id,
        }
    status = str(run.get("status", "")).lower()
    if not (
        status == "paused"
        or status.startswith("paused_before_")
        or status in {"technical_error", "recovering"}
    ):
        return {
            "ok": False,
            "status": "failed",
            "error": f"run {run_id} not in paused state (status={run.get('status')!r})",
            "run_id": run_id,
        }
    cp = db_wf.load_checkpoint(run_id)
    if cp is None:
        return {
            "ok": False,
            "status": "failed",
            "error": f"no checkpoint for run {run_id}; cannot resume",
            "run_id": run_id,
        }

    try:
        resume_state = json.loads(cp["state_json"])
    except (json.JSONDecodeError, TypeError):
        return {
            "ok": False,
            "status": "failed",
            "error": f"checkpoint state_json is malformed for run {run_id}",
            "run_id": run_id,
        }
    if not isinstance(resume_state, dict):
        return {
            "ok": False,
            "status": "failed",
            "error": f"checkpoint state_json is not a dict for run {run_id}",
            "run_id": run_id,
        }

    flow_input = _reconstruct_flow_input_from_run(run)

    # Flip status to running BEFORE invoking the graph so the
    # token's paused() check returns False for the resumed nodes.
    try:
        db_wf.update_workflow_run_status(run_id, "running")
    except Exception:
        pass

    graph_inst = build_graph()
    initial = {
        "flow_input": _flow_input_to_jsonable(flow_input),
        "loop_input": {},
        "__resume_state__": resume_state,
    }
    try:
        result = graph_inst.invoke(initial)
    except _PauseRequestedError as pause:
        step = pause.step_name
        # Re-paused. Write the pause status back so subsequent
        # resume calls know to look at this run.
        try:
            db_wf.update_workflow_run_status(run_id, "paused")
        except Exception:
            pass
        return {
            "ok": True,
            "status": "paused",
            "paused_before": step,
            "run_id": run_id,
            "project_session_id": flow_input.project_session_id,
        }
    except Exception as exc:
        # Resume crashed. Mark failed so the operator sees it; do
        # not leave the run stuck in "running" forever.
        try:
            db_wf.update_workflow_run_status(run_id, "failed")
        except Exception:
            pass
        logger.exception("resume_loop failed for run %s", run_id)
        return {
            "ok": False,
            "status": "failed",
            "error": f"resume crashed: {exc!r}",
            "run_id": run_id,
        }

    # Mark final status (completed / failed / etc).
    try:
        db_wf.update_workflow_run_status(
            run_id,
            status=str(result.get("status", "completed")),
            output_json=json.dumps(result, default=str),
        )
    except Exception:
        pass
    return {
        "ok": True,
        **result,
        "run_id": run_id,
        "resumed_from_step": resume_state.get("step_name"),
    }


def resume_loop_from_checkpoint(cp_id: int) -> dict:
    """Resume a paused run from a specific checkpoint by its id.

    Pipeline (mirrors resume_loop but loads checkpoint by cp_id):
      1. ``db_wf.get_checkpoint(cp_id)`` -- flow_checkpoints row.
         Returns error if cp_id not found.
      2. ``db_wf.get_workflow_run(cp["run_id"])`` -- workflow_runs row.
         Returns error if run not found.
      3. Validates run is in paused/paused_before_* state.
      4. Deserialises checkpoint.state_json as resume_state.
      5. Reconstructs FlowInput from the run row.
      6. Invokes the graph with __resume_state__ to skip completed nodes.
      7. Returns the same result shape as resume_loop.

    This satisfies the compat contract for
    POST /api/runtime/checkpoints/{cp_id}/resume.
    """
    cp = db_wf.get_checkpoint(cp_id)
    if cp is None:
        return {
            "ok": False,
            "status": "failed",
            "error": f"checkpoint {cp_id} not found",
            "checkpoint_id": cp_id,
        }

    run_id = cp["run_id"]
    run = db_wf.get_workflow_run(run_id)
    if run is None:
        return {
            "ok": False,
            "status": "failed",
            "error": f"run {run_id} not found for checkpoint {cp_id}",
            "run_id": run_id,
            "checkpoint_id": cp_id,
        }

    status = str(run.get("status", "")).lower()
    if not (status == "paused" or status.startswith("paused_before_")):
        return {
            "ok": False,
            "status": "failed",
            "error": f"run {run_id} not in paused state (status={run.get('status')!r})",
            "run_id": run_id,
            "checkpoint_id": cp_id,
        }

    try:
        resume_state = json.loads(cp["state_json"])
    except (json.JSONDecodeError, TypeError):
        return {
            "ok": False,
            "status": "failed",
            "error": f"checkpoint {cp_id} state_json is malformed",
            "run_id": run_id,
            "checkpoint_id": cp_id,
        }

    if not isinstance(resume_state, dict):
        return {
            "ok": False,
            "status": "failed",
            "error": f"checkpoint {cp_id} state_json is not a dict",
            "run_id": run_id,
            "checkpoint_id": cp_id,
        }

    flow_input = _reconstruct_flow_input_from_run(run)

    try:
        db_wf.update_workflow_run_status(run_id, "running")
    except Exception:
        pass

    graph_inst = build_graph()
    initial = {
        "flow_input": _flow_input_to_jsonable(flow_input),
        "loop_input": {},
        "__resume_state__": resume_state,
    }
    try:
        result = graph_inst.invoke(initial)
    except _PauseRequestedError as pause:
        step = pause.step_name
        try:
            db_wf.update_workflow_run_status(run_id, "paused")
        except Exception:
            pass
        return {
            "ok": True,
            "status": "paused",
            "paused_before": step,
            "run_id": run_id,
            "checkpoint_id": cp_id,
            "project_session_id": flow_input.project_session_id,
        }
    except Exception as exc:
        try:
            db_wf.update_workflow_run_status(run_id, "failed")
        except Exception:
            pass
        logger.exception("resume_loop_from_checkpoint failed for cp_id %s", cp_id)
        return {
            "ok": False,
            "status": "failed",
            "error": f"resume crashed: {exc!r}",
            "run_id": run_id,
            "checkpoint_id": cp_id,
        }

    try:
        db_wf.update_workflow_run_status(
            run_id,
            status=str(result.get("status", "completed")),
            output_json=json.dumps(result, default=str),
        )
    except Exception:
        pass
    return {
        "ok": True,
        **result,
        "run_id": run_id,
        "checkpoint_id": cp_id,
        "resumed_from_step": resume_state.get("step_name"),
    }


def _flow_input_to_jsonable(fi: M.FlowInput) -> dict:
    """Serialize a FlowInput for the graph's initial state.

    The graph's TypedDict-shaped initial state must be JSON-clean
    (LangGraph passes it through checkpointers), so list/dict
    defaults are already JSON-safe but we still go through
    asdict to be explicit.
    """
    from dataclasses import asdict
    return asdict(fi)
