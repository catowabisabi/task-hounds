"""opencode.lifecycle — start/stop/restart one shared OpenCode server.

Manages a single long-lived `opencode serve` process. Tracks its
state in agent_runtime_bindings. Health check on demand.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from task_hounds_api.db import ROOT
from task_hounds_api.opencode.binary import find
from task_hounds_api.opencode.process import (
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
        return is_reachable(self.host, self.port, timeout=1.5)

    def ensure_running(self) -> bool:
        """Start the server if not already up. Returns True if reachable."""
        if self.is_running():
            return True
        binary = find(required=True)
        self._proc = start_serve(binary, self.host, self.port)
        return wait_for_ready(self.host, self.port, timeout=30.0)

    def stop(self) -> None:
        if self._proc is not None:
            stop_serve(self._proc)
            self._proc = None

    def health(self) -> dict:
        """Return health info dict. Used by API."""
        return {
            "ok": self.is_running(),
            "host": self.host,
            "port": self.port,
            "pid": self._proc.pid if self._proc else None,
        }
