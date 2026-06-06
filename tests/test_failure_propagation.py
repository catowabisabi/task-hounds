"""Phase 7 (Blocker 2): Failure propagation tests.

Asserts:
  1. _record_directive_lifecycle maps result['status'] to the right
     directive status. 'completed' -> 'processed', anything else ->
     'failed' with an error string.
  2. worker_execute does NOT call update_suggestion_status('done')
     when oc_client.run returns ok=False. It sets state.status='failed',
     state.worker_test_result='failed', persists a worker_report,
     and marks the suggestion as 'failed'.
  3. _node_reviewer_check does not overwrite state.status. A Reviewer
     verdict of 'needs_review' / 'failed' must reach the final
     state dict intact.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "phase7_failure_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    return db


def _seed_session_and_suggestion(db, session_id: str, content: str) -> int:
    import sqlite3
    with sqlite3.connect(db) as c:
        existing = c.execute(
            "SELECT 1 FROM project_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not existing:
            c.execute(
                "INSERT INTO project_sessions (id, name, is_active) "
                "VALUES (?, ?, 1)",
                (session_id, session_id + "_name"),
            )
        c.execute(
            "INSERT INTO suggestion_queue (content, status, session_id) "
            "VALUES (?, 'pending', ?)",
            (content, session_id),
        )
        sug_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.commit()
    return int(sug_id)


# ── _record_directive_lifecycle ────────────────────────────────────────────


def test_record_directive_completed_marks_processed(fresh_db):
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.db.ops import chat as db_chat
    db_chat.create_directive("ps_a", "do thing")
    did = db_chat.claim_pending_directive("ps_a")["id"]
    loop_mod._record_directive_lifecycle(did, {"status": "completed"})
    rows = db_chat.list_directives("ps_a", limit=1)
    assert rows and rows[0]["status"] == "processed"


def test_record_directive_failed_marks_failed_with_error(fresh_db):
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.db.ops import chat as db_chat
    db_chat.create_directive("ps_b", "do thing")
    did = db_chat.claim_pending_directive("ps_b")["id"]
    loop_mod._record_directive_lifecycle(did, {"status": "failed"})
    rows = db_chat.list_directives("ps_b", limit=1)
    assert rows and rows[0]["status"] == "failed"
    assert rows[0].get("error") and "failed" in rows[0]["error"]


def test_record_directive_needs_review_marks_failed(fresh_db):
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.db.ops import chat as db_chat
    db_chat.create_directive("ps_c", "do thing")
    did = db_chat.claim_pending_directive("ps_c")["id"]
    loop_mod._record_directive_lifecycle(did, {"status": "needs_review"})
    rows = db_chat.list_directives("ps_c", limit=1)
    assert rows and rows[0]["status"] == "failed"
    assert rows[0].get("error") and "reviewer" in rows[0]["error"]


def test_record_directive_skipped_marks_failed(fresh_db):
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.db.ops import chat as db_chat
    db_chat.create_directive("ps_d", "do thing")
    did = db_chat.claim_pending_directive("ps_d")["id"]
    loop_mod._record_directive_lifecycle(did, {"status": "skipped"})
    rows = db_chat.list_directives("ps_d", limit=1)
    assert rows and rows[0]["status"] == "failed"
    assert rows[0].get("error") and "skipped" in rows[0]["error"]


def test_record_directive_non_dict_marks_failed(fresh_db):
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.db.ops import chat as db_chat
    db_chat.create_directive("ps_e", "do thing")
    did = db_chat.claim_pending_directive("ps_e")["id"]
    loop_mod._record_directive_lifecycle(did, None)
    rows = db_chat.list_directives("ps_e", limit=1)
    assert rows and rows[0]["status"] == "failed"


# ── worker_execute failure path ─────────────────────────────────────────────


def test_worker_execute_does_not_mark_done_on_opencode_failure(monkeypatch, fresh_db):
    from task_hounds_api.workflow import executor as ex_mod
    from task_hounds_api.workflow import models as M

    sug_id = _seed_session_and_suggestion(fresh_db, "ps_w", "build x")
    fi = M.FlowInput(
        power_team_project_id="pt_ps_w",
        project_session_id="ps_w",
        human_directive="build x",
        workspace_path=str(_HERE.parent),
    )
    state = M.FlowState(flow_input=fi, loop_input=M.FlowLoopInput())

    monkeypatch.setattr(
        "task_hounds_api.opencode.config.list_providers",
        lambda: {"minimax": {"options": {"apiKey": "fake-test-key"}}},
    )

    def fake_run(*a, **kw):
        return {"ok": False, "error": {"message": "opencode refused"}}
    monkeypatch.setattr(ex_mod.oc_client, "run", fake_run)

    out = ex_mod.worker_execute(state)

    import sqlite3
    with sqlite3.connect(fresh_db) as c:
        sug_status = c.execute(
            "SELECT status FROM suggestion_queue WHERE id=?", (sug_id,)
        ).fetchone()[0]
        report_count = c.execute(
            "SELECT COUNT(*) FROM worker_reports WHERE session_id=?", ("ps_w",)
        ).fetchone()[0]
    assert sug_status == "failed", (
        f"Worker OpenCode failure must mark suggestion as 'failed', got {sug_status!r}"
    )
    assert report_count >= 1, "Worker failure must still persist a worker_reports row"
    assert out.status == "failed"
    assert out.worker_test_result == "failed"
    assert "opencode refused" in out.worker_report


def test_worker_execute_success_does_NOT_mark_suggestion_done(
    monkeypatch, fresh_db
):
    """Phase-8 P0-1 contract change: the Worker no longer marks
    the suggestion 'done' on success. The Reviewer pass is the
    only path to 'done'. The Worker only records the
    suggestion_id in state so the Reviewer can find the same
    row even if the Manager has released new work in the
    meantime."""
    from task_hounds_api.workflow import executor as ex_mod
    from task_hounds_api.workflow import models as M

    sug_id = _seed_session_and_suggestion(fresh_db, "ps_w2", "build y")
    fi = M.FlowInput(
        power_team_project_id="pt_ps_w2",
        project_session_id="ps_w2",
        human_directive="build y",
        workspace_path=str(_HERE.parent),
    )
    state = M.FlowState(flow_input=fi, loop_input=M.FlowLoopInput())

    monkeypatch.setenv("OPENCODE_API_KEY_MINIMAX", "fake-minimax-key")
    monkeypatch.setenv("OPENCODE_API_KEY_BAILIAN", "fake-bailian-key")
    from task_hounds_api.opencode import config as _cfg
    _cfg.reset_cache()

    def fake_run(*a, **kw):
        return {"ok": True, "output": {"text": "all done"}}
    monkeypatch.setattr(ex_mod.oc_client, "run", fake_run)

    state = ex_mod.worker_execute(state)
    assert state.suggestion_id == sug_id, (
        f"Worker must record suggestion_id; got {state.suggestion_id!r}"
    )

    import sqlite3
    with sqlite3.connect(fresh_db) as c:
        sug_status = c.execute(
            "SELECT status FROM suggestion_queue WHERE id=?", (sug_id,)
        ).fetchone()[0]
    assert sug_status != "done", (
        f"Worker must NOT mark 'done'; Reviewer pass is the only "
        f"path to 'done'. Got {sug_status!r}"
    )


# ── graph._node_reviewer_check no longer overwrites state.status ───────────


def test_node_reviewer_check_does_not_overwrite_status(monkeypatch):
    from task_hounds_api.workflow import graph as g
    from task_hounds_api.workflow import models as M
    from task_hounds_api.workflow import executor as ex_mod

    fi = M.FlowInput(
        power_team_project_id="pt_x",
        project_session_id="x",
        human_directive="d",
    )
    state = M.FlowState(flow_input=fi, loop_input=M.FlowLoopInput())
    state.status = "needs_review"

    def fake_reviewer(s):
        # Preserve whatever status we hand in (e.g. needs_review)
        return s
    monkeypatch.setattr(ex_mod, "reviewer_check", fake_reviewer)
    monkeypatch.setattr(ex_mod, "set_agent_state_safe", lambda *a, **kw: None)

    out = g._node_reviewer_check(g._state_to_dict(state))
    assert out["status"] == "needs_review", (
        "graph._node_reviewer_check must NOT overwrite state.status. "
        f"Got {out['status']!r}"
    )


def test_node_reviewer_check_preserves_failed_status(monkeypatch):
    from task_hounds_api.workflow import graph as g
    from task_hounds_api.workflow import models as M
    from task_hounds_api.workflow import executor as ex_mod

    fi = M.FlowInput(
        power_team_project_id="pt_y",
        project_session_id="y",
        human_directive="d",
    )
    state = M.FlowState(flow_input=fi, loop_input=M.FlowLoopInput())
    state.status = "failed"

    monkeypatch.setattr(ex_mod, "reviewer_check", lambda s: s)
    monkeypatch.setattr(ex_mod, "set_agent_state_safe", lambda *a, **kw: None)

    out = g._node_reviewer_check(g._state_to_dict(state))
    assert out["status"] == "failed"
