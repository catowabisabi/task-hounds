# v1.1 Architecture Upgrade — Implementation Plan

## Executive Summary

The v1.1 roadmap proposes three phases to address **race conditions**, **scalability**, and **observability**. After analyzing the current codebase and the upgrade proposal, here is a refined implementation plan with task breakdowns, ownership suggestions, and dependency graph.

---

## Recommended Task Breakdown

| # | Task | Difficulty | Priority | People | Status |
|---|------|-----------|----------|--------|--------|
| A | SQLite SKIP LOCKED (Phase 1) | Low | Do First | 1 | Ready |
| B | PostgreSQL Migration (Phase 2) | High | Core | 2 | Ready |
| C | PG LISTEN/NOTIFY (Phase 2) | High | After B | 1 | Blocked by B |
| D | Structured Logging + DLQ (Phase 3) | Medium | Independent | 1 | Ready |
| E | Docker Compose (Phase 3) | Low | Independent | 1 | Ready |

---

## Task A — SQLite SKIP LOCKED (Phase 1)

**Goal**: Eliminate worker polling race condition without introducing new infrastructure.

### Problem
- Multiple workers compete for `status=released` suggestions
- Current polling (10s interval) has a race window where two workers see the same task
- No atomic dequeue mechanism

### Solution
```python
# Before (race condition):
rows = db.execute("SELECT * FROM suggestion_queue WHERE status='released' LIMIT 1")
if rows and can_claim(rows[0]):
    db.execute("UPDATE suggestion_queue SET status='worker_done' WHERE id=?", rows[0]['id'])

# After (atomic with SKIP LOCKED):
db.execute("BEGIN IMMEDIATE")  # Acquire RESERVED lock in WAL mode
row = db.execute("""
    SELECT * FROM suggestion_queue
    WHERE status = 'released'
    ORDER BY id LIMIT 1
    SKIP LOCKED
""").fetchone()
if row:
    db.execute("UPDATE suggestion_queue SET status='worker_done' WHERE id=?", row['id'])
db.commit()
```

### Files to Modify
- `core/power_teams/db.py`
  - New: `acquire_worker_claim(session_id)` — atomic dequeue with BEGIN IMMEDIATE + SKIP LOCKED
  - Modify: `update_suggestion_status()` to support retry_on_lock
- `core/power_teams/agents/worker.py`
  - Replace polling loop with `acquire_worker_claim()` call
  - Remove `time.sleep(worker_poll_interval)`
- `core/power_teams/agents/manager.py`
  - Ensure `release_suggestion()` is atomic

### Verification
- Launch two worker instances simultaneously
- Only one should claim each released suggestion

### Effort: 1 person, 1–2 weeks

---

## Task B — PostgreSQL Migration (Phase 2, Core)

**Goal**: Replace SQLite with PostgreSQL for real multi-agent concurrency.

### Sub-Task B1 — Schema Migration (1 person)

**Files**: `data/schema.sql` → `data/schema.pg.sql` + `migrations/001_sqlite_to_pg.sql`

**Key changes**:
```sql
-- SQLite → PostgreSQL type mapping
-- INTEGER PRIMARY KEY → SERIAL PRIMARY KEY
-- TEXT → TEXT (OK in PG)
-- BOOLEAN → BOOLEAN (same)
-- TIMESTAMP → TIMESTAMPTZ (use with timezone)

-- New: SKIP LOCKED becomes even more reliable in PG
CREATE INDEX idx_suggestion_queue_status ON suggestion_queue(status);

-- New: advisory locks (optional bonus)
SELECT pg_try_advisory_lock(hashtext('suggestion', id));
```

**Deliverable**: Migration script that:
1. Exports data from SQLite
2. Creates PG schema
3. Imports data
4. Validates row counts match

### Sub-Task B2 — DB Layer Adapter (1 person)

**File**: `core/power_teams/db.py`

**Pattern**: Abstract away SQL dialect
```python
# Pseudocode — introduce a DB backend abstraction
class DBBackend(Protocol):
    def execute(self, sql: str, params: tuple) -> list[dict]: ...
    def begin(self) -> None: ...
    def commit(self) -> None: ...

class SQLiteBackend(DBBackend):
    ...

class PostgreSQLBackend(DBBackend):
    def __init__(self, conn_string: str):
        self.pool = psycopg2.pool.ThreadedConnectionPool(5, 20, conn_string)

    def execute(self, sql: str, params: tuple) -> list[dict]:
        with self.pool.getconn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
```

**Affected files** (all DB queries need audit):
- `core/power_teams/agents/manager.py` — handoff, suggestion queries
- `core/power_teams/agents/worker.py` — suggestion fetch
- `core/power_teams/agents/base.py` — session, agent_registry
- `core/power_teams/mvp/runner.py` — checkpoint, loop state
- `core/power_teams/db.py` — all functions

### Coordination
- B1 owns schema + migration script
- B2 owns `db.py` refactor
- Meet daily to sync on接口 changes
- B2 should start with a clean `db_pg.py` adapter first, then gradually migrate queries

### Effort: 2 people, 3–4 weeks (parallel B1 + B2)

---

## Task C — PG LISTEN/NOTIFY (Phase 2)

**Goal**: Replace `time.sleep()` polling with event-driven notification.

### Prerequisites
- Task B must be complete (requires PostgreSQL)

### Solution
```python
# core/power_teams/db.py
def listen_suggestion_released(pg_conn):
    """Generator that yields suggestion_id whenever a suggestion is released."""
    pg_conn.execute("LISTEN suggestion_released")
    while True:
        pg_conn.poll()  # Wait for notification
        while pg_conn.notifies:
            notify = pg_conn.notifies.pop()
            yield notify.payload  # suggestion_id
```

```python
# core/power_teams/agents/worker.py
# Instead of:
while True:
    suggestion = acquire_worker_claim()
    if suggestion:
        worker_cycle(suggestion)
    time.sleep(10)

# Now:
for suggestion_id in listen_suggestion_released(pg_conn):
    suggestion = get_suggestion(suggestion_id)
    if suggestion:
        worker_cycle(suggestion)
```

```python
# core/power_teams/agents/manager.py
# After releasing a suggestion:
def release_suggestion(suggestion_id: int):
    db.execute("UPDATE suggestion_queue SET status='released' WHERE id=?", (suggestion_id,))
    db.execute("NOTIFY suggestion_released, ?", (str(suggestion_id),))
```

### Files to Modify
- `core/power_teams/db.py` — `listen_suggestion_released()`, `notify_suggestion_released()`
- `core/power_teams/agents/worker.py` — replace polling loop
- `core/power_teams/agents/manager.py` — add NOTIFY after release

### Effort: 1 person, 1 week (after Task B)

---

## Task D — Structured Logging + Dead Letter Queue (Phase 3)

### Sub-Task D1 — Structured Logging

**Problem**: `{agent}_stream.txt` files are ad-hoc text, no metrics, no alerting.

**Solution**: Replace with structured JSON logs

```python
# core/power_teams/logging/structured_logger.py
import json
import logging
from datetime import datetime, timezone

class StructuredLogger:
    def __init__(self, agent_name: str, log_dir: Path):
        self.agent = agent_name
        self.log_file = log_dir / f"{agent_name}.jsonl"

    def emit(self, event: str, data: dict = None, level: str = "INFO"):
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": self.agent,
            "event": event,
            "level": level,
            "data": data or {},
        }
        with open(self.log_file, "a") as f:
            f.write(json.dumps(record) + "\n")

    def info(self, event: str, **kwargs): self.emit(event, kwargs, "INFO")
    def error(self, event: str, **kwargs): self.emit(event, kwargs, "ERROR")
```

**Files to modify**:
- `core/power_teams/agents/base.py` — replace `append_text(stream_file)` calls with logger
- `core/power_teams/mvp/runner.py` — add logger for loop events

**Optional upgrade path** (not in initial scope):
- Add OpenTelemetry trace IDs
- Ship to Loki/Grafana
- Add Prometheus counters

### Sub-Task D2 — Dead Letter Queue

**New table**:
```sql
CREATE TABLE dead_letter_queue (
    id SERIAL PRIMARY KEY,
    suggestion_id INT REFERENCES suggestion_queue(id),
    agent_name TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT,
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

-- Webhook for alerting
CREATE TABLE dlq_webhooks (
    id SERIAL PRIMARY KEY,
    url TEXT NOT NULL,
    event_type TEXT DEFAULT 'dlq_created',
    active BOOLEAN DEFAULT TRUE
);
```

**New file**: `core/power_teams/dlq.py`
```python
def move_to_dlq(suggestion_id: int, agent_name: str, error: Exception, max_retries: int = 3):
    retry_count = get_retry_count(suggestion_id)
    if retry_count < max_retries:
        increment_retry(suggestion_id)
        return False  # Will retry
    else:
        db.execute("""
            INSERT INTO dead_letter_queue (suggestion_id, agent_name, error_type, error_message)
            VALUES (?, ?, ?, ?)
        """, (suggestion_id, agent_name, type(error).__name__, str(error)))
        db.execute("UPDATE suggestion_queue SET status='dead_letter' WHERE id=?", (suggestion_id,))
        fire_webhook(suggestion_id)  # Telegram, Slack, etc.
        return True
```

**Files to modify**:
- `data/schema.sql` — add DLQ tables
- `core/power_teams/dlq.py` (new)
- `core/power_teams/agents/base.py` — call `move_to_dlq()` on silence timeout

### Effort: 1 person, 1–2 weeks

---

## Task E — Docker Compose

**Goal**: Containerize all components for easy deployment.

### Deliverables

**`docker-compose.yml`**:
```yaml
version: '3.9'

services:
  api:
    build:
      context: .
      dockerfile: Dockerfile.api
    ports:
      - "8765:8765"
    environment:
      - DATABASE_URL=postgresql://postgres:password@db:5432/power_teams
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - ./data:/app/data
      - ./runtime:/app/runtime

  agent-runner:
    build:
      context: .
      dockerfile: Dockerfile
    command: python -m power_teams.mvp.runner --auto-release
    environment:
      - DATABASE_URL=postgresql://postgres:password@db:5432/power_teams
    depends_on:
      - api
      - db

  db:
    image: postgres:16-alpine
    environment:
      - POSTGRES_DB=power_teams
      - POSTGRES_PASSWORD=password
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

**`Dockerfile.api`** (new):
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY core/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY core/ .
EXPOSE 8765
CMD ["python", "api/fastapi_server.py"]
```

**`Dockerfile` update**:
- Add `DATABASE_URL` env var support
- Update health check to ping DB

### Optional: Redis (Phase 2 enhancement only)
Only add Redis if you implement Redis pub/sub. Otherwise, skip.

### Effort: 1 person, 3–5 days (can start immediately, independent of all other tasks)

---

## Dependency Graph

```
                    ┌─────────────────────┐
                    │  Task E (Docker)     │
                    │  Can start anytime   │
                    └──────────┬────────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
         ▼                     ▼                     ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Task A         │  │  Task D         │  │
│  SKIP LOCKED    │  │  Logging + DLQ │  │
│  (Low risk)     │  │  (Medium)       │  │
└────────┬────────┘  └────────┬────────┘  │
         │                    │          │
         └─────────┬──────────┘          │
                   │                    │
                   ▼                    │
         ┌─────────────────┐            │
         │  Task B         │            │
         │  PostgreSQL     │◄───────────┤
         │  (HIGH effort)  │            │
         └────────┬────────┘            │
                  │                     │
                  ▼                     │
         ┌─────────────────┐           │
         │  Task C         │           │
         │  LISTEN/NOTIFY  │           │
         │  (BLOCKED by B) │           │
         └────────┬────────┘           │
                  │                    │
                  └────────────────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │  Integration     │
                  │  Testing         │
                  └─────────────────┘
```

---

## Recommended Start Order

1. **Task E (Docker Compose)** — No dependencies, builds infrastructure early
2. **Task A (SKIP LOCKED)** — Low risk, immediate improvement, validates test harness
3. **Task D (Logging + DLQ)** — Independent, can run in parallel with B
4. **Task B (PostgreSQL)** — Core upgrade, start after A validated
5. **Task C (LISTEN/NOTIFY)** — Only after B complete

---

## Scope Reductions (from original roadmap)

| Original | Recommendation | Reason |
|----------|---------------|--------|
| Redis pub/sub | Remove | Extra deployment dependency,违背self-contained原則. PG LISTEN/NOTIFY is sufficient |
| OpenTelemetry | Defer | Too complex for v1.1. Structured JSON logs first, OTel optional in v1.2 |
| k8s | Remove | Docker Compose sufficient for most deployments; k8s is v2.0 topic |

---

## Files Summary

| File | Tasks |
|------|-------|
| `core/power_teams/db.py` | A, B2, C |
| `core/power_teams/agents/base.py` | A, D1 |
| `core/power_teams/agents/worker.py` | A, C |
| `core/power_teams/agents/manager.py` | A, C |
| `core/power_teams/mvp/runner.py` | C, D1 |
| `core/power_teams/dlq.py` | D2 (new) |
| `core/power_teams/logging/structured_logger.py` | D1 (new) |
| `data/schema.sql` | B1, D2 |
| `data/schema.pg.sql` | B1 (new) |
| `migrations/001_sqlite_to_pg.sql` | B1 (new) |
| `Dockerfile` | E |
| `Dockerfile.api` | E (new) |
| `docker-compose.yml` | E (new) |