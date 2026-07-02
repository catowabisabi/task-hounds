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

# Maps agent name → (run_id, proc).  Used by compat_agent_kill to
# terminate a specific named agent's subprocess.
_AGENT_RUN_REGISTRY: dict[str, tuple[str, subprocess.Popen]] = {}
_AGENT_RUN_REGISTRY_LOCK = threading.Lock()
_WORKFLOW_RUN_REGISTRY: dict[int, dict[str, subprocess.Popen]] = {}
_WORKFLOW_RUN_REGISTRY_LOCK = threading.Lock()
_EXECUTION_REGISTRY: dict[str, tuple[str, subprocess.Popen]] = {}
_EXECUTION_REGISTRY_LOCK = threading.Lock()


def register_run(run_id: str, proc: subprocess.Popen) -> None:
    with _RUN_REGISTRY_LOCK:
        _RUN_REGISTRY[run_id] = proc


def unregister_run(run_id: str) -> None:
    with _RUN_REGISTRY_LOCK:
        _RUN_REGISTRY.pop(run_id, None)


def register_agent_run(name: str, run_id: str, proc: subprocess.Popen) -> None:
    """Register a named agent's active run.

    Called from client.run() so compat_agent_kill can locate and terminate
    the correct subprocess by agent name.
    """
    with _AGENT_RUN_REGISTRY_LOCK:
        _AGENT_RUN_REGISTRY[name] = (run_id, proc)


def unregister_agent_run(name: str, run_id: str | None = None) -> None:
    """Remove a named agent's entry on run completion.

    When ``run_id`` is supplied, only remove the entry if it still points
    at that same run. This prevents a late finally block from an older run
    from clearing a newer run for the same agent.
    """
    with _AGENT_RUN_REGISTRY_LOCK:
        if run_id is None:
            _AGENT_RUN_REGISTRY.pop(name, None)
            return
        entry = _AGENT_RUN_REGISTRY.get(name)
        if entry and entry[0] == run_id:
            _AGENT_RUN_REGISTRY.pop(name, None)


def register_workflow_run(workflow_run_id: int, run_id: str, proc: subprocess.Popen) -> None:
    with _WORKFLOW_RUN_REGISTRY_LOCK:
        _WORKFLOW_RUN_REGISTRY.setdefault(workflow_run_id, {})[run_id] = proc


def unregister_workflow_run(workflow_run_id: int, run_id: str) -> None:
    with _WORKFLOW_RUN_REGISTRY_LOCK:
        entries = _WORKFLOW_RUN_REGISTRY.get(workflow_run_id)
        if not entries:
            return
        entries.pop(run_id, None)
        if not entries:
            _WORKFLOW_RUN_REGISTRY.pop(workflow_run_id, None)


def kill_workflow_run(workflow_run_id: int) -> int:
    """Kill only OpenCode subprocesses owned by one GraphFlow run."""
    with _WORKFLOW_RUN_REGISTRY_LOCK:
        entries = list(_WORKFLOW_RUN_REGISTRY.pop(workflow_run_id, {}).items())
    killed = 0
    for run_id, proc in entries:
        with _RUN_REGISTRY_LOCK:
            _RUN_REGISTRY.pop(run_id, None)
        if kill_process_tree(proc):
            killed += 1
    return killed


def register_execution(execution_id: str, run_id: str, proc: subprocess.Popen) -> None:
    with _EXECUTION_REGISTRY_LOCK:
        _EXECUTION_REGISTRY[execution_id] = (run_id, proc)


def unregister_execution(execution_id: str, run_id: str | None = None) -> None:
    with _EXECUTION_REGISTRY_LOCK:
        entry = _EXECUTION_REGISTRY.get(execution_id)
        if entry and (run_id is None or entry[0] == run_id):
            _EXECUTION_REGISTRY.pop(execution_id, None)


def kill_execution(execution_id: str) -> bool:
    with _EXECUTION_REGISTRY_LOCK:
        entry = _EXECUTION_REGISTRY.pop(execution_id, None)
    if not entry:
        return False
    run_id, proc = entry
    with _RUN_REGISTRY_LOCK:
        _RUN_REGISTRY.pop(run_id, None)
    return kill_process_tree(proc)


def kill_agent_run(name: str) -> bool:
    """Kill the active subprocess for a named agent.

    Returns True if a kill was attempted, False if the agent had no
    active run registered.

    Does NOT update the DB — the caller is responsible for that.
    """
    with _AGENT_RUN_REGISTRY_LOCK:
        entry = _AGENT_RUN_REGISTRY.get(name)

    if entry is None:
        return False

    _run_id, proc = entry
    with _RUN_REGISTRY_LOCK:
        _RUN_REGISTRY.pop(_run_id, None)

    killed = kill_process_tree(proc)

    with _AGENT_RUN_REGISTRY_LOCK:
        _AGENT_RUN_REGISTRY.pop(name, None)

    return killed


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
