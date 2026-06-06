"""Tests for user_directives lifecycle states.

Regression for the silently-swallowed-exception bug: the old code did
`try: graph.run_loop(fi) finally: mark_directive_processed(id)`, so
crashes got marked as successful. New lifecycle:

  pending -> running -> processed | failed(error)

Plus an atomic claim function that prevents two processes from picking
the same directive.
"""
from __future__ import annotations

import os
import sys
import tempfile
import gc
import time as time_mod
import threading
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def temp_db(monkeypatch):
    fd, db_path = tempfile.mkstemp(prefix="task_hounds_directive_", suffix=".db")
    os.close(fd)
    monkeypatch.setenv("POWER_TEAMS_DB", db_path)

    for name in list(sys.modules):
        if name == "task_hounds_api" or name.startswith("task_hounds_api."):
            sys.modules.pop(name, None)

    yield Path(db_path)

    for name in list(sys.modules):
        if name == "task_hounds_api" or name.startswith("task_hounds_api."):
            sys.modules.pop(name, None)
    gc.collect()

    for suffix in ("", "-wal", "-shm"):
        target = Path(db_path + suffix)
        for _ in range(5):
            try:
                target.unlink()
                break
            except FileNotFoundError:
                break
            except PermissionError:
                time_mod.sleep(0.1)


def _fresh():
    """Re-import chat ops under a freshly reloaded DB_PATH."""
    from task_hounds_api.db import connect, init_db
    from task_hounds_api.db.ops import chat as db_chat
    init_db()
    return connect, db_chat


def test_pending_to_running_to_processed(temp_db):
    connect, db_chat = _fresh()
    sid = "ps_test"
    did = db_chat.create_directive(sid, "do thing")
    assert did > 0

    claimed = db_chat.claim_pending_directive(sid)
    assert claimed is not None
    assert claimed["id"] == did
    assert claimed["status"] == "running"

    db_chat.mark_directive_status(did, "processed")
    with connect() as db:
        row = db.execute(
            "SELECT status, error FROM user_directives WHERE id=?", (did,)
        ).fetchone()
    assert row["status"] == "processed"
    assert row["error"] is None


def test_pending_to_running_to_failed_with_error(temp_db):
    connect, db_chat = _fresh()
    sid = "ps_test"
    did = db_chat.create_directive(sid, "do thing")
    db_chat.claim_pending_directive(sid)
    db_chat.mark_directive_status(did, "failed", error="graph crashed: RuntimeError('boom')")
    with connect() as db:
        row = db.execute(
            "SELECT status, error FROM user_directives WHERE id=?", (did,)
        ).fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "graph crashed: RuntimeError('boom')"


def test_claim_is_atomic(temp_db):
    connect, db_chat = _fresh()
    sid = "ps_test"
    did = db_chat.create_directive(sid, "do thing")

    results = []
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait()
        results.append(db_chat.claim_pending_directive(sid))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    claimed_rows = [r for r in results if r is not None]
    none_results = [r for r in results if r is None]
    assert len(claimed_rows) == 1, f"expected exactly 1 claim, got {len(claimed_rows)}"
    assert len(none_results) == 1
    assert claimed_rows[0]["id"] == did


def test_failed_directive_not_re_picked(temp_db):
    connect, db_chat = _fresh()
    sid = "ps_test"
    did1 = db_chat.create_directive(sid, "first")
    db_chat.claim_pending_directive(sid)
    db_chat.mark_directive_status(did1, "failed", error="boom")

    did2 = db_chat.create_directive(sid, "second")
    claimed = db_chat.claim_pending_directive(sid)
    assert claimed is not None
    assert claimed["id"] == did2
