"""opencode.process — spawn, monitor, kill the OpenCode serve process.

Wraps subprocess.Popen. Writes the process PID to agent_registry.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from task_hounds_api.db import ROOT

LOG_DIR = ROOT / "core" / "runtime" / "logs" / "opencode"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def is_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def find_free_port(preferred: int = 18765) -> int:
    """Return preferred if free, else scan 18765-18865."""
    if is_reachable("127.0.0.1", preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_serve(binary: Path, host: str, port: int) -> subprocess.Popen:
    """Spawn `opencode serve` in the background. Returns the Popen handle."""
    log_path = LOG_DIR / f"opencode-serve-{port}.log"
    log_file = log_path.open("a", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [str(binary), "serve", "--hostname", host, "--port", str(port)],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        cwd=str(ROOT),
        env=_isolated_env(),
    )
    return proc


def stop_serve(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_for_ready(host: str, port: int, timeout: float = 30.0) -> bool:
    """Poll the port until reachable or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_reachable(host, port, timeout=1.0):
            return True
        time.sleep(0.5)
    return False


def _isolated_env() -> dict[str, str]:
    """Set XDG_CONFIG_HOME / XDG_DATA_HOME / OPENCODE_CONFIG_DIR for isolation.

    OPENCODE_CONFIG_DIR points to a runtime-only directory whose
    opencode.jsonc has had its ${ENV_VAR} placeholders expanded; the
    opencode CLI does not perform env-var expansion itself, so we
    pre-expand the template before spawning.
    """
    from task_hounds_api.opencode.config import generate_runtime_config

    env = os.environ.copy()
    cfg = ROOT / "core" / "runtime" / "opencode_config"
    home = ROOT / "core" / "runtime" / "opencode_home"
    cfg.mkdir(parents=True, exist_ok=True)
    (home / ".config").mkdir(parents=True, exist_ok=True)
    (home / ".local" / "share").mkdir(parents=True, exist_ok=True)
    env.pop("OPENCODE_HOME", None)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    runtime_cfg_dir = generate_runtime_config(cfg / "opencode.jsonc")
    env["OPENCODE_CONFIG_DIR"] = str(runtime_cfg_dir)
    return env
