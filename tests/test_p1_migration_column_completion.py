"""Phase 8 (P1 migration): PRAGMA-based column补 for partial tables.

The audit reproduced a bug: CREATE TABLE IF NOT EXISTS in
schema.sql does NOT补 missing columns for tables that already
exist. If a v0.3 DB had a partial runtime_bindings table (e.g.
only id and role, missing server_instance_id, host, port, etc.),
init_db() would NOT add the missing columns because CREATE TABLE
IF NOT EXISTS is a no-op for existing tables.

Fix: after applying schema.sql, iterate over the 3 runtime
tables and check PRAGMA table_info for missing columns. Add
them via ALTER TABLE ADD COLUMN. Preserve existing rows.
Idempotent: running init_db() twice is safe (ALTER TABLE ADD
COLUMN on an existing column raises 'duplicate column' which
the runner already swallows).

Tests (4):
  - test_partial_agent_runtime_bindings_gets_columns_added: DB
    has agent_runtime_bindings(id, role) only. init_db() adds
    the missing columns (server_instance_id, host, port, etc.)
    and preserves the existing row.
  - test_partial_run_checkpoints_gets_columns_added: same for
    run_checkpoints.
  - test_partial_runtime_policies_gets_columns_added: same for
    runtime_policies.
  - test_init_db_idempotent_on_partial_tables: running init_db()
    twice on a partial DB leaves the schema correct (no
    duplicate column errors, all required columns present,
    existing rows preserved).
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


# Partial-table fixtures: each creates a table with ONLY the
# listed columns, inserts one row, then init_db() must补 the
# rest while preserving the row.

PARTIAL_AGENT_RUNTIME_BINDINGS = """
CREATE TABLE agent_runtime_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL
);
INSERT INTO agent_runtime_bindings (id, role) VALUES (1, 'manager');
"""

PARTIAL_RUN_CHECKPOINTS = """
CREATE TABLE run_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT
);
INSERT INTO run_checkpoints (id) VALUES (1);
"""

PARTIAL_RUNTIME_POLICIES = """
CREATE TABLE runtime_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT
);
INSERT INTO runtime_policies (id) VALUES (1);
"""


def _seed_partial_db(db: Path, sql: str):
    with sqlite3.connect(db) as c:
        c.executescript(sql)
        c.commit()


def _get_columns(db: Path, table: str) -> set[str]:
    with sqlite3.connect(db) as c:
        return {
            r[1]
            for r in c.execute(f"PRAGMA table_info({table})").fetchall()
        }


def _get_row_count(db: Path, table: str) -> int:
    with sqlite3.connect(db) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_partial_agent_runtime_bindings_gets_columns_added(
    monkeypatch, tmp_path
):
    """DB has agent_runtime_bindings(id, role) only. init_db()
    must add the missing columns and preserve the existing row."""
    db = tmp_path / "phase8_partial_arb.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()

    _seed_partial_db(db, PARTIAL_AGENT_RUNTIME_BINDINGS)
    pre_cols = _get_columns(db, "agent_runtime_bindings")
    assert pre_cols == {"id", "role"}, (
        f"Fixture must start with partial schema; got {pre_cols}"
    )
    assert _get_row_count(db, "agent_runtime_bindings") == 1

    from task_hounds_api.db import init_db
    init_db()

    post_cols = _get_columns(db, "agent_runtime_bindings")
    required = {
        "id", "role", "server_instance_id", "host", "port",
        "opencode_agent", "model", "binding_source", "updated_at",
    }
    missing = required - post_cols
    assert not missing, (
        f"init_db() must补 missing columns on partial "
        f"agent_runtime_bindings; missing: {missing}; got {post_cols}"
    )
    assert _get_row_count(db, "agent_runtime_bindings") == 1, (
        "Existing row must be preserved across the column补"
    )
    with sqlite3.connect(db) as c:
        role = c.execute(
            "SELECT role FROM agent_runtime_bindings WHERE id=1"
        ).fetchone()[0]
    assert role == "manager", "Existing row's data must be preserved"


def test_partial_run_checkpoints_gets_columns_added(monkeypatch, tmp_path):
    """DB has run_checkpoints(id) only. init_db() must补 the rest."""
    db = tmp_path / "phase8_partial_rcp.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()

    _seed_partial_db(db, PARTIAL_RUN_CHECKPOINTS)
    assert _get_columns(db, "run_checkpoints") == {"id"}
    assert _get_row_count(db, "run_checkpoints") == 1

    from task_hounds_api.db import init_db
    init_db()

    post_cols = _get_columns(db, "run_checkpoints")
    required = {
        "id", "project_session_id", "workspace_id", "created_at",
        "reason", "status", "manager_state_json", "worker_state_json",
        "reviewer_state_json", "chat_state_json",
        "agent_registry_snapshot_json", "active_suggestion_id",
        "handoff_version", "plan_snapshot", "todos_snapshot_json",
        "opencode_servers_snapshot_json",
        "runtime_bindings_snapshot_json", "workspace_path",
        "resume_prompt", "notes",
    }
    missing = required - post_cols
    assert not missing, (
        f"init_db() must补 missing columns on partial run_checkpoints; "
        f"missing: {missing}"
    )
    assert _get_row_count(db, "run_checkpoints") == 1


def test_partial_runtime_policies_gets_columns_added(monkeypatch, tmp_path):
    """DB has runtime_policies(id) only. init_db() must补 the rest."""
    db = tmp_path / "phase8_partial_rp.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()

    _seed_partial_db(db, PARTIAL_RUNTIME_POLICIES)
    assert _get_columns(db, "runtime_policies") == {"id"}
    assert _get_row_count(db, "runtime_policies") == 1

    from task_hounds_api.db import init_db
    init_db()

    post_cols = _get_columns(db, "runtime_policies")
    required = {
        "id", "name", "close_behavior", "background_mode_enabled",
        "on_backend_exit", "on_backend_crash_recovery",
        "on_opencode_crash", "max_managed_opencode_servers",
        "default_topology", "default_shared_port",
        "allow_external_attach", "allow_unknown_attach", "updated_at",
    }
    missing = required - post_cols
    assert not missing, (
        f"init_db() must补 missing columns on partial runtime_policies; "
        f"missing: {missing}"
    )
    assert _get_row_count(db, "runtime_policies") == 1


def test_init_db_idempotent_on_partial_tables(monkeypatch, tmp_path):
    """Running init_db() twice on a partial DB is safe. The
    second run must NOT raise (ALTER TABLE ADD COLUMN on an
    existing column is a no-op for the migration runner,
    which swallows 'duplicate column' errors)."""
    db = tmp_path / "phase8_idempotent.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()

    _seed_partial_db(
        db,
        PARTIAL_AGENT_RUNTIME_BINDINGS + PARTIAL_RUN_CHECKPOINTS
        + PARTIAL_RUNTIME_POLICIES,
    )

    from task_hounds_api.db import init_db
    init_db()
    init_db()
    init_db()

    for table in ("agent_runtime_bindings", "run_checkpoints", "runtime_policies"):
        cols = _get_columns(db, table)
        assert len(cols) > 1, (
            f"{table} should have multiple columns after init_db(); got {cols}"
        )
        assert _get_row_count(db, table) == 1, (
            f"Existing row in {table} must survive 3 init_db() runs"
        )
