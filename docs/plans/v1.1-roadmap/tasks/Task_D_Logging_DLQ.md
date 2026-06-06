# Task D — Structured Logging + Dead Letter Queue

## Goal
Replace ad-hoc `{agent}_stream.txt` files with structured JSON logs and implement a dead letter queue for failed tasks.

## Problem

### Current Logging
- Files like `runtime/agent_files/manager_stream.txt` contain plain text
- No machine-readable format
- No log levels, no timestamps, no correlation IDs
- No metrics or alerting hooks

### Current Error Handling
- Silence timeout → partial progress saved to `{agent}_partial.txt`
- Retry up to 3 times (in-memory counter)
- No persistence of failed tasks
- No alerting

## Solution

## Sub-Task D1 — Structured Logging

### New File: `core/power_teams/logging/structured_logger.py`

```python
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StructuredLogger:
    """
    JSON-lines logger for agent events.
    Each line is a valid JSON object.
    """
    
    def __init__(
        self,
        agent_name: str,
        log_dir: Path | str | None = None,
        level: str = "INFO",
        include_pid: bool = True,
    ):
        self.agent = agent_name
        self.log_dir = Path(log_dir) if log_dir else get_default_log_dir()
        self.level = getattr(logging, level.upper(), logging.INFO)
        self.include_pid = include_pid
        self.log_file = self.log_dir / f"{agent_name}.jsonl"
        
        # Ensure log directory exists
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Also set up standard library logger for stderr
        self._std_logger = logging.getLogger(f"power_teams.{agent_name}")
        self._std_logger.setLevel(self.level)
        if not self._std_logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter(f"%(asctime)s [{agent_name}] %(message)s"))
            self._std_logger.addHandler(handler)
    
    def _make_record(self, event: str, data: dict | None = None, level: str = "INFO") -> dict:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": self.agent,
            "event": event,
            "level": level,
            "data": data or {},
        }
        if self.include_pid:
            record["pid"] = os.getpid()
        return record
    
    def _emit(self, record: dict) -> None:
        # Write JSON line to file
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        
        # Also forward to std logger
        std_level = getattr(logging, record["level"], logging.INFO)
        self._std_logger.log(std_level, json.dumps(record))
    
    def info(self, event: str, **kwargs) -> None:
        self._emit(self._make_record(event, kwargs, "INFO"))
    
    def warning(self, event: str, **kwargs) -> None:
        self._emit(self._make_record(event, kwargs, "WARNING"))
    
    def error(self, event: str, **kwargs) -> None:
        self._emit(self._make_record(event, kwargs, "ERROR"))
    
    def debug(self, event: str, **kwargs) -> None:
        self._emit(self._make_record(event, kwargs, "DEBUG"))
    
    def emit(self, event: str, data: dict | None = None, level: str = "INFO") -> None:
        """Generic emit with explicit data dict."""
        self._emit(self._make_record(event, data, level))


def get_default_log_dir() -> Path:
    """Returns runtime/logs/ directory."""
    return Path("runtime") / "logs"
```

### Usage in base.py

```python
# Before:
append_text(stream_file, f"[{timestamp}] {event}: {data}\n")

# After:
logger = StructuredLogger(agent_name)
logger.info("agent_started", suggestion_id=123)
logger.emit("stream_chunk", {"text": "Hello world"}, "INFO")
```

### Example Output

```jsonl
{"timestamp": "2026-05-29T15:30:01.234Z", "agent": "worker", "event": "suggestion_claimed", "level": "INFO", "data": {"suggestion_id": 42}, "pid": 12345}
{"timestamp": "2026-05-29T15:30:01.456Z", "agent": "worker", "event": "file_created", "level": "INFO", "data": {"path": "src/main.py", "size": 1024}, "pid": 12345}
{"timestamp": "2026-05-29T15:30:15.789Z", "agent": "worker", "event": "suggestion_completed", "level": "INFO", "data": {"suggestion_id": 42, "duration_s": 14.3}, "pid": 12345}
{"timestamp": "2026-05-29T15:30:45.012Z", "agent": "manager", "event": "handoff_updated", "level": "INFO", "data": {"version": 5, "files_changed": 3}, "pid": 12346}
```

### Log Aggregation (Future, v1.2)

```bash
# View all logs aggregated
cat runtime/logs/*.jsonl | jq -s 'sort_by(.timestamp)'

# Count events by type
cat runtime/logs/worker.jsonl | jq -r '.event' | sort | uniq -c

# Tail in real-time with JSON formatting
tail -f runtime/logs/worker.jsonl | jq .
```

## Sub-Task D2 — Dead Letter Queue

### New Table Schema

```sql
CREATE TABLE dead_letter_queue (
    id SERIAL PRIMARY KEY,
    suggestion_id INT REFERENCES suggestion_queue(id) ON DELETE SET NULL,
    project_session_id INT REFERENCES project_sessions(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    error_type TEXT NOT NULL,  -- 'SilenceTimeout', 'AssertionError', etc.
    error_message TEXT,
    partial_progress_path TEXT,  -- path to {agent}_partial.txt
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
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

CREATE INDEX idx_dead_letter_queue_created ON dead_letter_queue(created_at DESC);
CREATE INDEX idx_dead_letter_queue_agent ON dead_letter_queue(agent_name);
```

### New File: `core/power_teams/dlq.py`

```python
import subprocess
from typing import Protocol

import requests


class AlertHook(Protocol):
    def send(self, event_type: str, payload: dict) -> None: ...


class WebhookAlert:
    """Sends alerts to a configured webhook URL."""
    
    def __init__(self, webhook_url: str, timeout: float = 5.0):
        self.webhook_url = webhook_url
        self.timeout = timeout
    
    def send(self, event_type: str, payload: dict) -> None:
        try:
            requests.post(
                self.webhook_url,
                json={"event": event_type, **payload},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            logging.warning(f"Webhook delivery failed: {e}")


class TelegramAlert:
    """Sends alerts via Telegram bot."""
    
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
    
    def send(self, event_type: str, payload: dict) -> None:
        message = self._format_message(event_type, payload)
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            requests.post(url, json={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
            }, timeout=self.timeout)
        except requests.RequestException as e:
            logging.warning(f"Telegram delivery failed: {e}")


def get_active_hooks() -> list[AlertHook]:
    """Load active webhook hooks from DB."""
    rows = db.execute("SELECT url FROM dlq_webhooks WHERE active = TRUE")
    return [WebhookAlert(row["url"]) for row in rows]


def move_to_dlq(
    suggestion_id: int,
    agent_name: str,
    error: Exception,
    partial_progress: str | None = None,
) -> bool:
    """
    Move a failed suggestion to the dead letter queue.
    
    If retry_count < max_retries, increments retry_count and returns False.
    Otherwise, moves to DLQ, fires alerts, and returns True (permanently failed).
    """
    error_type = type(error).__name__
    error_message = str(error)[:1000]  # Truncate long errors
    
    # Check retry count
    row = db.execute(
        "SELECT retry_count, max_retries FROM suggestion_queue WHERE id = ?",
        (suggestion_id,)
    ).fetchone()
    
    if row is None:
        return True  # Suggestion already resolved
    
    retry_count = row["retry_count"]
    max_retries = row["max_retries"]
    
    if retry_count < max_retries:
        # Increment retry and reschedule
        db.execute("""
            UPDATE suggestion_queue
            SET retry_count = retry_count + 1,
                status = 'pending',
                updated_at = NOW()
            WHERE id = ?
        """, (suggestion_id,))
        return False  # Will be retried
    
    # Max retries exceeded — move to DLQ
    db.execute("""
        INSERT INTO dead_letter_queue
            (suggestion_id, agent_name, error_type, error_message, partial_progress_path)
        VALUES (?, ?, ?, ?, ?)
    """, (suggestion_id, agent_name, error_type, error_message, partial_progress))
    
    db.execute("""
        UPDATE suggestion_queue
        SET status = 'dead_letter', updated_at = NOW()
        WHERE id = ?
    """, (suggestion_id,))
    
    # Fire all active hooks
    hooks = get_active_hooks()
    for hook in hooks:
        try:
            hook.send("dlq_created", {
                "suggestion_id": suggestion_id,
                "agent_name": agent_name,
                "error_type": error_type,
                "error_message": error_message,
                "retry_count": retry_count,
            })
        except Exception as e:
            logging.error(f"Alert hook failed: {e}")
    
    return True  # Permanently failed
```

### Integration with base.py (silence timeout)

```python
# In base.py — send_to_agent() — on silence timeout
def send_to_agent(agent_name: str, prompt: str, ...):
    ...
    except SilenceTimeout:
        partial_file = files_dir() / f"{agent_name}_partial.txt"
        partial_file.write_text(partial_so_far, encoding="utf-8")
        
        suggestion_id = get_active_suggestion_id()
        
        move_to_dlq(
            suggestion_id=suggestion_id,
            agent_name=agent_name,
            error=SilenceTimeout("Agent exceeded SILENCE_TIMEOUT"),
            partial_progress=str(partial_file),
        )
        
        _agent_silence_count[agent_name] += 1
        
        if _agent_silence_count[agent_name] >= 3:
            raise MaxRetriesExceeded(f"Agent {agent_name} exceeded max retries")
```

## Files to Create

| File | Purpose |
|------|---------|
| `core/power_teams/logging/__init__.py` | Package init |
| `core/power_teams/logging/structured_logger.py` | JSON logger |
| `core/power_teams/dlq.py` | Dead letter queue logic |
| `docs/guides/STRUCTURED_LOGGING.md` | Usage guide |

## Files to Modify

| File | Change |
|------|--------|
| `core/power_teams/agents/base.py` | Replace `append_text(stream_file)` with logger calls |
| `core/power_teams/agents/worker.py` | Replace stream writes with logger |
| `core/power_teams/mvp/runner.py` | Add structured logging for loop events |
| `data/schema.sql` | Add DLQ tables |

## Acceptance Criteria

### Logging
- [ ] All agent events written as JSON lines to `runtime/logs/{agent}.jsonl`
- [ ] Each log line has: timestamp, agent, event, level, data, pid
- [ ] Log files are append-only (no rotation yet, but structured for future logrotate)
- [ ] `cat runtime/logs/worker.jsonl | jq` produces valid formatted output

### DLQ
- [ ] Failed suggestions (after max_retries) go to `dead_letter_queue` table
- [ ] Partial progress path stored for debugging
- [ ] Active webhook URLs receive POST on DLQ creation
- [ ] `suggestion_queue.status = 'dead_letter'` marks permanently failed tasks
- [ ] DLQ items can be manually resolved (resolved_at set, status updated)

## Effort
1 person, 1–2 weeks