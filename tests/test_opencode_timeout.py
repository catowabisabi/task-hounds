"""Tests for OpenCode subprocess timeout enforcement in client._run_cmd.

Regression for the silent-hang bug where `proc.wait()` had no timeout
argument. With this fix:
  - `client.run(..., timeout=N)` returns within ~N seconds when the
    subprocess hangs
  - The subprocess is killed via `taskkill /T /F` (Windows) or
    `proc.kill()` (non-Windows)
  - The result dict has `ok: False` and `error.type == "TimeoutError"`
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


def test_run_cmd_kills_on_timeout(monkeypatch):
    from task_hounds_api.opencode import client as oc_client
    from unittest.mock import MagicMock, patch

    kill_event = threading.Event()

    class HangingStream:
        def __iter__(self):
            return self

        def __next__(self):
            kill_event.wait(timeout=30)
            raise StopIteration

    proc = MagicMock()
    proc.stdout = HangingStream()
    proc.stdin = MagicMock()
    proc.pid = 99999
    proc.kill = MagicMock(side_effect=lambda: (kill_event.set(), True)[1])
    proc.terminate = MagicMock(side_effect=lambda: (kill_event.set(), True)[1])

    kill_tree_calls = []
    def fake_kill_tree(p):
        kill_tree_calls.append(p)
        kill_event.set()
        return True

    result_holder = [None, None]

    def run_in_thread():
        with patch("task_hounds_api.opencode.client.is_reachable", return_value=True), \
             patch("task_hounds_api.opencode.client.subprocess.Popen", return_value=proc), \
             patch("task_hounds_api.opencode.registry.kill_process_tree", side_effect=fake_kill_tree):
            try:
                result_holder[0] = oc_client.run(
                    agent="test",
                    prompt="hi",
                    host="127.0.0.1",
                    port=18765,
                    model=None,
                    session_id=None,
                    on_chunk=None,
                    timeout=1,
                )
            except BaseException as e:
                result_holder[1] = e

    t = threading.Thread(target=run_in_thread, daemon=True)
    t.start()
    t.join(timeout=5)

    assert not t.is_alive(), (
        f"run() did not return within 5s. timeout parameter is being ignored. "
        f"proc.terminate.called={proc.terminate.called}, exc={result_holder[1]!r}"
    )
    assert proc.terminate.called, (
        "subprocess was not terminate()'d on timeout — pipe won't close"
    )
    assert kill_tree_calls, (
        "kill_process_tree was not called on timeout — process tree not killed"
    )
    result = result_holder[0]
    assert result is not None
    assert result.get("ok") is False
    assert result.get("error", {}).get("type") == "TimeoutError"
