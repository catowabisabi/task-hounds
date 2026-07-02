CREATE TABLE IF NOT EXISTS project_rounds (
    id TEXT PRIMARY KEY,
    project_session_id TEXT NOT NULL,
    round_number INTEGER NOT NULL,
    directive TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('draft', 'active', 'locked')),
    completion_run_id INTEGER,
    completion_summary TEXT,
    snapshot_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    locked_at TIMESTAMP,
    UNIQUE(project_session_id, round_number)
);

ALTER TABLE session_todos ADD COLUMN round_id TEXT;
ALTER TABLE user_directives ADD COLUMN round_id TEXT;
ALTER TABLE workflow_runs ADD COLUMN round_id TEXT;
ALTER TABLE project_handoff ADD COLUMN round_id TEXT;

INSERT OR IGNORE INTO project_rounds
    (id, project_session_id, round_number, directive, status, completion_run_id,
     completion_summary, snapshot_json, created_at, locked_at)
SELECT
    'round_' || ps.id || '_1',
    ps.id,
    1,
    COALESCE(
        (SELECT directive FROM user_directives ud
          WHERE ud.session_id=ps.id ORDER BY ud.id DESC LIMIT 1),
        'Imported project work'
    ),
    'active',
    NULL,
    'Imported from existing project session',
    NULL,
    ps.created_at,
    NULL
FROM project_sessions ps;

UPDATE session_todos
   SET round_id='round_' || session_id || '_1'
 WHERE round_id IS NULL;
UPDATE user_directives
   SET round_id='round_' || session_id || '_1'
 WHERE round_id IS NULL;
UPDATE workflow_runs
   SET round_id='round_' || project_session_id || '_1'
 WHERE round_id IS NULL;
UPDATE project_handoff
   SET round_id='round_' || session_id || '_1'
 WHERE round_id IS NULL;
