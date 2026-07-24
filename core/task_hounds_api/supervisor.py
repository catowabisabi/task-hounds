"""Stable owner for OpenCode serve while FastAPI runs with --reload."""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from task_hounds_api.db import ROOT
from task_hounds_api.opencode.binary import find
from task_hounds_api.opencode.process import (
    cleanup_orphaned_managed_serves,
    find_free_port,
    start_serve,
    stop_serve,
    wait_for_ready,
)
from task_hounds_api.workflow.capacity import graphflow_worker_count


def _runtime_logger(name: str, filename: str) -> logging.Logger:
    logger = logging.getLogger(f"task_hounds.supervisor.{name}")
    if logger.handlers:
        return logger
    log_dir = Path(
        os.environ.get(
            "POWER_TEAMS_RUNTIME_DIR",
            str(ROOT / "core" / "runtime"),
        )
    ) / "logs" / "server-start"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / filename,
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def _drain_stream(stream: Any, logger: logging.Logger) -> None:
    try:
        for line in iter(stream.readline, ""):
            logger.info(line.rstrip())
    finally:
        try:
            stream.close()
        except Exception:
            pass


def runtime_dir() -> Path:
    path = Path(
        os.environ.get(
            "POWER_TEAMS_RUNTIME_DIR",
            str(ROOT / "core" / "runtime"),
        )
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path() -> Path:
    return runtime_dir() / "supervisor_state.json"


def command_path() -> Path:
    return runtime_dir() / "supervisor_command.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temp, path)


def request_restart(timeout: float = 40.0) -> dict[str, Any]:
    """Ask the stable supervisor to restart OpenCode and await its result."""
    command_id = uuid.uuid4().hex
    _write_json(command_path(), {
        "id": command_id,
        "action": "restart_opencode",
        "requested_at": time.time(),
    })
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _read_json(state_path())
        result = state.get("last_command") or {}
        if result.get("id") == command_id:
            return dict(result)
        time.sleep(0.2)
    return {
        "id": command_id,
        "ok": False,
        "error": "Supervisor did not answer the restart request in time.",
    }


class Supervisor:
    def __init__(self, host: str, api_port: int, reload_backend: bool) -> None:
        self.host = host
        self.api_port = api_port
        self.reload_backend = reload_backend
        self.preferred_opencode_port = int(
            os.environ.get("TASK_HOUNDS_OPENCODE_PORT", "18765")
        )
        self.opencode_port = self.preferred_opencode_port
        self.opencode_proc: subprocess.Popen | None = None
        self.backend_proc: subprocess.Popen | None = None
        self.graphflow_worker_procs: list[subprocess.Popen] = []
        self.last_command: dict[str, Any] = {}
        self.stopping = False
        self.backend_ready = False

    @staticmethod
    def _role_command(role: str, *args: str) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--runtime-role", role, *args]
        module = {
            "api": "task_hounds_api",
            "worker": "task_hounds_api.graphflow_worker",
        }[role]
        return [sys.executable, "-m", module, *args]

    def _state(self, error: str = "") -> dict[str, Any]:
        oc_alive = self.opencode_proc is not None and self.opencode_proc.poll() is None
        backend_alive = self.backend_proc is not None and self.backend_proc.poll() is None
        worker_pids = [
            proc.pid
            for proc in self.graphflow_worker_procs
            if proc.poll() is None
        ]
        return {
            "supervisor_pid": os.getpid(),
            "status": "stopping" if self.stopping else "running",
            "updated_at": time.time(),
            "backend": {
                "pid": self.backend_proc.pid if backend_alive else None,
                "status": (
                    "ready"
                    if backend_alive and self.backend_ready
                    else "starting"
                    if backend_alive
                    else "stopped"
                ),
                "port": self.api_port,
            },
            "graphflow_worker": {
                "pid": worker_pids[0] if worker_pids else None,
                "pids": worker_pids,
                "count": len(worker_pids),
                "desired_count": graphflow_worker_count(),
                "status": "running" if worker_pids else "stopped",
            },
            "opencode": {
                "pid": self.opencode_proc.pid if oc_alive else None,
                "status": "running" if oc_alive else "stopped",
                "host": self.host,
                "port": self.opencode_port,
                "error": error,
            },
            "last_command": self.last_command,
        }

    def publish(self, error: str = "") -> None:
        _write_json(state_path(), self._state(error))

    def start_opencode(self) -> tuple[bool, str]:
        binary = find(required=True)
        cleanup_orphaned_managed_serves(binary)
        selected = find_free_port(self.preferred_opencode_port)
        self.opencode_port = selected
        try:
            self.opencode_proc = start_serve(binary, self.host, selected)
            ready = wait_for_ready(
                self.host,
                selected,
                timeout=30.0,
                proc=self.opencode_proc,
            )
        except Exception as exc:
            self.opencode_proc = None
            return False, str(exc)
        if not ready:
            return False, f"OpenCode exited before becoming ready on {self.host}:{selected}."
        return True, ""

    def stop_opencode(self) -> None:
        if self.opencode_proc is not None:
            try:
                stop_serve(self.opencode_proc)
            except Exception:
                pass
        self.opencode_proc = None

    def start_backend(self) -> None:
        self.backend_ready = False
        env = os.environ.copy()
        env["TASK_HOUNDS_SUPERVISED"] = "1"
        env["TASK_HOUNDS_OPENCODE_PORT"] = str(self.opencode_port)
        cmd = self._role_command(
            "api",
            "--host",
            self.host,
            "--port",
            str(self.api_port),
            "--port-conflict",
            "quit",
        )
        if self.reload_backend:
            cmd.extend(
                [
                    "--reload",
                    "--reload-dir",
                    str(ROOT / "core" / "task_hounds_api"),
                ]
            )
        self.backend_proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT / "core"),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._capture_process_logs(
            self.backend_proc,
            "fastapi-8766.out.log",
            "fastapi-8766.err.log",
        )

    def wait_for_backend_ready(self, timeout: float = 60.0) -> bool:
        """Wait for the real ASGI app, not merely the uvicorn reloader."""
        deadline = time.monotonic() + timeout
        url = f"http://{self.host}:{self.api_port}/api/health"
        while time.monotonic() < deadline:
            if self.backend_proc is None or self.backend_proc.poll() is not None:
                self.backend_ready = False
                return False
            try:
                with urllib.request.urlopen(url, timeout=2.0) as response:
                    if 200 <= response.status < 500:
                        self.backend_ready = True
                        return True
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        self.backend_ready = False
        return False

    def _capture_process_logs(
        self,
        proc: subprocess.Popen,
        stdout_name: str,
        stderr_name: str,
    ) -> None:
        if proc.stdout is not None:
            threading.Thread(
                target=_drain_stream,
                args=(proc.stdout, _runtime_logger(stdout_name, stdout_name)),
                daemon=True,
                name=f"log-{stdout_name}",
            ).start()
        if proc.stderr is not None:
            threading.Thread(
                target=_drain_stream,
                args=(proc.stderr, _runtime_logger(stderr_name, stderr_name)),
                daemon=True,
                name=f"log-{stderr_name}",
            ).start()

    def stop_backend(self) -> None:
        self.backend_ready = False
        proc = self.backend_proc
        if proc is None or proc.poll() is not None:
            self.backend_proc = None
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
        self.backend_proc = None

    def start_graphflow_worker(self) -> subprocess.Popen:
        env = os.environ.copy()
        env["TASK_HOUNDS_SUPERVISED"] = "1"
        env["TASK_HOUNDS_OPENCODE_PORT"] = str(self.opencode_port)
        index = len(self.graphflow_worker_procs) + 1
        proc = subprocess.Popen(
            self._role_command("worker"),
            cwd=str(ROOT / "core"),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.graphflow_worker_procs.append(proc)
        self._capture_process_logs(
            proc,
            f"graphflow-worker-{index}.out.log",
            f"graphflow-worker-{index}.err.log",
        )
        return proc

    def stop_graphflow_worker(self, proc: subprocess.Popen) -> None:
        if proc is None or proc.poll() is not None:
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

    def start_graphflow_workers(self) -> None:
        self.graphflow_worker_procs = [
            proc for proc in self.graphflow_worker_procs if proc.poll() is None
        ]
        desired = graphflow_worker_count()
        while len(self.graphflow_worker_procs) < desired:
            self.start_graphflow_worker()

    def stop_graphflow_workers(self) -> None:
        for proc in list(self.graphflow_worker_procs):
            self.stop_graphflow_worker(proc)
        self.graphflow_worker_procs = []

    def handle_command(self) -> None:
        command = _read_json(command_path())
        command_id = str(command.get("id") or "")
        if not command_id or command_id == self.last_command.get("id"):
            return
        if command.get("action") != "restart_opencode":
            self.last_command = {
                "id": command_id,
                "ok": False,
                "error": f"Unknown supervisor action: {command.get('action')}",
            }
            self.publish()
            return

        self.stop_opencode()
        ok, error = self.start_opencode()
        if ok:
            # The worker inherited the previous managed port. Restart only the
            # worker; durable jobs remain queued/running and recover by lease.
            self.stop_graphflow_workers()
            self.start_graphflow_workers()
        self.last_command = {
            "id": command_id,
            "ok": ok,
            "error": error,
            "host": self.host,
            "port": self.opencode_port,
            "pid": self.opencode_proc.pid if ok and self.opencode_proc else None,
        }
        self.publish(error)

    def run(self) -> int:
        try:
            command_path().unlink(missing_ok=True)
        except OSError:
            pass
        ok, error = self.start_opencode()
        if not ok:
            print(f"[supervisor] OpenCode startup failed: {error}", file=sys.stderr)
        self.start_backend()
        if self.wait_for_backend_ready():
            self.start_graphflow_workers()
        else:
            print(
                "[supervisor] FastAPI did not become ready; GraphFlow worker "
                "will remain stopped until the backend restarts successfully.",
                file=sys.stderr,
            )
            self.stop_backend()
        self.publish(error)
        try:
            while True:
                self.handle_command()
                if self.backend_proc is None or self.backend_proc.poll() is not None:
                    print("[supervisor] FastAPI unavailable; restarting it.", file=sys.stderr)
                    time.sleep(1)
                    self.start_backend()
                    if self.wait_for_backend_ready():
                        self.start_graphflow_workers()
                    else:
                        self.stop_backend()
                dead_workers = [
                    proc for proc in self.graphflow_worker_procs
                    if proc.poll() is not None
                ]
                if dead_workers:
                    print(
                        f"[supervisor] {len(dead_workers)} GraphFlow worker(s) exited; restarting.",
                        file=sys.stderr,
                    )
                    self.graphflow_worker_procs = [
                        proc for proc in self.graphflow_worker_procs
                        if proc.poll() is None
                    ]
                    time.sleep(1)
                    if self.backend_ready:
                        self.start_graphflow_workers()
                elif self.backend_ready:
                    self.start_graphflow_workers()
                if self.opencode_proc is not None and self.opencode_proc.poll() is not None:
                    self.opencode_proc = None
                    self.publish("OpenCode exited unexpectedly; waiting for user-confirmed restart.")
                else:
                    self.publish()
                time.sleep(0.5)
        except KeyboardInterrupt:
            return 0
        finally:
            self.stopping = True
            self.publish()
            self.stop_graphflow_workers()
            self.stop_backend()
            self.stop_opencode()
            self.publish()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task Hounds runtime supervisor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    return Supervisor(args.host, args.port, args.reload).run()


if __name__ == "__main__":
    raise SystemExit(main())
