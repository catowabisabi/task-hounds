"""Tests for BackgroundLoop stop semantics.

Regression for the stub-stop bug: the old `stop_loop` returned
`{"stopping": True}` but didn't actually do anything. New semantics:

  - stop sets the loop's stop event (blocks next tick)
  - stop interrupts the current OpenCode subprocess via kill_all_runs
  - response shape: {"stopping": True, "current_run_cancel_requested": True,
                    "current_run_killed": bool}
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


def test_stop_loop_returns_cancel_requested():
    from task_hounds_api.workflow.loop import BackgroundLoop

    loop = BackgroundLoop(interval=60)
    result = loop.stop()

    assert isinstance(result, dict)
    assert result.get("stopping") is True
    assert result.get("current_run_cancel_requested") is True
    assert "current_run_killed" in result
    assert isinstance(result["current_run_killed"], bool)
