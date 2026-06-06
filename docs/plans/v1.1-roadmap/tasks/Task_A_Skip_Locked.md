# Task A — SQLite SKIP LOCKED

## Goal
Eliminate worker polling race condition with atomic dequeue using SQLite's `BEGIN IMMEDIATE` + `SKIP LOCKED`.

## Current Problem

```python
# runner.py — current polling approach (RACE CONDITION)
while True:
    suggestion = db.execute("""
        SELECT * FROM suggestion_queue
        WHERE status = 'released'
        ORDER BY id LIMIT 1
    """).fetchone()
    
    if suggestion and can_claim(suggestion):
        # Two workers can reach here simultaneously
        db.execute("UPDATE suggestion_queue SET status='worker_done' WHERE id=?", (suggestion['id'],))
        worker_cycle(suggestion)
    
    time.sleep(10)  # 10s polling interval
```

## Solution

### Step 1 — Atomic claim function (db.py)

```python
def acquire_worker_claim() -> dict | None:
    """
    Atomically claim one 'released' suggestion using SKIP LOCKED.
    Returns the suggestion row, or None if nothing available.
    Uses BEGIN IMMEDIATE to get a RESERVED lock and prevent writer conflicts.
    """
    import sqlite3
    db_path = get_db_path()
    
    with sqlite3.connect(db_path, timeout=30) as conn:
        # BEGIN IMMEDIATE acquires a RESERVED lock in WAL mode
        # This prevents other writers from starting while we read
        conn.execute("BEGIN IMMEDIATE")
        
        row = conn.execute("""
            SELECT * FROM suggestion_queue
            WHERE status = 'released'
            ORDER BY id
            LIMIT 1
            SKIP LOCKED
        """).fetchone()
        
        if row is None:
            conn.commit()
            return None
        
        # Mark as worker_done atomically within same transaction
        conn.execute("""
            UPDATE suggestion_queue
            SET status = 'worker_done', worker_claimed_at = ?
            WHERE id = ?
        """, (datetime.utcnow().isoformat(), row['id']))
        
        conn.commit()
        
        # Return as dict
        columns = [desc[0] for desc in conn.execute("SELECT * FROM suggestion_queue WHERE id=?", (row['id'],)).description]
        return dict(zip(columns, row))
```

### Step 2 — Worker cycle update (worker.py)

```python
def worker_cycle():
    """Called when we have a valid suggestion to work on."""
    suggestion = acquire_worker_claim()
    
    if suggestion is None:
        return  # No work available, will be notified via LISTEN/NOTIFY in future
    
    # Proceed with work using suggestion['id']
    do_work(suggestion)
    
    # On completion, mark done
    db.execute("UPDATE suggestion_queue SET status='done' WHERE id=?", (suggestion['id'],))
```

### Step 3 — Verify WAL mode enabled

Check that the DB is in WAL mode (required for SKIP LOCKED to work correctly with concurrent readers):

```python
# In db.py initialization or startup
def ensure_wal_mode():
    with get_db_connection() as conn:
        result = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if result != 'wal':
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()
```

### Step 4 — Remove manager lock dependency (optional cleanup)

The current `manager.lock` file-based mechanism can be replaced by SQLite's `BEGIN IMMEDIATE` transaction, which handles contention automatically.

## Verification Test

```python
# tests/test_skipped_lock.py
def test_concurrent_worker_claims():
    """Two workers should not claim the same suggestion."""
    import multiprocessing
    
    def worker_loop():
        claimed = []
        for _ in range(10):
            suggestion = acquire_worker_claim()
            if suggestion:
                claimed.append(suggestion['id'])
        return claimed
    
    # Setup: create 5 released suggestions
    for i in range(5):
        create_suggestion(status='released')
    
    with multiprocessing.Pool(2) as pool:
        results = pool.map(lambda _: worker_loop(), range(2))
    
    # Flatten and check uniqueness
    all_claimed = [sid for worker_claims in results for sid in worker_claims]
    assert len(all_claimed) == len(set(all_claimed)), "Duplicate claims detected!"
```

## Files to Modify
- `core/power_teams/db.py` — add `acquire_worker_claim()`
- `core/power_teams/agents/worker.py` — replace polling with `acquire_worker_claim()`
- `core/power_teams/agents/manager.py` — ensure `release_suggestion()` is atomic
- `core/power_teams/db.py` — add `ensure_wal_mode()` call at startup

## Acceptance Criteria
- [ ] Two worker processes started simultaneously do NOT claim the same suggestion
- [ ] Worker no longer uses `time.sleep()` for polling
- [ ] `BEGIN IMMEDIATE` is used so only one writer can proceed at a time
- [ ] SKIP LOCKED allows non-blocking "skip" of already-claimed rows
- [ ] Existing tests still pass

## Effort
1 person, 1–2 weeks