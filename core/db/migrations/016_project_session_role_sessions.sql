CREATE TABLE IF NOT EXISTS project_session_role_sessions (
    project_session_id   TEXT NOT NULL,
    role                 TEXT NOT NULL CHECK(role IN ('manager', 'worker', 'reviewer', 'chat')),
    opencode_session_id  TEXT,
    server_instance_id   INTEGER,
    workspace_path       TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_session_id, role),
    FOREIGN KEY (project_session_id) REFERENCES project_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (server_instance_id) REFERENCES opencode_server_instances(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_project_session_role_sessions_role ON project_session_role_sessions(role);

CREATE INDEX IF NOT EXISTS idx_project_session_role_sessions_opencode_session ON project_session_role_sessions(opencode_session_id);

INSERT OR IGNORE INTO project_session_role_sessions (project_session_id, role, opencode_session_id, workspace_path)
SELECT id, 'manager', manager_session_id, workspace_path
FROM project_sessions
WHERE manager_session_id IS NOT NULL AND manager_session_id != '';

INSERT OR IGNORE INTO project_session_role_sessions (project_session_id, role, opencode_session_id, workspace_path)
SELECT id, 'worker', worker_session_id, workspace_path
FROM project_sessions
WHERE worker_session_id IS NOT NULL AND worker_session_id != '';

INSERT OR IGNORE INTO project_session_role_sessions (project_session_id, role, opencode_session_id, workspace_path)
SELECT id, 'reviewer', reviewer_session_id, workspace_path
FROM project_sessions
WHERE reviewer_session_id IS NOT NULL AND reviewer_session_id != '';

INSERT OR IGNORE INTO project_session_role_sessions (project_session_id, role, opencode_session_id, workspace_path)
SELECT id, 'chat', chat_session_id, workspace_path
FROM project_sessions
WHERE chat_session_id IS NOT NULL AND chat_session_id != '';
