ALTER TABLE session_todos
ADD COLUMN worker_timeout_count INTEGER NOT NULL DEFAULT 0;
