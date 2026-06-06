# Task C — PG LISTEN/NOTIFY (Event-Driven Triggers)

## Goal
Replace `time.sleep()` polling with PostgreSQL `LISTEN/NOTIFY` for event-driven worker activation.

## Prerequisites
- Task B (PostgreSQL Migration) must be complete

## Problem with Polling

```python
# Current approach — wasteful and slow
while True:
    suggestion = acquire_worker_claim()  # Still polls DB
    if suggestion:
        worker_cycle(suggestion)
    time.sleep(10)  # 10s latency to react to new work
```

## Solution: LISTEN/NOTIFY Pattern

### PostgreSQL Mechanism

```sql
-- After releasing a suggestion, Manager sends:
NOTIFY suggestion_released, '42';

-- Worker listens for notifications:
LISTEN suggestion_released;
```

### Step 1 — Notification on release (manager.py)

```python
def release_suggestion(suggestion_id: int):
    """Mark a suggestion as released AND notify any waiting workers."""
    db.execute("""
        UPDATE suggestion_queue
        SET status = 'released', updated_at = NOW()
        WHERE id = ?
    """, (suggestion_id,))
    
    db.execute("""
        NOTIFY suggestion_released, ?
    """, (str(suggestion_id),))
```

### Step 2 — Event listener (db.py)

```python
def listen_suggestion_events(blocking: bool = True) -> Generator[int, None, None]:
    """
    Listen for suggestion_released notifications.
    
    Yields suggestion_id each time a suggestion is released.
    
    In blocking mode (default), this blocks and waits indefinitely.
    In non-blocking mode, yields available events and returns.
    """
    conn = get_pg_connection()  # Get pooled connection
    
    conn.execute("LISTEN suggestion_released")
    
    try:
        while True:
            # Wait for notification (blocks until event or timeout)
            conn.poll()
            
            # Process all pending notifications
            while conn.notifies:
                notify = conn.notifies.pop()
                if notify.channel == 'suggestion_released':
                    yield int(notify.payload)
            
            if not blocking:
                return
    except GeneratorExit:
        pass
    finally:
        conn.execute("UNLISTEN suggestion_released")
        put_pg_connection(conn)
```

### Step 3 — Worker event loop (worker.py)

```python
# Replace polling loop with event-driven listener
def worker_event_loop():
    """
    Main worker loop that waits for suggestion_released events
    instead of polling the DB every N seconds.
    """
    logger = StructuredLogger("worker")
    
    logger.info("worker_started", pid=os.getpid())
    
    for suggestion_id in listen_suggestion_events(blocking=True):
        try:
            suggestion = get_suggestion(suggestion_id)
            if suggestion is None:
                logger.warning("suggestion_not_found", suggestion_id=suggestion_id)
                continue
            
            if suggestion['status'] != 'released':
                logger.warning("suggestion_not_released", 
                              suggestion_id=suggestion_id, 
                              status=suggestion['status'])
                continue
            
            # Claim and process
            claimed = acquire_worker_claim_speculative(suggestion_id)
            if claimed is None:
                # Already claimed by another worker
                logger.info("claim_contention", suggestion_id=suggestion_id)
                continue
            
            logger.info("worker_claiming", suggestion_id=suggestion_id)
            worker_cycle(claimed)
            
        except Exception as e:
            logger.error("worker_loop_error", 
                        suggestion_id=suggestion_id, 
                        error=str(e))
            move_to_dlq(suggestion_id, "worker", e)
```

### Step 4 — Combined mode (hybrid: LISTEN/NOTIFY + polling fallback)

For resilience, keep a periodic poll as backup:

```python
def worker_event_loop():
    for suggestion_id in listen_suggestion_events(blocking=True):
        # Process event
        ...
    
    # This line is never reached unless blocking=False
    # Use with select() on both notify socket and poll interval
```

Or use `SELECT` on both the PG socket and a timeout:

```python
import select as sel

def worker_event_loop_with_fallback():
    """LISTEN/NOTIFY + 30s poll fallback."""
    
    def poll_once():
        """Non-blocking poll of the DB for edge cases."""
        suggestion = acquire_worker_claim()
        if suggestion:
            process_suggestion(suggestion)
    
    poll_interval = 30  # seconds
    last_poll = time.time()
    
    conn = get_pg_connection()
    conn.execute("LISTEN suggestion_released")
    
    try:
        while True:
            # Check for notifications with timeout
            now = time.time()
            timeout = max(0.1, poll_interval - (now - last_poll))
            
            if conn.poll(timeout=timeout):
                # Process notifications
                while conn.notifies:
                    notify = conn.notifies.pop()
                    if notify.channel == 'suggestion_released':
                        suggestion_id = int(notify.payload)
                        suggestion = get_suggestion(suggestion_id)
                        if suggestion:
                            process_suggestion(suggestion)
                last_poll = time.time()
            
            # Fallback poll
            if time.time() - last_poll >= poll_interval:
                poll_once()
                last_poll = time.time()
    finally:
        conn.execute("UNLISTEN suggestion_released")
```

## Files to Modify
- `core/power_teams/db.py` — add `listen_suggestion_events()`, `notify_suggestion_released()`
- `core/power_teams/agents/manager.py` — add `NOTIFY` after releasing suggestion
- `core/power_teams/agents/worker.py` — replace polling loop with event loop
- `core/power_teams/mvp/runner.py` — adapt main loop

## Latency Improvement

| Before | After |
|--------|-------|
| Worker sleeps 10s between polls | Worker activated in <100ms of release |
| 10s worst-case latency | Near-instant response |
| Constant DB load from polling | DB load only on actual events |

## Acceptance Criteria
- [ ] Worker reacts to released suggestion within 500ms
- [ ] No `time.sleep()` calls in worker loop
- [ ] Notification survives restarts (reconnect on connection loss)
- [ ] Fallback poll works if LISTEN/NOTIFY connection drops
- [ ] Multiple workers can listen simultaneously without interfering

## Effort
1 person, 1 week (after Task B complete)