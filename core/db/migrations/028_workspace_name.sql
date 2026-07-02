-- Migration 028: project_sessions.workspace_name
-- Separates workspace display name from session name.
-- Workspace name is independent; session name is per-session.
-- For existing rows, workspace_name defaults to the current 'name' value.

ALTER TABLE project_sessions ADD COLUMN workspace_name TEXT;

UPDATE project_sessions SET workspace_name = name WHERE workspace_name IS NULL;
