"""Tests that BackgroundLoop uses RuntimeManager instead of constructing
a fresh OpenCodeLifecycle (which lost the process handle on every restart).

Regression for the "stop_all kills nothing because the process handle
lives in a one-shot OpenCodeLifecycle" bug.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "loop_rm_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.db import init_db
    init_db()
    return db


@pytest.fixture()
def rm_mock(monkeypatch):
    rm = MagicMock()
    rm.ensure_managed_running.return_value = True
    rm.get_managed_health.return_value = {
        "ok": True,
        "host": "127.0.0.1",
        "port": 18957,
        "pid": 66666,
    }
    rm._managed_lifecycle = MagicMock()
    rm.instance.return_value = rm

    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.opencode import runtime_manager as rm_mod
    monkeypatch.setattr(loop_mod, "RuntimeManager", rm)
    monkeypatch.setattr(rm_mod, "RuntimeManager", rm)
    return rm


def test_background_loop_uses_runtime_manager(rm_mock, fresh_db):
    """BackgroundLoop._run should call RuntimeManager.instance() and read
    pid from get_managed_health, not construct a fresh OpenCodeLifecycle."""
    from task_hounds_api.workflow.loop import BackgroundLoop

    bg = BackgroundLoop(interval=1)
    bg.start()
    time.sleep(0.5)
    bg.stop()

    assert bg._pid == 66666
    assert rm_mock.ensure_managed_running.called


def test_loop_module_imports_runtime_manager(fresh_db):
    """loop.py must import RuntimeManager so _run / run_once can use the
    shared singleton instead of constructing a fresh OpenCodeLifecycle."""
    from task_hounds_api.workflow import loop as loop_mod
    assert hasattr(loop_mod, "RuntimeManager")
    assert loop_mod.RuntimeManager is not None
