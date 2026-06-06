-- Migration 013: OpenCode lifecycle management
-- Adds columns to opencode_server_instances + indexes

-- ============================================================
-- ENHANCED opencode_server_instances
-- ============================================================
ALTER TABLE opencode_server_instances ADD COLUMN owner TEXT DEFAULT 'power_teams' NOT NULL;
ALTER TABLE opencode_server_instances ADD COLUMN managed INTEGER DEFAULT 1 NOT NULL;
ALTER TABLE opencode_server_instances ADD COLUMN status TEXT DEFAULT 'unknown' NOT NULL;
ALTER TABLE opencode_server_instances ADD COLUMN pid INTEGER;
ALTER TABLE opencode_server_instances ADD COLUMN cwd TEXT;
ALTER TABLE opencode_server_instances ADD COLUMN command TEXT;
ALTER TABLE opencode_server_instances ADD COLUMN topology TEXT DEFAULT 'shared' NOT NULL;
ALTER TABLE opencode_server_instances ADD COLUMN roles_json TEXT;
ALTER TABLE opencode_server_instances ADD COLUMN agent_bindings_json TEXT;
ALTER TABLE opencode_server_instances ADD COLUMN project_session_id TEXT;
ALTER TABLE opencode_server_instances ADD COLUMN started_by TEXT;
ALTER TABLE opencode_server_instances ADD COLUMN started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE opencode_server_instances ADD COLUMN last_seen TIMESTAMP;
ALTER TABLE opencode_server_instances ADD COLUMN stopped_at TIMESTAMP;
ALTER TABLE opencode_server_instances ADD COLUMN stop_reason TEXT;
ALTER TABLE opencode_server_instances ADD COLUMN health_url TEXT;
ALTER TABLE opencode_server_instances ADD COLUMN version TEXT;
ALTER TABLE opencode_server_instances ADD COLUMN error_count INTEGER DEFAULT 0;
ALTER TABLE opencode_server_instances ADD COLUMN last_error TEXT;
ALTER TABLE opencode_server_instances ADD COLUMN last_error_at TIMESTAMP;

-- ============================================================
-- Indexes for opencode_server_instances
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_osi_owner ON opencode_server_instances(owner);
CREATE INDEX IF NOT EXISTS idx_osi_status ON opencode_server_instances(status);
CREATE INDEX IF NOT EXISTS idx_osi_project ON opencode_server_instances(project_session_id);
CREATE INDEX IF NOT EXISTS idx_osi_pid ON opencode_server_instances(pid);