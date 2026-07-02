"""opencode.process — spawn, monitor, kill the OpenCode serve process.

Wraps subprocess.Popen. Writes the process PID to agent_registry.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from task_hounds_api.db import ROOT
from task_hounds_api.opencode import status_log
from task_hounds_api.opencode.log_rotation import rotate_if_needed

LOG_DIR = ROOT / "core" / "runtime" / "logs" / "opencode"
LOG_DIR.mkdir(parents=True, exist_ok=True)
OPENCODE_SERVE_CWD = ROOT / "core"


def _load_runtime_env() -> None:
    """Load credentials before generating the OpenCode runtime config.

    The Safe CMD preflight runs in a child Python process, so values found in
    `.env` do not propagate back to the parent CMD or supervisor. OpenCode serve
    is started before FastAPI loads dotenv; without this bootstrap its generated
    provider config contains an empty apiKey even though later `opencode run`
    subprocesses can see the key.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    runtime_dir = Path(
        os.environ.get(
            "POWER_TEAMS_RUNTIME_DIR",
            str(ROOT / "core" / "runtime"),
        )
    )
    for path in (
        ROOT / ".env",
        ROOT / "config" / ".env",
        runtime_dir / ".env",
    ):
        if path.exists():
            load_dotenv(path, override=False, encoding="utf-8-sig")


def is_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def find_free_port(preferred: int = 18765) -> int:
    """Return preferred if free, else scan 18765-18865."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as preferred_socket:
        try:
            preferred_socket.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_serve(binary: Path, host: str, port: int) -> subprocess.Popen:
    """Spawn `opencode serve` in the background. Returns the Popen handle."""
    log_path = LOG_DIR / f"opencode-serve-{port}.log"
    rotate_if_needed(log_path)
    log_file = log_path.open("a", encoding="utf-8", errors="replace")
    cmd = [str(binary), "serve", "--hostname", host, "--port", str(port)]
    env = _isolated_env()
    status_log.append("opencode.serve.start.request", {
        "cmd": cmd,
        "host": host,
        "port": port,
        "cwd": str(OPENCODE_SERVE_CWD),
        "log_path": str(log_path),
        "env": {
            "OPENCODE_CONFIG_DIR": env.get("OPENCODE_CONFIG_DIR"),
            "XDG_CONFIG_HOME": env.get("XDG_CONFIG_HOME"),
            "XDG_DATA_HOME": env.get("XDG_DATA_HOME"),
        },
    })
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        cwd=str(OPENCODE_SERVE_CWD),
        env=env,
    )
    status_log.append("opencode.serve.start.spawned", {
        "pid": proc.pid,
        "cmd": cmd,
        "host": host,
        "port": port,
    })
    _attach_parent_watchdog(proc)
    return proc


def cleanup_orphaned_managed_serves(binary: Path) -> int:
    """Kill orphaned managed `opencode serve` processes.

    Only processes launched from Task Hounds' managed runtime binary are
    considered, and only when their parent process no longer exists.
    Operator-owned external OpenCode servers from other paths are left
    alone.
    """
    if os.environ.get("TASK_HOUNDS_DISABLE_OPENCODE_ORPHAN_CLEANUP") == "1":
        status_log.append("opencode.serve.cleanup_orphans.skipped", {
            "reason": "TASK_HOUNDS_DISABLE_OPENCODE_ORPHAN_CLEANUP=1",
        })
        return 0
    if os.name != "nt":
        status_log.append("opencode.serve.cleanup_orphans.skipped", {
            "reason": "non-windows",
        })
        return 0
    target = str(binary)
    before = status_log.list_local_opencode_processes()
    escaped = target.replace("'", "''")
    script = (
        f"$target='{escaped}'; $count=0; "
        "$procs = Get-CimInstance Win32_Process -Filter \"Name='opencode.exe'\" "
        "| Where-Object { $_.ExecutablePath -eq $target -and $_.CommandLine -match ' serve ' }; "
        "foreach ($p in $procs) { "
        "$parent = Get-Process -Id $p.ParentProcessId -ErrorAction SilentlyContinue; "
        "if (-not $parent) { "
        "taskkill /PID $p.ProcessId /T /F | Out-Null; $count++ "
        "} "
        "}; "
        "Write-Output $count"
    )
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        status_log.append("opencode.serve.cleanup_orphans.exception", {
            "target": target,
            "before": before,
        })
        return 0
    try:
        count = int((result.stdout or "0").strip().splitlines()[-1])
    except (IndexError, ValueError):
        count = 0
    status_log.append("opencode.serve.cleanup_orphans.done", {
        "target": target,
        "before": before,
        "after": status_log.list_local_opencode_processes(),
        "killed_count": count,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    })
    return count


def ensure_parent_watchdog(proc: subprocess.Popen) -> None:
    """Restart the parent-death watchdog if it is missing or exited."""
    if proc.poll() is not None:
        return
    watchdog = getattr(proc, "_task_hounds_watchdog", None)
    if watchdog is not None and watchdog.poll() is None:
        return
    _attach_parent_watchdog(proc)


def _attach_parent_watchdog(proc: subprocess.Popen) -> None:
    watchdog = _start_parent_watchdog(proc.pid)
    try:
        setattr(proc, "_task_hounds_watchdog", watchdog)
    except Exception:
        pass


def _start_parent_watchdog(child_pid: int) -> subprocess.Popen | None:
    """Best-effort orphan cleanup for managed `opencode serve`.

    If the Python backend exits normally, FastAPI lifespan calls
    RuntimeManager.stop_all(), which kills the managed server. If the
    backend is closed abruptly, that cleanup hook may not run. This
    detached watchdog watches this Python process; when it disappears,
    it kills the OpenCode process tree.
    """
    if os.environ.get("TASK_HOUNDS_DISABLE_OPENCODE_WATCHDOG") == "1":
        return None
    parent_pid = os.getpid()
    if os.name == "nt":
        script = (
            f"$parent={parent_pid}; $child={int(child_pid)}; "
            "while ($true) { "
            "Start-Sleep -Milliseconds 1000; "
            "if (-not (Get-Process -Id $child -ErrorAction SilentlyContinue)) { exit 0 }; "
            "if (-not (Get-Process -Id $parent -ErrorAction SilentlyContinue)) { "
            "taskkill /PID $child /T /F | Out-Null; exit 0 "
            "} "
            "}"
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ]
    else:
        script = (
            f"parent={parent_pid}; child={int(child_pid)}; "
            "while kill -0 \"$child\" 2>/dev/null; do "
            "if ! kill -0 \"$parent\" 2>/dev/null; then "
            "kill -TERM \"$child\" 2>/dev/null; sleep 2; "
            "kill -KILL \"$child\" 2>/dev/null; exit 0; "
            "fi; sleep 1; done"
        )
        creationflags = 0
        cmd = ["sh", "-c", script]
    try:
        return subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=(os.name != "nt"),
        )
    except Exception:
        print(
            f"[opencode] warning: failed to start parent watchdog for pid={child_pid}",
            file=sys.stderr,
        )
        return None


def stop_serve(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        status_log.append("opencode.serve.stop.noop", {
            "pid": proc.pid,
            "reason": "already exited",
        })
        return
    status_log.append("opencode.serve.stop.request", {
        "pid": proc.pid,
        "before": status_log.list_local_opencode_processes(),
    })
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
    status_log.append("opencode.serve.stop.done", {
        "pid": proc.pid,
        "returncode": proc.poll(),
        "after": status_log.list_local_opencode_processes(),
    })


def wait_for_ready(
    host: str,
    port: int,
    timeout: float = 30.0,
    proc: subprocess.Popen | None = None,
) -> bool:
    """Poll the port until reachable, timeout, or the child exits."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_reachable(host, port, timeout=1.0):
            return True
        if proc is not None and proc.poll() is not None:
            status_log.append("opencode.serve.wait_for_ready.exited", {
                "pid": proc.pid,
                "returncode": proc.returncode,
                "host": host,
                "port": port,
            })
            return False
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

    _load_runtime_env()
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
