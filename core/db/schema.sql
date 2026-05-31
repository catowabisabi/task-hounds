-- ============================================================
-- Agent Company System - SQLite Schema
-- Version: 0.3
-- ============================================================

-- Companies
CREATE TABLE IF NOT EXISTS companies (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT,
    parent_id   TEXT,
    industry    TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    dissolved_at TIMESTAMP
);

-- Departments
CREATE TABLE IF NOT EXISTS departments (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    type            TEXT,
    company_id      TEXT NOT NULL,
    manager_agent_id TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    dissolved_at    TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

-- Teams
CREATE TABLE IF NOT EXISTS teams (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    dept_id          TEXT NOT NULL,
    company_id      TEXT NOT NULL,
    lead_agent_id   TEXT,
    worker_count    INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    dissolved_at    TIMESTAMP,
    FOREIGN KEY (dept_id) REFERENCES departments(id),
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

-- Agents
CREATE TABLE IF NOT EXISTS agents (
    id                  TEXT PRIMARY KEY,
    name                TEXT,
    role                TEXT NOT NULL,
    type                TEXT DEFAULT 'permanent',
    company_id          TEXT,
    dept_id             TEXT,
    team_id             TEXT,
    state               TEXT DEFAULT 'idle',
    model               TEXT,
    opencode_url        TEXT,
    opencode_auth       TEXT,
    location            TEXT DEFAULT 'local',
    context_window      INTEGER DEFAULT 0,
    last_heartbeat      TIMESTAMP,
    missed_beats        INTEGER DEFAULT 0,
    heartbeat_interval_sec INTEGER DEFAULT 30,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    dissolved_at        TIMESTAMP,
    metadata            TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    FOREIGN KEY (dept_id) REFERENCES departments(id),
    FOREIGN KEY (team_id) REFERENCES teams(id)
);

-- Tasks
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT,
    status      TEXT DEFAULT 'todo',
    priority    TEXT DEFAULT 'medium',
    assigned_to TEXT,
    team_id     TEXT,
    created_by  TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP,
    tags        TEXT,
    attachments TEXT,
    FOREIGN KEY (team_id) REFERENCES teams(id),
    FOREIGN KEY (assigned_to) REFERENCES agents(id),
    FOREIGN KEY (created_by) REFERENCES agents(id)
);

-- Memos
CREATE TABLE IF NOT EXISTS memos (
    id          TEXT PRIMARY KEY,
    from_agent  TEXT NOT NULL,
    team_id     TEXT,
    content     TEXT NOT NULL,
    status      TEXT DEFAULT 'open',
    reply       TEXT,
    replied_by  TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at  TIMESTAMP,
    FOREIGN KEY (from_agent) REFERENCES agents(id),
    FOREIGN KEY (team_id) REFERENCES teams(id),
    FOREIGN KEY (replied_by) REFERENCES agents(id)
);

-- Messages
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    type        TEXT DEFAULT 'email',
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL,
    subject     TEXT,
    body        TEXT,
    status      TEXT DEFAULT 'sent',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read_at     TIMESTAMP,
    reply_to    TEXT,
    FOREIGN KEY (from_agent) REFERENCES agents(id),
    FOREIGN KEY (to_agent) REFERENCES agents(id)
);

-- Parttime Requests
CREATE TABLE IF NOT EXISTS parttime_requests (
    id          TEXT PRIMARY KEY,
    team_id     TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    reason      TEXT,
    task_desc   TEXT,
    status      TEXT DEFAULT 'pending',
    approved_by TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    notify_count INTEGER DEFAULT 0,
    last_notify TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id),
    FOREIGN KEY (requested_by) REFERENCES agents(id),
    FOREIGN KEY (approved_by) REFERENCES agents(id)
);

-- Proxy Queries
CREATE TABLE IF NOT EXISTS proxy_queries (
    id              TEXT PRIMARY KEY,
    created_by      TEXT NOT NULL,
    query           TEXT NOT NULL,
    scope           TEXT,
    context_window  INTEGER DEFAULT 2000000,
    response        TEXT,
    status          TEXT DEFAULT 'pending',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TIMESTAMP,
    auto_delete_after_response BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (created_by) REFERENCES agents(id)
);

-- Whiteboards
CREATE TABLE IF NOT EXISTS whiteboards (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    content     TEXT,
    created_by  TEXT NOT NULL,
    scope       TEXT NOT NULL,
    scope_id    TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES agents(id)
);

-- Audit Log
CREATE TABLE IF NOT EXISTS audit_log (
    id          TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    action      TEXT NOT NULL,
    target_type TEXT,
    target_id   TEXT,
    data        TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);

-- Sessions Archive
CREATE TABLE IF NOT EXISTS sessions_arch (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key     TEXT NOT NULL,
    session_name    TEXT,
    agent_name      TEXT,
    folder_relation TEXT,
    worker_status   TEXT,
    token_usage     INTEGER DEFAULT 0,
    last_active_at  TIMESTAMP,
    archived_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Port Checks
CREATE TABLE IF NOT EXISTS port_checks (
    id          TEXT PRIMARY KEY,
    host        TEXT NOT NULL DEFAULT '127.0.0.1',
    port        INTEGER NOT NULL,
    is_running  INTEGER NOT NULL,
    output      TEXT NOT NULL,
    prompt      TEXT,
    extra_input TEXT,
    output_note TEXT,
    response    TEXT,
    model       TEXT,
    agent       TEXT,
    approval_format TEXT,
    output_mode TEXT,
    options_json TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- OpenCode Agent Registry
CREATE TABLE IF NOT EXISTS agent_registry (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    role            TEXT NOT NULL,
    host            TEXT NOT NULL DEFAULT '127.0.0.1',
    port            INTEGER NOT NULL,
    model           TEXT,
    opencode_agent  TEXT NOT NULL DEFAULT 'general',
    session_id      TEXT,
    state           TEXT NOT NULL DEFAULT 'idle',
    task_complete   INTEGER NOT NULL DEFAULT 0,
    parent_id       TEXT,
    relations_json  TEXT,
    last_error      TEXT,
    last_seen       TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP,
    UNIQUE(name),
    FOREIGN KEY (parent_id) REFERENCES agent_registry(id)
);

-- ============================================================
-- Project Handoff
-- ============================================================

CREATE TABLE IF NOT EXISTS project_handoff (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    version             INTEGER NOT NULL DEFAULT 1,

    -- Stable layer
    human_requirements  TEXT,
    working_direction   TEXT,
    references_demos    TEXT,
    file_structure      TEXT,
    important_files     TEXT,
    available_scripts   TEXT,
    existing_solutions  TEXT,

    -- Dynamic layer
    macro_flow          TEXT,
    current_task        TEXT,
    current_micro_flow  TEXT,
    human_concerns      TEXT,
    tested_files        TEXT,
    known_bugs          TEXT,
    completion_criteria TEXT,

    -- Meta
    session_id           TEXT,
    project_folder_location TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by          TEXT DEFAULT 'manager'
);

-- Suggestion Queue
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

-- Manager Messages History
CREATE TABLE IF NOT EXISTS manager_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT NOT NULL,
    session_id  TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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

CREATE INDEX IF NOT EXISTS idx_project_session_role_sessions_role
    ON project_session_role_sessions(role);

CREATE INDEX IF NOT EXISTS idx_project_session_role_sessions_opencode_session
    ON project_session_role_sessions(opencode_session_id);

CREATE TABLE IF NOT EXISTS session_plan (
    session_id TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    updated_by TEXT DEFAULT 'manager',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS session_todos (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    parent_id  TEXT,
    content    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    priority   TEXT DEFAULT 'medium',
    position   INTEGER DEFAULT 0,
    owner      TEXT DEFAULT 'manager',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS opencode_server_instances (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    power_teams_session_id  TEXT NOT NULL,
    agent_role              TEXT NOT NULL,
    host                    TEXT NOT NULL,
    port                    INTEGER NOT NULL,
    opencode_session_id     TEXT,
    project_folder          TEXT,
    pid                     INTEGER,
    started_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS worker_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    report      TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    content     TEXT NOT NULL,
    sender      TEXT NOT NULL DEFAULT 'chat',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_directives (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    directive   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runtime_policy (
    id                              INTEGER PRIMARY KEY CHECK (id = 1),
    name                            TEXT NOT NULL DEFAULT 'default',
    close_behavior                  TEXT NOT NULL DEFAULT 'ask',
    background_mode_enabled         INTEGER NOT NULL DEFAULT 0,
    on_backend_exit                 TEXT NOT NULL DEFAULT 'stop_managed_opencode',
    on_backend_crash_recovery       TEXT NOT NULL DEFAULT 'ask',
    on_opencode_crash               TEXT NOT NULL DEFAULT 'mark_error',
    max_managed_opencode_servers    INTEGER NOT NULL DEFAULT 1,
    default_topology                TEXT NOT NULL DEFAULT 'shared',
    default_shared_port             INTEGER NOT NULL DEFAULT 18765,
    allow_external_attach           INTEGER NOT NULL DEFAULT 1,
    allow_unknown_attach            INTEGER NOT NULL DEFAULT 0,
    updated_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- Indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_agents_role ON agents(role);
CREATE INDEX IF NOT EXISTS idx_agents_state ON agents(state);
CREATE INDEX IF NOT EXISTS idx_agents_company ON agents(company_id);
CREATE INDEX IF NOT EXISTS idx_agents_dept ON agents(dept_id);
CREATE INDEX IF NOT EXISTS idx_agents_team ON agents(team_id);

CREATE INDEX IF NOT EXISTS idx_tasks_team ON tasks(team_id);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_agent);
CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_agent);
CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(type);

CREATE INDEX IF NOT EXISTS idx_memos_team ON memos(team_id);
CREATE INDEX IF NOT EXISTS idx_memos_from ON memos(from_agent);

CREATE INDEX IF NOT EXISTS idx_parttime_team ON parttime_requests(team_id);
CREATE INDEX IF NOT EXISTS idx_parttime_status ON parttime_requests(status);

CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_log(agent_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

CREATE INDEX IF NOT EXISTS idx_whiteboards_scope ON whiteboards(scope);
CREATE INDEX IF NOT EXISTS idx_whiteboards_created_by ON whiteboards(created_by);
CREATE INDEX IF NOT EXISTS idx_port_checks_created ON port_checks(created_at);
CREATE INDEX IF NOT EXISTS idx_agent_registry_role ON agent_registry(role);
CREATE INDEX IF NOT EXISTS idx_agent_registry_state ON agent_registry(state);
CREATE INDEX IF NOT EXISTS idx_handoff_version ON project_handoff(version);
CREATE INDEX IF NOT EXISTS idx_suggestion_status ON suggestion_queue(status);
CREATE INDEX IF NOT EXISTS idx_manager_messages_created ON manager_messages(created_at);
CREATE INDEX IF NOT EXISTS idx_project_handoff_session ON project_handoff(session_id);
CREATE INDEX IF NOT EXISTS idx_suggestion_session ON suggestion_queue(session_id);
CREATE INDEX IF NOT EXISTS idx_manager_messages_session ON manager_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_session_todos_session ON session_todos(session_id);
CREATE INDEX IF NOT EXISTS idx_worker_reports_session ON worker_reports(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_user_directives_session ON user_directives(session_id);

-- ============================================================
-- Triggers
-- ============================================================

CREATE TRIGGER IF NOT EXISTS trigger_task_updated
AFTER UPDATE ON tasks
BEGIN
    UPDATE tasks SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trigger_whiteboard_updated
AFTER UPDATE ON whiteboards
BEGIN
    UPDATE whiteboards SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- ============================================================
-- End of schema
-- ============================================================
