"""Tests for the graph max-retry counter.

Regression for the infinite-loop bug: 4 manager nodes can route back
to `manager_digest` when their result is empty. Without a counter,
the graph loops forever. With this fix:

  - After 3 round-trips through manager_digest, the graph routes to
    END and sets `state.status = "failed"`

The test runs the graph in a thread with a watchdog so RED state
(graph hangs forever) produces a clear assertion failure instead of
blocking the test runner.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


def test_digest_loop_gives_up_after_3():
    from task_hounds_api.workflow import graph as graph_mod
    from task_hounds_api.workflow import models as M

    fi = M.FlowInput(
        power_team_project_id="pt_x",
        project_session_id="ps_x",
        human_directive="do thing",
    )

    def empty_plan(state):
        state.plan = ""
        return state

    def empty_todo(state):
        state.todo_list = []
        return state

    def empty_select(state):
        state.suggestion_content = ""
        return state

    result_holder = [None, None]

    def run_in_thread():
        with patch.object(graph_mod.ex, "manager_plan", side_effect=empty_plan), \
             patch.object(graph_mod.ex, "manager_todo", side_effect=empty_todo), \
             patch.object(graph_mod.ex, "manager_select_task", side_effect=empty_select), \
             patch.object(
                 graph_mod.ex,
                 "state_from_db",
                 side_effect=lambda fi, li: M.FlowState(flow_input=fi, loop_input=li),
             ):
            try:
                result_holder[0] = graph_mod.run_loop(fi)
            except BaseException as e:
                result_holder[1] = e

    t = threading.Thread(target=run_in_thread, daemon=True)
    t.start()
    t.join(timeout=5)

    assert not t.is_alive(), (
        "graph did not return within 5s — manager_digest loop-back is "
        "infinite (no retry counter / no give-up path). "
        f"exc={result_holder[1]!r}"
    )
    result = result_holder[0]
    assert result is not None, f"no result; exc={result_holder[1]!r}"

    final_status = result.get("status")
    retry = result.get("__digest_retry__", 0)
    assert final_status == "failed", f"expected status=failed, got {final_status}"
    assert retry >= 3, f"expected __digest_retry__ >= 3, got {retry}"
