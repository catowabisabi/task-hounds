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
"""
from __future__ import annotations

from typing import Any

try:
    from langgraph.graph import END, StateGraph
except ImportError:
    END = None
    StateGraph = None

from task_hounds_api.workflow import executor as ex
from task_hounds_api.workflow import models as M


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


def _node_start(raw: GraphState) -> GraphState:
    """Initial: read DB to build a fresh FlowState."""
    fi = M.FlowInput(**raw["flow_input"]) if isinstance(raw.get("flow_input"), dict) else raw["flow_input"]
    li = M.FlowLoopInput(**raw.get("loop_input", {}))
    state = ex.state_from_db(fi, li)
    return _state_to_dict(state)


def _node_manager_digest(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    ex.set_agent_state_safe("manager", "busy", "digest")
    state = ex.manager_digest(state)
    return _state_to_dict(state)


def _node_manager_plan(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    state = ex.manager_plan(state)
    if not state.plan.strip():
        _bump_retry(state)
        return {"__route__": "manager_digest", **_state_to_dict(state)}
    return _state_to_dict(state)


def _node_manager_todo(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    if not state.plan.strip():
        _bump_retry(state)
        return {"__route__": "manager_digest", **_state_to_dict(state)}
    state = ex.manager_todo(state)
    if not state.todo_list:
        _bump_retry(state)
        return {"__route__": "manager_digest", **_state_to_dict(state)}
    return _state_to_dict(state)


def _node_manager_select(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    if not state.todo_list:
        _bump_retry(state)
        return {"__route__": "manager_digest", **_state_to_dict(state)}
    state = ex.manager_select_task(state)
    if not state.suggestion_content.strip():
        _bump_retry(state)
        return {"__route__": "manager_digest", **_state_to_dict(state)}
    return _state_to_dict(state)


def _node_manager_release(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    if not state.suggestion_content.strip():
        _bump_retry(state)
        return {"__route__": "manager_digest", **_state_to_dict(state)}
    state = ex.manager_release(state)
    ex.set_agent_state_safe("manager", "idle")
    return _state_to_dict(state)


def _node_worker_execute(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    ex.set_agent_state_safe("worker", "busy", state.suggestion_content[:80])
    state = ex.worker_execute(state)
    ex.set_agent_state_safe("worker", "idle")
    return _state_to_dict(state)


def _node_reviewer_check(raw: GraphState) -> GraphState:
    state = _state_from_dict(raw)
    ex.set_agent_state_safe("reviewer", "busy", "checking")
    state = ex.reviewer_check(state)
    ex.set_agent_state_safe("reviewer", "idle")
    # state.status is owned by the Reviewer executor (qa_result-driven).
    # Do not overwrite it here or Reviewer rejects will be misreported.
    return _state_to_dict(state)


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
    status = str(raw.get("status", "") or "").strip().lower()
    if status in {"failed", "skipped"}:
        return "__worker_failed__"
    return "reviewer_check"


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
    g.set_entry_point("start")
    g.add_edge("start", "manager_digest")
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
        "continue": "manager_release",
    })
    g.add_edge("manager_release", "worker_execute")
    g.add_conditional_edges("worker_execute", _route_after_worker, {
        "reviewer_check": "reviewer_check",
        "__worker_failed__": END,
    })
    g.add_edge("reviewer_check", END)
    return g.compile()


# ── Public entry point ──────────────────────────────────────────────────────

def run_loop(flow_input: M.FlowInput, loop_input: M.FlowLoopInput | None = None) -> dict:
    """Run one full Manager/Worker/Reviewer loop. Returns the final state dict."""
    M.validate_flow_input(flow_input)
    graph = build_graph()
    li = loop_input or M.FlowLoopInput()
    initial = _state_to_dict(M.FlowState(flow_input=flow_input, loop_input=li))
    return graph.invoke(initial)
