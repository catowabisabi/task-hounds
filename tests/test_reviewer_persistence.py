"""Phase 7 (Blocker 1): Reviewer persistence tests.

Asserts the silent-reviewer bug is fixed by:
  1. create_reviewer_session returns a new id and inserts a
     'running' row.
  2. update_reviewer_session(status='completed') sets completed_at.
  3. update_reviewer_session(status='failed') sets completed_at AND
     records the error string.
  4. update_reviewer_session(status='needs_review') sets
     completed_at.
  5. get_latest_reviewer_session joins on suggestion_queue.session_id
     and returns the most recent row.
  6. Persisting across multiple suggestion rows preserves order
     (the latest is returned, not the first).
  7. Bugs JSON round-trips into a list on the read path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "phase7_reviewer_test.db"
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


def test_create_reviewer_session_returns_id_and_running_status(fresh_db):
    from task_hounds_api.db.ops import workflow as db_wf
    sug_id = _seed_session_and_suggestion(fresh_db, "ps1", "build login")
    rid = db_wf.create_reviewer_session(sug_id, status="running")
    assert isinstance(rid, int) and rid > 0

    import sqlite3
    with sqlite3.connect(fresh_db) as c:
        row = c.execute(
            "SELECT status, suggestion_id, started_at, completed_at "
            "FROM reviewer_sessions WHERE id=?", (rid,)
        ).fetchone()
    assert row[0] == "running"
    assert row[1] == sug_id
    assert row[2] is not None
    assert row[3] is None


def test_update_reviewer_session_completed_sets_completed_at(fresh_db):
    from task_hounds_api.db.ops import workflow as db_wf
    sug_id = _seed_session_and_suggestion(fresh_db, "ps2", "fix bug")
    rid = db_wf.create_reviewer_session(sug_id)
    db_wf.update_reviewer_session(
        rid,
        status="completed",
        review_notes="looks good",
        bugs_json="[]",
        style_feedback="clean",
        scripts_documented="files=[]",
        completed=True,
    )
    import sqlite3
    with sqlite3.connect(fresh_db) as c:
        row = c.execute(
            "SELECT status, review_notes, completed_at, error "
            "FROM reviewer_sessions WHERE id=?", (rid,)
        ).fetchone()
    assert row[0] == "completed"
    assert row[1] == "looks good"
    assert row[2] is not None
    assert row[3] == ""


def test_update_reviewer_session_failed_records_error(fresh_db):
    from task_hounds_api.db.ops import workflow as db_wf
    sug_id = _seed_session_and_suggestion(fresh_db, "ps3", "ship feature")
    rid = db_wf.create_reviewer_session(sug_id)
    db_wf.update_reviewer_session(
        rid,
        status="failed",
        review_notes="worker regressed build",
        error="AssertionError: 1+1==3",
        completed=True,
    )
    import sqlite3
    with sqlite3.connect(fresh_db) as c:
        row = c.execute(
            "SELECT status, review_notes, error, completed_at "
            "FROM reviewer_sessions WHERE id=?", (rid,)
        ).fetchone()
    assert row[0] == "failed"
    assert row[1] == "worker regressed build"
    assert "AssertionError" in row[2]
    assert row[3] is not None


def test_update_reviewer_session_needs_review_sets_completed_at(fresh_db):
    from task_hounds_api.db.ops import workflow as db_wf
    sug_id = _seed_session_and_suggestion(fresh_db, "ps4", "edge case")
    rid = db_wf.create_reviewer_session(sug_id)
    db_wf.update_reviewer_session(
        rid,
        status="needs_review",
        review_notes="ambiguous verdict from LLM",
        completed=True,
    )
    import sqlite3
    with sqlite3.connect(fresh_db) as c:
        row = c.execute(
            "SELECT status, review_notes, completed_at "
            "FROM reviewer_sessions WHERE id=?", (rid,)
        ).fetchone()
    assert row[0] == "needs_review"
    assert "ambiguous" in row[1]
    assert row[2] is not None


def test_get_latest_reviewer_session_returns_most_recent(fresh_db):
    from task_hounds_api.db.ops import workflow as db_wf
    sug1 = _seed_session_and_suggestion(fresh_db, "ps5", "first task")
    sug2 = _seed_session_and_suggestion(fresh_db, "ps5", "second task")
    rid_old = db_wf.create_reviewer_session(sug1)
    rid_new = db_wf.create_reviewer_session(sug2)
    db_wf.update_reviewer_session(rid_old, status="completed", review_notes="old")
    db_wf.update_reviewer_session(rid_new, status="failed", review_notes="new")

    latest = db_wf.get_latest_reviewer_session("ps5")
    assert latest is not None
    assert latest["id"] == rid_new
    assert latest["status"] == "failed"
    assert latest["review_notes"] == "new"
    assert latest["completed_at"] is not None


def test_get_latest_reviewer_session_returns_none_when_empty(fresh_db):
    from task_hounds_api.db.ops import workflow as db_wf
    assert db_wf.get_latest_reviewer_session("never_seeded") is None


def test_bugs_json_round_trips_into_list(fresh_db):
    from task_hounds_api.db.ops import workflow as db_wf
    sug_id = _seed_session_and_suggestion(fresh_db, "ps6", "audit ui")
    rid = db_wf.create_reviewer_session(sug_id)
    bugs = ["dead link in header", "contrast ratio too low"]
    db_wf.update_reviewer_session(
        rid,
        status="needs_review",
        review_notes="two issues",
        bugs_json=json.dumps(bugs),
        style_feedback="spacing off",
        completed=True,
    )
    latest = db_wf.get_latest_reviewer_session("ps6")
    assert latest is not None
    assert latest["usability_issues"] == bugs
