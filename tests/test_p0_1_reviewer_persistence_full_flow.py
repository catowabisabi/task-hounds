"""Phase 8 (P0-1): Reviewer persistence in the REAL full flow.

The audit reproduced a bug where worker_execute marks the suggestion
'done' before the Reviewer runs, and get_active_suggestion excludes
'done', so the Reviewer sees no active suggestion, no reviewer_sessions
row is ever created, and the directive is marked 'processed' even
though the Reviewer verdict was never recorded.

This test simulates the real Worker -> Reviewer sequence with the
real executor functions (only oc_client.run is mocked). It asserts:
  1. worker_execute success: state.suggestion_id is set, the
     suggestion is NOT yet 'done' (the Reviewer is the one that
     marks it done, only on pass).
  2. reviewer_check pass: reviewer_sessions has a 'completed' row
     AND the suggestion is now 'done'.
  3. reviewer_check fail: reviewer_sessions has a 'failed' row AND
     the suggestion is now 'failed'.
  4. reviewer_check needs_review: reviewer_sessions has a
     'needs_review' row AND the suggestion is now 'needs_review'.
  5. Worker success alone does NOT mark the suggestion 'done'.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "phase8_p0_1.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    return db


def _seed(fresh_db, session_id: str) -> int:
    with sqlite3.connect(fresh_db) as c:
        c.execute(
            "INSERT INTO project_sessions (id, name, is_active) "
            "VALUES (?, ?, 1)",
            (session_id, session_id + "_name"),
        )
        c.execute(
            "INSERT INTO suggestion_queue "
            "(content, status, verification, session_id) "
            "VALUES (?, 'released', ?, ?)",
            ("build login screen", "page renders without error", session_id),
        )
        sug_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.commit()
    return int(sug_id)


def _fake_credentials(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY_MINIMAX", "fake-minimax-key")
    monkeypatch.setenv("OPENCODE_API_KEY_BAILIAN", "fake-bailian-key")
    from task_hounds_api.opencode import config as _cfg
    _cfg.reset_cache()


def _make_state(session_id: str):
    from task_hounds_api.workflow import models as M
    fi = M.FlowInput(
        power_team_project_id="pt_" + session_id,
        project_session_id=session_id,
        human_directive="build the login screen",
    )
    return M.FlowState(flow_input=fi, loop_input=M.FlowLoopInput())


def _get_suggestion_status(fresh_db, sug_id: int) -> str:
    with sqlite3.connect(fresh_db) as c:
        return c.execute(
            "SELECT status FROM suggestion_queue WHERE id=?", (sug_id,)
        ).fetchone()[0]


def _get_latest_reviewer_session(fresh_db, sug_id: int):
    with sqlite3.connect(fresh_db) as c:
        return c.execute(
            "SELECT id, status, error FROM reviewer_sessions "
            "WHERE suggestion_id=? ORDER BY id DESC LIMIT 1",
            (sug_id,),
        ).fetchone()


def _patch_oc_run(monkeypatch, qa_result: str | None, error: str = ""):
    """Patch oc_client.run to return canned output. The first call
    is the Worker (return success text). The second call is the
    Reviewer (return JSON with qa_result). If error is set, return
    ok=False instead."""
    from task_hounds_api.workflow import executor as ex_mod
    call_count = {"n": 0}

    def fake_run(*a, **kw):
        call_count["n"] += 1
        if error:
            return {"ok": False, "error": {"message": error}}
        if call_count["n"] == 1:
            return {
                "ok": True,
                "output": {"text": "worker finished the task successfully"},
            }
        if qa_result is None:
            return {"ok": True, "output": {"text": "{}"}}
        return {
            "ok": True,
            "output": {
                "text": json.dumps({
                    "reviewer_feedback": f"qa={qa_result}",
                    "qa_result": qa_result,
                    "bugs": [],
                    "uiux_suggestions": [],
                })
            },
        }
    monkeypatch.setattr(ex_mod.oc_client, "run", fake_run)


# ── P0-1 full flow: worker pass + reviewer pass -> done + reviewer row ──


def test_full_flow_worker_ok_reviewer_pass_marks_done_and_persists(
    monkeypatch, fresh_db
):
    from task_hounds_api.workflow import executor as ex_mod
    sid = "ps_p01_pass"
    sug_id = _seed(fresh_db, session_id=sid)
    _fake_credentials(monkeypatch)
    _patch_oc_run(monkeypatch, qa_result="pass")

    state = _make_state(sid)
    state = ex_mod.worker_execute(state)
    assert state.suggestion_id == sug_id, (
        f"Worker must record the suggestion_id it worked on, got {state.suggestion_id!r}"
    )
    pre_status = _get_suggestion_status(fresh_db, sug_id)
    assert pre_status != "done", (
        f"Worker must NOT mark suggestion 'done' before the Reviewer decides; "
        f"got {pre_status!r}"
    )

    state = ex_mod.reviewer_check(state)
    final = _get_suggestion_status(fresh_db, sug_id)
    rs = _get_latest_reviewer_session(fresh_db, sug_id)
    assert rs is not None, "Reviewer pass must persist a reviewer_sessions row"
    assert rs[1] == "completed", f"reviewer_sessions.status should be 'completed', got {rs[1]!r}"
    assert final == "done", f"Reviewer pass must mark suggestion 'done', got {final!r}"


def test_full_flow_worker_ok_reviewer_fail_marks_failed_and_persists(
    monkeypatch, fresh_db
):
    from task_hounds_api.workflow import executor as ex_mod
    sid = "ps_p01_fail"
    sug_id = _seed(fresh_db, session_id=sid)
    _fake_credentials(monkeypatch)
    _patch_oc_run(monkeypatch, qa_result="fail")

    state = _make_state(sid)
    state = ex_mod.worker_execute(state)
    assert state.suggestion_id == sug_id
    state = ex_mod.reviewer_check(state)

    final = _get_suggestion_status(fresh_db, sug_id)
    rs = _get_latest_reviewer_session(fresh_db, sug_id)
    assert rs is not None, "Reviewer fail must still persist a reviewer_sessions row"
    assert rs[1] == "failed", f"reviewer_sessions.status should be 'failed', got {rs[1]!r}"
    assert final == "failed", f"Reviewer fail must mark suggestion 'failed', got {final!r}"


def test_full_flow_worker_ok_reviewer_needs_review_marks_needs_review_and_persists(
    monkeypatch, fresh_db
):
    from task_hounds_api.workflow import executor as ex_mod
    sid = "ps_p01_nr"
    sug_id = _seed(fresh_db, session_id=sid)
    _fake_credentials(monkeypatch)
    _patch_oc_run(monkeypatch, qa_result="needs_review")

    state = _make_state(sid)
    state = ex_mod.worker_execute(state)
    state = ex_mod.reviewer_check(state)

    final = _get_suggestion_status(fresh_db, sug_id)
    rs = _get_latest_reviewer_session(fresh_db, sug_id)
    assert rs is not None, "Reviewer needs_review must persist a reviewer_sessions row"
    assert rs[1] == "needs_review", (
        f"reviewer_sessions.status should be 'needs_review', got {rs[1]!r}"
    )
    assert final == "needs_review", (
        f"Reviewer needs_review must mark suggestion 'needs_review', got {final!r}"
    )


def test_worker_execute_success_does_NOT_mark_suggestion_done(
    monkeypatch, fresh_db
):
    """Worker success is a precondition for the Reviewer; the
    suggestion must remain in 'released' (or another non-terminal
    status) so the Reviewer can find it via state.suggestion_id.
    The 'done' status is the Reviewer pass's responsibility now."""
    from task_hounds_api.workflow import executor as ex_mod
    sid = "ps_p01_nodone"
    sug_id = _seed(fresh_db, session_id=sid)
    _fake_credentials(monkeypatch)
    _patch_oc_run(monkeypatch, qa_result=None)

    state = _make_state(sid)
    ex_mod.worker_execute(state)
    final = _get_suggestion_status(fresh_db, sug_id)
    assert final != "done", (
        f"Worker must NOT mark suggestion 'done' in isolation; the "
        f"Reviewer pass is the only path to 'done'. Got {final!r}"
    )
