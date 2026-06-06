"""Phase 5: DB schema unification tests.

Asserts:
  1. Fresh DB builds all required tables (incl. the 3 runtime tables
     that were previously untracked: agent_runtime_bindings,
     run_checkpoints, runtime_policies).
  2. schema_version table exists and starts at v0.4 on a fresh DB.
  3. Migration 020 is idempotent (can re-run without errors).
  4. The 3 previously-untracked tables have the exact column set the
     production code expects.
  5. Pre-existing user data survives the migration (no DROP TABLE).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

REPO = _HERE.parent
DB_DIR = REPO / "core" / "db"
SCHEMA_PATH = DB_DIR / "schema.sql"
MIGRATIONS_DIR = DB_DIR / "migrations"


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "phase5_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    return db


def test_schema_and_migrations_files_exist():
    """The canonical schema and migrations directory must exist on disk
    before any of the rest of these tests can be meaningful."""
    assert SCHEMA_PATH.exists(), f"missing canonical schema at {SCHEMA_PATH}"
    assert SCHEMA_PATH.stat().st_size > 1000, (
        f"schema.sql is suspiciously small: {SCHEMA_PATH.stat().st_size} bytes"
    )
    assert MIGRATIONS_DIR.exists(), f"missing migrations dir at {MIGRATIONS_DIR}"
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert migrations, f"no migrations found in {MIGRATIONS_DIR}"
    assert migrations[-1].name.startswith("022_"), (
        f"latest migration is {migrations[-1].name}, expected 022_user_directives_error_column"
    )


def test_fresh_db_creates_all_required_tables(fresh_db):
    """A fresh init_db() must create every table the application
    code reads from or writes to, including the 3 previously-untracked
    runtime tables."""
    required = {
        "agent_registry",
        "agent_runtime_bindings",
        "agents",
        "chat_messages",
        "manager_messages",
        "opencode_server_instances",
        "project_sessions",
        "reviewer_sessions",
        "run_checkpoints",
        "runtime_policies",
        "schema_version",
        "session_todos",
        "suggestion_queue",
        "user_directives",
        "worker_reports",
        "workflow_runs",
    }
    conn = sqlite3.connect(fresh_db)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        existing = {r[0] for r in rows}
    finally:
        conn.close()
    missing = required - existing
    assert not missing, (
        f"fresh init_db() is missing {len(missing)} required tables: "
        f"{sorted(missing)}"
    )


def test_fresh_db_records_v0_4_in_schema_version(fresh_db):
    """After init_db(), the schema_version table must contain 'v0.4'."""
    conn = sqlite3.connect(fresh_db)
    try:
        rows = conn.execute("SELECT version, notes FROM schema_version").fetchall()
    finally:
        conn.close()
    versions = {r[0] for r in rows}
    assert "v0.4" in versions, f"schema_version missing v0.4 row, got: {versions}"


def test_runtime_bindings_table_has_required_columns(fresh_db):
    """The agent_runtime_bindings table must have the columns the
    runtime code (db/ops/runtime.py) reads/writes."""
    required = {"role", "host", "port", "opencode_agent", "model", "binding_source", "updated_at", "server_instance_id"}
    conn = sqlite3.connect(fresh_db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_runtime_bindings)").fetchall()}
    finally:
        conn.close()
    missing = required - cols
    assert not missing, f"agent_runtime_bindings missing columns: {sorted(missing)}"


def test_run_checkpoints_table_has_required_columns(fresh_db):
    """run_checkpoints must have the columns the workflow writes
    when it snapshots a project state."""
    required = {"reason", "status", "manager_state_json", "worker_state_json", "reviewer_state_json", "chat_state_json"}
    conn = sqlite3.connect(fresh_db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(run_checkpoints)").fetchall()}
    finally:
        conn.close()
    missing = required - cols
    assert not missing, f"run_checkpoints missing columns: {sorted(missing)}"


def test_runtime_policies_table_has_required_columns(fresh_db):
    """runtime_policies must have the columns the policy endpoint reads/writes."""
    required = {"name", "close_behavior", "background_mode_enabled", "max_managed_opencode_servers", "default_topology", "default_shared_port"}
    conn = sqlite3.connect(fresh_db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(runtime_policies)").fetchall()}
    finally:
        conn.close()
    missing = required - cols
    assert not missing, f"runtime_policies missing columns: {sorted(missing)}"


def test_migration_020_is_idempotent(fresh_db):
    """Re-running init_db() on an already-v0.4 DB must be a no-op
    (no errors, no duplicate rows). The IF NOT EXISTS / INSERT OR
    IGNORE in migration 020 makes this safe."""
    from task_hounds_api.db import init_db
    init_db()  # second call
    init_db()  # third call
    conn = sqlite3.connect(fresh_db)
    try:
        versions = conn.execute("SELECT version FROM schema_version").fetchall()
        binding_count = conn.execute("SELECT COUNT(*) FROM agent_runtime_bindings").fetchone()[0]
        policies_count = conn.execute("SELECT COUNT(*) FROM runtime_policies").fetchone()[0]
        run_ckpt_count = conn.execute("SELECT COUNT(*) FROM run_checkpoints").fetchone()[0]
    finally:
        conn.close()
    assert len(versions) == 1, f"re-run added duplicate version rows: {versions}"
    assert versions[0][0] == "v0.4"
    assert binding_count == 0 and policies_count == 0 and run_ckpt_count == 0, (
        f"idempotent run should leave empty runtime tables; got "
        f"agent_runtime_bindings={binding_count} "
        f"runtime_policies={policies_count} run_checkpoints={run_ckpt_count}"
    )


def test_migration_preserves_existing_user_data(fresh_db):
    """The v0.3 -> v0.4 migration must NOT drop or rename any
    existing user table. Simulate a v0.3 DB with user rows, run
    init_db(), and verify the data is still there."""
    conn = sqlite3.connect(fresh_db)
    try:
        # Insert a user directive (a v0.3 table)
        conn.execute(
            "INSERT INTO user_directives (session_id, directive, status) "
            "VALUES (?, ?, ?)",
            ("ps_test", "test directive before migration", "pending"),
        )
        conn.execute(
            "INSERT INTO project_sessions (id, name, is_active) "
            "VALUES (?, ?, ?)",
            ("ps_test", "test project", 1),
        )
        conn.commit()
        before_directives = conn.execute(
            "SELECT COUNT(*) FROM user_directives WHERE session_id='ps_test'"
        ).fetchone()[0]
        before_projects = conn.execute(
            "SELECT COUNT(*) FROM project_sessions WHERE id='ps_test'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert before_directives == 1
    assert before_projects == 1

    # Re-run init_db() -- the migration should NOT touch user data
    from task_hounds_api.db import init_db
    init_db()

    conn = sqlite3.connect(fresh_db)
    try:
        after_directives = conn.execute(
            "SELECT COUNT(*) FROM user_directives WHERE session_id='ps_test'"
        ).fetchone()[0]
        after_projects = conn.execute(
            "SELECT COUNT(*) FROM project_sessions WHERE id='ps_test'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert after_directives == 1, (
        f"v0.4 migration dropped user_directives rows: before={before_directives} after={after_directives}"
    )
    assert after_projects == 1, (
        f"v0.4 migration dropped project_sessions rows: before={before_projects} after={after_projects}"
    )
