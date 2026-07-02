-- Migration: 029_reviewer_issues
-- Desc: Add reviewer_issues table for structured bug/issue tracking
-- Reviewer writes found issues here as a side effect of reviewer_check
-- Manager can optionally read to generate todos

CREATE TABLE IF NOT EXISTS reviewer_issues (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_session_id  TEXT NOT NULL,
    suggestion_id       INTEGER,
    issue_type          TEXT NOT NULL,       -- 'bug' / 'ui_ux' / 'risk' / 'other'
    severity            INTEGER DEFAULT 3,    -- 1-5, 1=最嚴重
    description         TEXT NOT NULL,
    file_path           TEXT,
    line_number         INTEGER,
    status              TEXT DEFAULT 'open',  -- 'open' / 'acknowledged' / 'fixed' / 'dismissed'
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_session_id) REFERENCES project_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reviewer_issues_session
    ON reviewer_issues(project_session_id);

CREATE INDEX IF NOT EXISTS idx_reviewer_issues_status
    ON reviewer_issues(status);

CREATE INDEX IF NOT EXISTS idx_reviewer_issues_severity
    ON reviewer_issues(severity);
