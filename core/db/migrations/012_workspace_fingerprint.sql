-- Migration 012: Add workspace fingerprint for relink safety checks.

ALTER TABLE project_sessions ADD COLUMN workspace_fingerprint TEXT;
