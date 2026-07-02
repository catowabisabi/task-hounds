ALTER TABLE session_todos ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE session_todos ADD COLUMN plan_revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE session_todos ADD COLUMN archive_reason TEXT;
ALTER TABLE session_todos ADD COLUMN archive_note TEXT;
ALTER TABLE session_todos ADD COLUMN archived_at TIMESTAMP;
ALTER TABLE session_todos ADD COLUMN archived_by TEXT;
ALTER TABLE session_todos ADD COLUMN replaced_by_todo_id TEXT;
