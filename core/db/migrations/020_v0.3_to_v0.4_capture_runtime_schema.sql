-- 020_v0.3_to_v0.4_capture_runtime_schema.sql
--
-- Bumps the schema from v0.3 to v0.4.
--
-- The previous notes identified 3 runtime tables that were
-- CREATED by migrations but never reflected in the canonical
-- core/db/schema.sql:
--   * agent_runtime_bindings    (added by 013_opencode_lifecycle)
--   * run_checkpoints           (added by 017_workflow_runs_and_checkpoints)
--   * runtime_policies          (added by 014_lifecycle_tables)
--
-- A v0.3 production DB therefore has these tables but a fresh
-- init_db() on v0.3 schema.sql would not. This migration:
--   1. Adds a schema_version table so we can detect what
--      version any given DB is at (not just hope the migrations
--      sorted correctly).
--   2. Idempotently creates the 3 runtime tables with
--      IF NOT EXISTS so re-running this migration is a no-op.
--   3. Records the v0.3 -> v0.4 upgrade in schema_version.
--
-- This migration is SAFE to re-run. Every statement uses
-- IF NOT EXISTS or INSERT OR IGNORE so a v0.4 DB stays v0.4.

CREATE TABLE IF NOT EXISTS schema_version (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes       TEXT
);

-- Capture the 3 runtime tables that v0.3 schema.sql missed.
-- These match the production DB schema 1:1.

CREATE TABLE IF NOT EXISTS agent_runtime_bindings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    role                TEXT NOT NULL,
    server_instance_id  INTEGER,
    host                TEXT NOT NULL DEFAULT '127.0.0.1',
    port                INTEGER NOT NULL DEFAULT 18765,
    opencode_agent      TEXT NOT NULL DEFAULT 'general',
    model               TEXT,
    binding_source      TEXT NOT NULL DEFAULT 'auto',
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_instance_id) REFERENCES opencode_server_instances(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_bindings_role
    ON agent_runtime_bindings(role);
CREATE INDEX IF NOT EXISTS idx_agent_bindings_server
    ON agent_runtime_bindings(server_instance_id);

CREATE TABLE IF NOT EXISTS run_checkpoints (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_session_id          TEXT,
    workspace_id                TEXT,
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason                      TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'complete',
    manager_state_json          TEXT,
    worker_state_json           TEXT,
    reviewer_state_json         TEXT,
    chat_state_json             TEXT,
    agent_registry_snapshot_json TEXT,
    active_suggestion_id        INTEGER,
    handoff_version             INTEGER,
    plan_snapshot               TEXT,
    todos_snapshot_json         TEXT,
    opencode_servers_snapshot_json TEXT,
    runtime_bindings_snapshot_json TEXT,
    workspace_path              TEXT,
    resume_prompt               TEXT,
    notes                       TEXT
);

CREATE TABLE IF NOT EXISTS runtime_policies (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    name                        TEXT NOT NULL UNIQUE,
    close_behavior              TEXT NOT NULL DEFAULT 'ask',
    background_mode_enabled      INTEGER NOT NULL DEFAULT 0,
    on_backend_exit             TEXT NOT NULL DEFAULT 'stop_managed_opencode',
    on_backend_crash_recovery    TEXT NOT NULL DEFAULT 'ask',
    on_opencode_crash           TEXT NOT NULL DEFAULT 'mark_error',
    max_managed_opencode_servers INTEGER NOT NULL DEFAULT 1,
    default_topology            TEXT NOT NULL DEFAULT 'shared',
    default_shared_port         INTEGER NOT NULL DEFAULT 18765,
    allow_external_attach       INTEGER NOT NULL DEFAULT 1,
    allow_unknown_attach        INTEGER NOT NULL DEFAULT 0,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_version (version, notes)
VALUES ('v0.4', 'Captured agent_runtime_bindings, run_checkpoints, runtime_policies into canonical schema.sql. Idempotent re-run safe.');
