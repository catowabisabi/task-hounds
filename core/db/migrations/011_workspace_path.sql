-- Migration 011: Add workspace_path, path_missing, workspace_fingerprint to project_sessions

ALTER TABLE project_sessions ADD COLUMN workspace_path TEXT;
ALTER TABLE project_sessions ADD COLUMN path_missing INTEGER DEFAULT 0;
ALTER TABLE project_sessions ADD COLUMN workspace_fingerprint TEXT;
