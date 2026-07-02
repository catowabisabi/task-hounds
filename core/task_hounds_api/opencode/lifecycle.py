"""opencode.lifecycle — start/stop/restart one shared OpenCode server.

Manages a single long-lived `opencode serve` process. Tracks its
state in agent_runtime_bindings. Health check on demand.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from task_hounds_api.db import ROOT
from task_hounds_api.opencode import status_log
from task_hounds_api.opencode.binary import find
from task_hounds_api.opencode.process import (
    cleanup_orphaned_managed_serves,
    ensure_parent_watchdog,
    is_reachable,
    start_serve,
    stop_serve,
    wait_for_ready,
)


class OpenCodeLifecycle:
    """Manages one shared `opencode serve` instance.

    Usage:
        lc = OpenCodeLifecycle()
        if not lc.ensure_running():
            raise RuntimeError("opencode not running")
        lc.health()
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 18765):
        self.host = host
        self.port = port
        self._proc: subprocess.Popen | None = None

    def is_running(self) -> bool:
        running = is_reachable(self.host, self.port, timeout=1.5)
        if running and self._proc is not None:
            ensure_parent_watchdog(self._proc)
        return running

    def ensure_running(self) -> bool:
        """Start the server if not already up. Returns True if reachable."""
        binary = find(required=True)
        status_log.snapshot("opencode.lifecycle.ensure_running.begin", {
            "managed_host": self.host,
            "managed_port": self.port,
            "binary": str(binary),
        })
        cleanup_orphaned_managed_serves(binary)
        if self.is_running():
            status_log.snapshot("opencode.lifecycle.ensure_running.already_reachable", {
                "managed_host": self.host,
                "managed_port": self.port,
                "proc_pid": self._proc.pid if self._proc else None,
            })
            return True
        self._proc = start_serve(binary, self.host, self.port)
        ready = wait_for_ready(
            self.host,
            self.port,
            timeout=30.0,
            proc=self._proc,
        )
        status_log.snapshot("opencode.lifecycle.ensure_running.started", {
            "managed_host": self.host,
            "managed_port": self.port,
            "proc_pid": self._proc.pid if self._proc else None,
            "ready": ready,
        })
        return ready

    def stop(self) -> None:
        if self._proc is not None:
            status_log.snapshot("opencode.lifecycle.stop.begin", {
                "managed_host": self.host,
                "managed_port": self.port,
                "proc_pid": self._proc.pid,
            })
            stop_serve(self._proc)
            self._proc = None
            status_log.snapshot("opencode.lifecycle.stop.done", {
                "managed_host": self.host,
                "managed_port": self.port,
            })

    def health(self) -> dict:
        """Return health info dict. Used by API."""
        return {
            "ok": self.is_running(),
            "host": self.host,
            "port": self.port,
            "pid": self._proc.pid if self._proc else None,
        }
