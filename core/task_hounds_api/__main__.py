"""Task Hounds API entry point.

Run the FastAPI server:
    python -m task_hounds_api

Or with uvicorn:
    uvicorn task_hounds_api.api.main:app --port 8765
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable


def _can_bind(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _next_available_port(host: str, preferred: int) -> int | None:
    candidates = []
    for port in (8766, 8765):
        if port != preferred:
            candidates.append(port)
    candidates.extend(range(18951, 19001))

    for port in candidates:
        if _can_bind(host, port):
            return port
    return None


def _pids_listening_on_port(port: int) -> list[int]:
    if sys.platform == "win32":
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-NetTCPConnection "
                f"-LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
                "| Select-Object -ExpandProperty OwningProcess -Unique"
            ),
        ]
    else:
        cmd = ["sh", "-c", f"lsof -tiTCP:{port} -sTCP:LISTEN 2>/dev/null"]

    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    pids: list[int] = []
    for line in result.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid != os.getpid() and pid not in pids:
            pids.append(pid)
    return pids


def _stop_pids(pids: list[int]) -> bool:
    ok = True
    for pid in pids:
        if sys.platform == "win32":
            cmd = ["taskkill", "/PID", str(pid), "/T", "/F"]
        else:
            cmd = ["kill", "-TERM", str(pid)]
        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            ok = False
            continue
        ok = ok and result.returncode == 0
    return ok


def _wait_until_can_bind(host: str, port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _can_bind(host, port):
            return True
        time.sleep(0.2)
    return _can_bind(host, port)


def _choose_alternate_after_stop_failure(
    host: str,
    port: int,
    prompt: Callable[[str], str],
) -> int | None:
    if not sys.stdin.isatty():
        answer = "yes"
    else:
        answer = prompt(
            "Port is still unavailable. Use a new port instead? "
            "[yes = use new port / quit = exit]: "
        ).strip().lower()
    if answer in {"q", "quit", "exit"}:
        print("Startup cancelled.", file=sys.stderr)
        return None
    new_port = _next_available_port(host, port)
    if new_port is None:
        print("No free local port is available.", file=sys.stderr)
        return None
    print(f"Using alternate backend port {new_port}.", file=sys.stderr)
    return new_port


def _resolve_port_conflict(
    host: str,
    port: int,
    mode: str,
    prompt: Callable[[str], str] = input,
) -> int | None:
    if _can_bind(host, port):
        return port

    pids = _pids_listening_on_port(port)
    owner = f" pid(s): {', '.join(map(str, pids))}" if pids else ""
    print(f"Port {host}:{port} is already in use{owner}.", file=sys.stderr)

    choice = mode
    if choice == "ask":
        if not sys.stdin.isatty():
            choice = "new"
        else:
            answer = prompt(
                "Stop the process using this port? "
                "[yes = stop and reuse / no = use a new port / quit = exit]: "
            ).strip().lower()
            if answer in {"y", "yes"}:
                choice = "stop"
            elif answer in {"q", "quit", "exit"}:
                choice = "quit"
            else:
                choice = "new"

    if choice == "quit":
        print("Startup cancelled.", file=sys.stderr)
        return None

    if choice == "stop":
        if not pids:
            print("Could not identify the process using the port.", file=sys.stderr)
            return None
        if not _stop_pids(pids):
            print("Could not stop every process using the port.", file=sys.stderr)
            return _choose_alternate_after_stop_failure(host, port, prompt)
        if _wait_until_can_bind(host, port):
            print(f"Stopped existing process; reusing {host}:{port}.", file=sys.stderr)
            return port
        print(f"Port {host}:{port} is still unavailable after stopping the process.", file=sys.stderr)
        fresh_pids = _pids_listening_on_port(port)
        if fresh_pids:
            print(
                f"Port {host}:{port} is now held by pid(s): "
                f"{', '.join(map(str, fresh_pids))}.",
                file=sys.stderr,
            )
        return _choose_alternate_after_stop_failure(host, port, prompt)

    new_port = _next_available_port(host, port)
    if new_port is None:
        print("No free local port is available.", file=sys.stderr)
        return None
    print(f"Using alternate backend port {new_port}.", file=sys.stderr)
    return new_port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task Hounds FastAPI server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--port-conflict",
        choices=("ask", "stop", "new", "quit"),
        default=os.environ.get("TASK_HOUNDS_PORT_CONFLICT", "ask"),
        help="What to do when the requested port is already in use.",
    )
    parser.add_argument("--reload", action="store_true", help="(dev only)")
    parser.add_argument(
        "--reload-dir",
        action="append",
        default=[],
        help="Directory watched by uvicorn reload. May be provided more than once.",
    )
    args = parser.parse_args(argv)

    port = _resolve_port_conflict(args.host, args.port, args.port_conflict)
    if port is None:
        return 1

    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed. Run: pip install uvicorn", file=sys.stderr)
        return 1

    uvicorn.run(
        "task_hounds_api.api.main:app",
        host=args.host,
        port=port,
        reload=args.reload,
        reload_dirs=args.reload_dir or None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
