"""opencode.registry — tracks in-flight OpenCode run subprocesses.

Threads of BackgroundLoop._tick and BackgroundLoop.stop() run
concurrently. To interrupt a currently-running `opencode run --attach`
subprocess from stop(), the handle must be reachable from outside
_run_cmd. This module is the single source of truth.

Public API:
  register_run(run_id, proc)  — called from client._run_cmd after spawn
  unregister_run(run_id)      — called from client._run_cmd on completion
  kill_all_runs()             — called from BackgroundLoop.stop() (T4a)
  kill_process_tree(proc)     — single source for process-tree kill
  active_count()              — for observability / tests
"""
from __future__ import annotations

import os
import subprocess
import threading
from typing import Any


_RUN_REGISTRY: dict[str, subprocess.Popen] = {}
_RUN_REGISTRY_LOCK = threading.Lock()


def register_run(run_id: str, proc: subprocess.Popen) -> None:
    with _RUN_REGISTRY_LOCK:
        _RUN_REGISTRY[run_id] = proc


def unregister_run(run_id: str) -> None:
    with _RUN_REGISTRY_LOCK:
        _RUN_REGISTRY.pop(run_id, None)


def snapshot() -> dict[str, subprocess.Popen]:
    """Return a shallow copy of the current registry."""
    with _RUN_REGISTRY_LOCK:
        return dict(_RUN_REGISTRY)


def kill_process_tree(proc: subprocess.Popen) -> bool:
    """Best-effort kill of a subprocess + its children.

    Strategy:
      - Windows: subprocess.run(["taskkill", "/PID", <pid>, "/T", "/F"])
        kills the process tree.
      - non-Windows: proc.kill() (SIGKILL) terminates the process.

    Returns True if a kill attempt was made, False if the process was
    already dead.
    """
    if proc.poll() is not None:
        return False
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass
    else:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
    return True


def kill_all_runs() -> int:
    """Kill every registered subprocess. Returns the number killed.

    Called from BackgroundLoop.stop() so a Stop All request interrupts
    the current OpenCode run (P1.1). Uses kill_process_tree for the
    process-tree-aware kill.
    """
    killed = 0
    with _RUN_REGISTRY_LOCK:
        procs = list(_RUN_REGISTRY.values())

    for proc in procs:
        if kill_process_tree(proc):
            killed += 1
    return killed


def active_count() -> int:
    with _RUN_REGISTRY_LOCK:
        return len(_RUN_REGISTRY)

