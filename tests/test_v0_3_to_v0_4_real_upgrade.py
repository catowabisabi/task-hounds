"""Phase 7 (Blocker 5): Real v0.3 -> v0.4 migration upgrade test.

The existing test_db_schema_unification.py only tests init_db() on a
fresh (empty) DB. A fresh DB never exercises the ALTER TABLE
statements, because schema.sql's CREATE IF NOT EXISTS already created
the tables at v0.4 shape.

This test creates a true v0.3 fixture: a DB whose reviewer_sessions
and user_directives tables are missing the `error` columns that
migrations 021 and 022 are supposed to add. It then runs init_db()
and asserts:

  1. The reviewer_sessions.error column was added by migration 021.
  2. The user_directives.error column was added by migration 022.
  3. The 3 runtime tables (agent_runtime_bindings, run_checkpoints,
     runtime_policies) were created by migration 020.
  4. The schema_version table was created and records 'v0.4'.
  5. Pre-existing v0.3 data survives the upgrade.
  6. The upgrade is idempotent: running init_db() 3 times in a row
     leaves the schema at v0.4 with no duplicate columns and no
     seeded data.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


V0_3_FIXTURE_SQL = """
CREATE TABLE IF NOT EXISTS project_sessions (
    id                  TEXT PRIMARY KEY,
    workspace_id        TEXT,
    name                TEXT,
    manager_session_id  TEXT,
    worker_session_id   TEXT,
    reviewer_session_id TEXT,
    chat_session_id     TEXT,
    is_active           INTEGER DEFAULT 1,
    name_generated      INTEGER DEFAULT 0,
    workspace_path      TEXT,
    path_missing        INTEGER DEFAULT 0,
    workspace_fingerprint TEXT,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS suggestion_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',
    human_comment   TEXT,
    verification    TEXT,
    related_files   TEXT,
    handoff_version INTEGER,
    session_id      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    released_at     TIMESTAMP,
    done_at         TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reviewer_sessions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_id      INTEGER NOT NULL,
    status             TEXT DEFAULT 'pending',
    screenshot_paths   TEXT,
    review_notes       TEXT,
    usability_issues   TEXT,
    style_feedback     TEXT,
    scripts_documented TEXT,
    started_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at       TIMESTAMP,
    timeout_at         TIMESTAMP,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_directives (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    directive   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO user_directives (session_id, directive)
VALUES ('legacy_v0_3_directive', 'ship the v0.3 release');
INSERT INTO reviewer_sessions (suggestion_id, status)
VALUES (1, 'completed');
"""


@pytest.fixture()
def v0_3_db(monkeypatch, tmp_path):
    db = tmp_path / "v0_3_to_v0_4.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    with sqlite3.connect(db) as c:
        c.executescript(V0_3_FIXTURE_SQL)
        c.commit()
    return db


def test_v0_3_fixture_is_missing_error_columns(v0_3_db):
    with sqlite3.connect(v0_3_db) as c:
        rs_cols = {
            r[1]
            for r in c.execute("PRAGMA table_info(reviewer_sessions)").fetchall()
        }
        ud_cols = {
            r[1]
            for r in c.execute("PRAGMA table_info(user_directives)").fetchall()
        }
    assert "error" not in rs_cols, (
        f"v0.3 fixture must not have reviewer_sessions.error; got {rs_cols}"
    )
    assert "error" not in ud_cols, (
        f"v0.3 fixture must not have user_directives.error; got {ud_cols}"
    )


def test_v0_3_fixture_is_missing_runtime_tables(v0_3_db):
    with sqlite3.connect(v0_3_db) as c:
        tables = {
            r[0]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    for missing in (
        "agent_runtime_bindings",
        "run_checkpoints",
        "runtime_policies",
        "schema_version",
    ):
        assert missing not in tables, (
            f"v0.3 fixture must not contain {missing!r}; found it in {tables}"
        )


def test_v0_3_fixture_preserves_pre_existing_data(v0_3_db):
    with sqlite3.connect(v0_3_db) as c:
        ud_count = c.execute(
            "SELECT COUNT(*) FROM user_directives WHERE session_id='legacy_v0_3_directive'"
        ).fetchone()[0]
        rs_count = c.execute("SELECT COUNT(*) FROM reviewer_sessions").fetchone()[0]
    assert ud_count == 1, "pre-existing v0.3 directive must survive the upgrade"
    assert rs_count == 1, "pre-existing v0.3 reviewer_sessions row must survive"


def test_init_db_adds_reviewer_sessions_error_column(v0_3_db):
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()

    with sqlite3.connect(v0_3_db) as c:
        rs_cols = {
            r[1]
            for r in c.execute("PRAGMA table_info(reviewer_sessions)").fetchall()
        }
    assert "error" in rs_cols, (
        f"Migration 021 must add reviewer_sessions.error on upgrade; got {rs_cols}"
    )


def test_init_db_adds_user_directives_error_column(v0_3_db):
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()

    with sqlite3.connect(v0_3_db) as c:
        ud_cols = {
            r[1]
            for r in c.execute("PRAGMA table_info(user_directives)").fetchall()
        }
    assert "error" in ud_cols, (
        f"Migration 022 must add user_directives.error on upgrade; got {ud_cols}"
    )


def test_init_db_creates_runtime_tables(v0_3_db):
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()

    with sqlite3.connect(v0_3_db) as c:
        tables = {
            r[0]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    for required in (
        "agent_runtime_bindings",
        "run_checkpoints",
        "runtime_policies",
        "schema_version",
    ):
        assert required in tables, (
            f"Migration 020 must add {required!r} on upgrade; still missing"
        )


def test_init_db_records_schema_version_v0_4(v0_3_db):
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()

    with sqlite3.connect(v0_3_db) as c:
        row = c.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
    assert row and row[0] == "v0.4", (
        f"schema_version must end at 'v0.4'; got {row[0] if row else None!r}"
    )


def test_upgrade_is_idempotent(v0_3_db):
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    init_db()
    init_db()

    with sqlite3.connect(v0_3_db) as c:
        ar_count = c.execute(
            "SELECT COUNT(*) FROM agent_runtime_bindings"
        ).fetchone()[0]
        rs_error_count = sum(
            1
            for r in c.execute("PRAGMA table_info(reviewer_sessions)").fetchall()
            if r[1] == "error"
        )
        ud_error_count = sum(
            1
            for r in c.execute("PRAGMA table_info(user_directives)").fetchall()
            if r[1] == "error"
        )
        ud_legacy = c.execute(
            "SELECT COUNT(*) FROM user_directives WHERE session_id='legacy_v0_3_directive'"
        ).fetchone()[0]
    assert ar_count == 0, "init_db() must not seed data on re-run"
    assert rs_error_count == 1, "reviewer_sessions.error must exist exactly once"
    assert ud_error_count == 1, "user_directives.error must exist exactly once"
    assert ud_legacy == 1, "pre-existing v0.3 data must survive re-runs"


def test_upgrade_preserves_and_uses_error_column(v0_3_db):
    """End-to-end: simulate a real v0.3 -> v0.4 upgrade and use the
    newly-added error column through the db ops layer."""
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()

    from task_hounds_api.db.ops import chat as db_chat
    db_chat.create_directive("post_upgrade", "ship the v0.4 release")
    did = db_chat.claim_pending_directive("post_upgrade")["id"]
    db_chat.mark_directive_status(did, "failed", error="upgrade marker")

    with sqlite3.connect(v0_3_db) as c:
        row = c.execute(
            "SELECT status, error FROM user_directives WHERE id=?", (did,)
        ).fetchone()
    assert row[0] == "failed"
    assert row[1] == "upgrade marker"
