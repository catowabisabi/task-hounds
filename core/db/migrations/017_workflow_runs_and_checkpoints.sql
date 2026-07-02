-- 017_workflow_runs_and_checkpoints.sql
-- Add tables that the LangGraph workflow executor needs.
-- These existed in the old flow_01 temp DB; now part of the main schema.

CREATE TABLE IF NOT EXISTS workflow_runs (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    power_team_project_id       TEXT NOT NULL,
    project_session_id          TEXT NOT NULL,
    loop_index                  INTEGER NOT NULL,
    status                      TEXT NOT NULL,
    manager_opencode_session_id TEXT,
    worker_opencode_session_id  TEXT,
    reviewer_opencode_session_id TEXT,
    server_instance_id          INTEGER,
    input_json                  TEXT NOT NULL,
    output_json                 TEXT NOT NULL,
    created_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_session ON workflow_runs(project_session_id);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status);

CREATE TABLE IF NOT EXISTS flow_checkpoints (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    power_team_project_id   TEXT NOT NULL,
    project_session_id      TEXT NOT NULL,
    run_id                  INTEGER NOT NULL,
    step_name               TEXT NOT NULL,
    step_index              INTEGER NOT NULL,
    state_json              TEXT NOT NULL,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, step_index)
);

CREATE INDEX IF NOT EXISTS idx_flow_checkpoints_run ON flow_checkpoints(run_id);
