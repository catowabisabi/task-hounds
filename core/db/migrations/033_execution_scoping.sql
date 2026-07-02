CREATE TABLE IF NOT EXISTS agent_execution_state (
    execution_id TEXT PRIMARY KEY,
    project_session_id TEXT NOT NULL,
    workflow_run_id INTEGER,
    role TEXT NOT NULL CHECK(role IN ('manager', 'worker', 'reviewer', 'manager_chat', 'chat')),
    agent_registry_name TEXT,
    opencode_session_id TEXT,
    server_instance_id INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',
    current_step TEXT,
    process_id INTEGER,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_execution_project
    ON agent_execution_state(project_session_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_execution_run
    ON agent_execution_state(workflow_run_id, role, updated_at);

CREATE TABLE IF NOT EXISTS opencode_session_bindings (
    opencode_session_id TEXT PRIMARY KEY,
    project_session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('manager', 'worker', 'reviewer', 'manager_chat', 'chat')),
    server_instance_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_session_id, role)
);
