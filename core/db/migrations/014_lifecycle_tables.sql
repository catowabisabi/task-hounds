-- Migration 014: Lifecycle tables (split from 013 for safety)
-- These CREATE TABLE IF NOT EXISTS statements are idempotent and safe to run
-- even if 013 (ALTER TABLE) failed partway through.

-- ============================================================
-- agent_runtime_bindings
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_runtime_bindings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    role            TEXT NOT NULL,
    server_instance_id INTEGER,
    host            TEXT NOT NULL DEFAULT '127.0.0.1',
    port            INTEGER NOT NULL DEFAULT 18765,
    opencode_agent  TEXT NOT NULL DEFAULT 'general',
    model           TEXT,
    binding_source  TEXT NOT NULL DEFAULT 'auto',
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_instance_id) REFERENCES opencode_server_instances(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_bindings_role ON agent_runtime_bindings(role);
CREATE INDEX IF NOT EXISTS idx_agent_bindings_server ON agent_runtime_bindings(server_instance_id);

-- ============================================================
-- run_checkpoints
-- ============================================================
CREATE TABLE IF NOT EXISTS run_checkpoints (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_session_id      TEXT,
    workspace_id            TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason                  TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'complete',
    manager_state_json      TEXT,
    worker_state_json       TEXT,
    reviewer_state_json      TEXT,
    chat_state_json          TEXT,
    agent_registry_snapshot_json TEXT,
    active_suggestion_id    INTEGER,
    handoff_version         INTEGER,
    plan_snapshot           TEXT,
    todos_snapshot_json     TEXT,
    opencode_servers_snapshot_json TEXT,
    runtime_bindings_snapshot_json TEXT,
    workspace_path          TEXT,
    resume_prompt           TEXT,
    notes                  TEXT
);

CREATE INDEX IF NOT EXISTS idx_checkpoint_project ON run_checkpoints(project_session_id);
CREATE INDEX IF NOT EXISTS idx_checkpoint_created ON run_checkpoints(created_at);
CREATE INDEX IF NOT EXISTS idx_checkpoint_status ON run_checkpoints(status);

-- ============================================================
-- runtime_policies
-- ============================================================
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