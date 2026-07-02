ALTER TABLE session_todos ADD COLUMN worker_task_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE session_todos ADD COLUMN reviewer_task_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE session_todos ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE session_todos ADD COLUMN human_attention_status TEXT NOT NULL DEFAULT 'none';

UPDATE session_todos
   SET status = 'pending'
 WHERE status = 'blocked';
