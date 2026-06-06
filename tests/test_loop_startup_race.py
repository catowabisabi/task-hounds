"""Phase 7 (Blocker 3): Startup timeout race tests.

Asserts the slow-handshake-completes-after-timeout race is fixed:
  1. _run() with a stale captured_generation does NOT transition
     state to RUNNING.
  2. start() bumps the generation counter, so a new call after a
     previous timed-out start() invalidates the late handshake.
  3. stop() bumps the generation counter.
  4. End-to-end: a slow handshake (mocked) that completes after
     start()'s timeout must NOT flip a FAILED state back to RUNNING.
"""
from __future__ import annotations

import sys
import threading as _t
import time
from pathlib import Path

import pytest


class _HandshakeEvent(_t.Event):
    """Event subclass that returns True from wait() so existing
    `assert handshake.wait(...)` calls work without boilerplate."""
    def wait(self, timeout=None):  # type: ignore[override]
        return super().wait(timeout=timeout)

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "phase7_race_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    return db


def test_run_with_stale_generation_does_not_set_running(monkeypatch, fresh_db):
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    loop = loop_mod.BackgroundLoop()
    loop._state = loop_mod.STATE_FAILED
    loop._generation = 7
    captured = 6  # stale: one less than current

    rm = RuntimeManager.instance()
    monkeypatch.setattr(rm, "ensure_managed_running", lambda: True)
    monkeypatch.setattr(rm, "get_managed_health", lambda: {"pid": 9999})

    loop._run(captured, _t.Event())

    assert loop.get_state() == loop_mod.STATE_FAILED, (
        "Stale-generation _run must not overwrite FAILED with RUNNING"
    )


def test_run_with_current_generation_sets_running(monkeypatch, fresh_db):
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    loop = loop_mod.BackgroundLoop()
    loop.interval = 0.01  # unblock the tick loop quickly
    loop._state = loop_mod.STATE_STARTING
    loop._generation = 5
    captured = 5  # current

    rm = RuntimeManager.instance()
    monkeypatch.setattr(rm, "ensure_managed_running", lambda: True)
    monkeypatch.setattr(rm, "get_managed_health", lambda: {"pid": 1234})

    t = _t.Thread(target=loop._run, args=(captured, _t.Event()), daemon=True)
    t.start()
    time.sleep(0.1)
    assert loop.get_state() == loop_mod.STATE_RUNNING
    assert loop._pid == 1234
    loop.stop()
    t.join(timeout=2.0)


def test_run_with_ensure_failed_does_not_overwrite(monkeypatch, fresh_db):
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    loop = loop_mod.BackgroundLoop()
    loop._state = loop_mod.STATE_FAILED
    loop._last_start_error = "previous failure"
    loop._generation = 3
    captured = 3

    rm = RuntimeManager.instance()
    monkeypatch.setattr(rm, "ensure_managed_running", lambda: False)

    loop._run(captured, _t.Event())

    assert loop.get_state() == loop_mod.STATE_FAILED
    assert "not reachable" in (loop._last_start_error or "")


def test_stop_bumps_generation(fresh_db):
    from task_hounds_api.workflow import loop as loop_mod

    loop = loop_mod.BackgroundLoop()
    g0 = loop._generation
    loop.stop()
    g1 = loop._generation
    assert g1 > g0, "stop() must bump the generation counter"


def test_start_bumps_generation(fresh_db):
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    import unittest.mock as _mock
    import threading as _t

    loop = loop_mod.BackgroundLoop()
    loop.interval = 0.01

    rm = RuntimeManager.instance()
    hang_event = _t.Event()

    def slow_ensure():
        hang_event.wait(timeout=1.0)
        return True

    with _mock.patch.object(rm, "ensure_managed_running", side_effect=slow_ensure):
        g0 = loop._generation
        loop.start()
        g1 = loop._generation
    hang_event.set()
    loop.stop()
    if loop._thread:
        loop._thread.join(timeout=2.0)
    assert g1 > g0, f"start() must bump generation: g0={g0} g1={g1}"


def test_late_handshake_does_not_flip_failed_to_running(monkeypatch, fresh_db):
    """End-to-end race: handshake takes longer than start()'s timeout.

    The slow thread should NOT transition state to RUNNING after
    start() has already published FAILED. The generation counter
    is what prevents this."""
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    loop = loop_mod.BackgroundLoop()
    loop.interval = 0.01

    monkeypatch.setattr(loop_mod, "STARTUP_HANDSHAKE_TIMEOUT_S", 0.2)

    rm = RuntimeManager.instance()
    handshake_done = _HandshakeEvent()

    def slow_ensure():
        time.sleep(0.6)
        handshake_done.set()
        return True

    monkeypatch.setattr(rm, "ensure_managed_running", slow_ensure)
    monkeypatch.setattr(rm, "get_managed_health", lambda: {"pid": 4242})

    result = loop.start()

    assert result["state"] == loop_mod.STATE_FAILED
    assert loop.get_state() == loop_mod.STATE_FAILED

    assert handshake_done.wait(timeout=3.0), "handshake never finished"
    if loop._thread:
        loop._thread.join(timeout=2.0)

    final_state = loop.get_state()
    assert final_state == loop_mod.STATE_FAILED, (
        f"Late handshake must not flip FAILED -> RUNNING. "
        f"Got state={final_state!r}"
    )
    loop.stop()

