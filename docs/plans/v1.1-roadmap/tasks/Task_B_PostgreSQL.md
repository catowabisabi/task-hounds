# Task B — PostgreSQL Migration

## Goal
Replace SQLite with PostgreSQL to support true multi-agent concurrency and prepare for LISTEN/NOTIFY.

## Scope
- Migrate schema from SQLite to PostgreSQL
- Create DB adapter layer to abstract dialect differences
- Migrate all data
- Ensure 100% backward compatibility

## Sub-Task B1 — Schema Migration (Owner: [Name])

### Deliverable: `data/schema.pg.sql`

```sql
-- PostgreSQL schema for power-teams v1.1

-- Enable UUID extension for session IDs
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- === Tables ===

CREATE TABLE project_sessions (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    workspace_path TEXT NOT NULL,
    manager_session_id TEXT,
    worker_session_id TEXT,
    reviewer_session_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE agent_registry (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    agent_type TEXT NOT NULL,  -- 'manager', 'worker', 'reviewer', 'chat'
    host TEXT,
    port INTEGER,
    model TEXT,
    opencode_agent TEXT,
    session_id TEXT,
    state TEXT DEFAULT 'idle',  -- 'idle', 'busy'
    last_seen TIMESTAMPTZ,
    relations_json TEXT,  -- JSON: {"worker": "worker_0001", ...}
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE project_handoff (
    id SERIAL PRIMARY KEY,
    project_session_id INTEGER REFERENCES project_sessions(id) ON DELETE CASCADE,
    version INTEGER DEFAULT 1,
    human_requirements TEXT,
    working_direction TEXT,
    file_structure TEXT,
    important_files TEXT,
    macro_flow TEXT,
    current_task TEXT,
    current_micro_flow TEXT,
    tested_files TEXT,
    known_bugs TEXT,
    completion_criteria TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE suggestion_queue (
    id SERIAL PRIMARY KEY,
    project_session_id INTEGER REFERENCES project_sessions(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'released', 'worker_done', 'done', 'dead_letter')),
    priority INTEGER DEFAULT 0,
    task_description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    worker_claimed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- Critical index for SKIP LOCKED performance
CREATE INDEX idx_suggestion_queue_status ON suggestion_queue(status, id);

CREATE TABLE manager_messages (
    id SERIAL PRIMARY KEY,
    project_session_id INTEGER REFERENCES project_sessions(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    message_type TEXT DEFAULT 'manager_to_human',  -- 'manager_to_human', 'human_to_manager'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE reviewer_sessions (
    id SERIAL PRIMARY KEY,
    suggestion_id INTEGER REFERENCES suggestion_queue(id) ON DELETE CASCADE,
    review_notes TEXT,
    review_result TEXT,  -- 'pass', 'fail', 'needs_revision'
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE run_checkpoints (
    id SERIAL PRIMARY KEY,
    project_session_id INTEGER REFERENCES project_sessions(id) ON DELETE CASCADE,
    manager_state_json TEXT,
    worker_state_json TEXT,
    plan_snapshot TEXT,
    todos_snapshot_json TEXT,
    resume_prompt TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE dead_letter_queue (
    id SERIAL PRIMARY KEY,
    suggestion_id INTEGER REFERENCES suggestion_queue(id) ON DELETE SET NULL,
    agent_name TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE dlq_webhooks (
    id SERIAL PRIMARY KEY,
    url TEXT NOT NULL,
    event_type TEXT DEFAULT 'dlq_created',
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE opencode_server_instances (
    id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    host TEXT DEFAULT 'localhost',
    port INTEGER NOT NULL,
    status TEXT DEFAULT 'running',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- === Migration Helper ===

-- Function to get next session_id value
CREATE SEQUENCE IF NOT EXISTS project_sessions_id_seq;
```

### Deliverable: `migrations/001_sqlite_to_pg.py`

Script that:
1. Reads existing SQLite DB
2. Exports each table to CSV
3. Imports into PostgreSQL
4. Validates row counts match
5. Reports any data type conversions

## Sub-Task B2 — DB Layer Adapter (Owner: [Name])

### Pattern: Backend abstraction

```python
# core/power_teams/db_backend.py
from abc import ABC, abstractmethod
from typing import Any, Protocol

class DBBackend(Protocol):
    def execute(self, sql: str, params: tuple = ()) -> list[dict]: ...
    def begin(self) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...
    
    # For LISTEN/NOTIFY (PostgreSQL only)
    def listen(self, channel: str) -> None: ...
    def notify(self, channel: str, payload: str = "") -> None: ...
    def poll(self, timeout: float = 0) -> None: ...

class SQLiteBackend:
    def __init__(self, db_path: str):
        import sqlite3
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
    
    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = self.conn.execute(sql, params)
        if sql.strip().upper().startswith(("SELECT", "PRAGMA")):
            return [dict(row) for row in cursor.fetchall()]
        return []
    
    def begin(self) -> None: self.conn.execute("BEGIN")
    def commit(self) -> None: self.conn.commit()
    def rollback(self) -> None: self.conn.rollback()
    def close(self) -> None: self.conn.close()

class PostgreSQLBackend:
    def __init__(self, conn_string: str):
        import psycopg2
        from psycopg2 import pool
        self.pool = pool.ThreadedConnectionPool(5, 20, conn_string)
    
    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = self.pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.description:
                    return [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]
                return []
        finally:
            self.pool.putconn(conn)
    
    # ... implement begin/commit/rollback/listen/notify/poll
```

### Files to Refactor
Every file that calls `db.execute()` needs to be checked for SQLite-isms:

| File | Changes Needed |
|------|----------------|
| `core/power_teams/db.py` | Replace `sqlite3` with backend abstraction |
| `core/power_teams/agents/base.py` | Session persistence, `send_to_agent` |
| `core/power_teams/agents/manager.py` | Handoff, suggestion queries |
| `core/power_teams/agents/worker.py` | Suggestion fetch |
| `core/power_teams/mvp/runner.py` | Checkpoints, loop state |

### SQLite-isms to Fix
- `%Y-%m-%d %H:%M:%S` date formatting → use `TIMESTAMPTZ` in PG
- `?` placeholders → `%s` in PostgreSQL (psycopg2 uses `%s` not `?`)
- `last_insert_rowid()` → `RETURNING id` clause
- `PRAGMA` statements → remove/replace
- `LIMIT 1 OFFSET n` → same in PG (no change needed)

## Coordination Protocol

1. **B1 and B2 work in parallel** on separate files initially
2. **Daily sync** at standup to review接口 changes
3. **Integration branch** (`feature/b-pg-migration`) merges both when ready
4. **Test in isolation first**: B1 tests with raw SQL, B2 tests with adapter

## Effort
2 people, 3–4 weeks (parallel work)

## Acceptance Criteria
- [ ] All 10+ SQLite tables migrated to PostgreSQL
- [ ] Migration script handles data without loss
- [ ] `db.execute()` works identically for both backends
- [ ] SKIP LOCKED works in PostgreSQL (even better than SQLite)
- [ ] No application-level code changes needed beyond config (DATABASE_URL)
- [ ] Performance: concurrent worker claims scale linearly