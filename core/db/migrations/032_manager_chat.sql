CREATE TABLE IF NOT EXISTS manager_chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    response_id TEXT,
    session_id TEXT NOT NULL,
    sender TEXT NOT NULL CHECK(sender IN ('human', 'manager')),
    message_type TEXT NOT NULL DEFAULT 'suggestion',
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_manager_chat_messages_session
    ON manager_chat_messages(session_id, id);

CREATE TABLE IF NOT EXISTS manager_chat_amendments (
    id TEXT PRIMARY KEY,
    response_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    amendment_type TEXT NOT NULL CHECK(amendment_type IN ('todo-amendment', 'user-directive-amend', 'handoff-amend')),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed', 'applied', 'rejected')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    applied_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_manager_chat_amendments_session
    ON manager_chat_amendments(session_id, status, created_at);
