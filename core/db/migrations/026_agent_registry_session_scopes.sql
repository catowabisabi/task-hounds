-- 026_agent_registry_session_scopes.sql
-- Add project_session_id and role_session_id columns to agent_registry
-- so a busy/idle/error row in the registry can be tied back to a specific
-- project session and a specific role-scoped OpenCode session.
--
-- Without these columns, the registry can only express "agent is busy in
-- SOME project" — which made Chat thinking appear to block every other
-- project in the UI (s7c). With them, the API can scope state queries to
-- {project_session_id, role} and the UI can poll only its own scope.
--
-- Idempotent: uses the same ALTER TABLE ADD COLUMN pattern as
-- _complete_runtime_table_columns() in db/__init__.py. Re-runs are
-- no-ops because ADD COLUMN on an existing column raises
-- "duplicate column" which the migration runner swallows.

ALTER TABLE agent_registry ADD COLUMN project_session_id TEXT;
ALTER TABLE agent_registry ADD COLUMN role_session_id     TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_registry_project_session
    ON agent_registry(project_session_id);
CREATE INDEX IF NOT EXISTS idx_agent_registry_role_session
    ON agent_registry(role_session_id);
