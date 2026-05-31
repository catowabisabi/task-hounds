from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib import request as urlrequest

from power_teams.db import (
    DB_PATH,
    init_db,
    register_opencode_server_instance,
    seed_default_agents,
    update_agent,
    update_opencode_server_status,
    upsert_agent_binding,
)
from power_teams.runtime.opencode_binary import find_opencode_bin


ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = Path(os.environ.get("POWER_TEAMS_RUNTIME_DIR", str(ROOT / "core" / "runtime")))
PROCESS_DIR = RUNTIME_DIR / "processes"
LOG_DIR = RUNTIME_DIR / "logs" / "opencode"
STATE_PATH = PROCESS_DIR / "opencode_servers.json"
OPENCODE_CONFIG_HOME = RUNTIME_DIR / "opencode_home" / ".config"
OPENCODE_DATA_HOME = RUNTIME_DIR / "opencode_home" / ".local" / "share"
OPENCODE_CONFIG_DIR = RUNTIME_DIR / "opencode_config"


@dataclass
class ServerSpec:
    name: str
    role: str
    agent: str
    port: int
    cwd: Path


@dataclass
class ManagedServer:
    spec: ServerSpec
    process: subprocess.Popen
    log_path: Path


class OpenCodeSupervisor:
    def __init__(
        self,
        manager_port: int | None = None,
        worker_port: int | None = None,
        host: str = "127.0.0.1",
        cwd: Path | None = None,
        opencode_bin: str | None = None,
        startup_timeout: int = 90,
    ) -> None:
        self.host = host
        self.cwd = (cwd or ROOT).resolve()
        self.opencode_bin = opencode_bin or find_opencode_bin(required=True)
        self.topology = os.environ.get("POWER_TEAMS_OPENCODE_TOPOLOGY", "shared").lower().strip()
        self.manager_port = manager_port or int(os.environ.get("POWER_TEAMS_OPENCODE_PORT", "0") or 0) or find_free_port()
        self.worker_port = worker_port or (
            self.manager_port if self.topology == "shared" else find_free_port(exclude={self.manager_port})
        )
        self.startup_timeout = startup_timeout
        self.servers: list[ManagedServer] = []
        self.instance_ids: dict[int, int] = {}
        self._stopping = False

    def start(self) -> None:
        init_db(DB_PATH)
        seed_default_agents(DB_PATH)
        PROCESS_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        OPENCODE_CONFIG_HOME.mkdir(parents=True, exist_ok=True)
        OPENCODE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        specs = self._server_specs(self.cwd)
        for spec in specs:
            if is_port_reachable(self.host, spec.port):
                self._assign_spec_to_roles(spec)
                continue
            managed = self._start_one(spec)
            self.servers.append(managed)
            self._write_state("starting")
            wait_for_health(
                self.host,
                managed.spec.port,
                process=managed.process,
                log_path=managed.log_path,
                timeout=self.startup_timeout,
            )
            instance_id = self._register_managed_server(managed)
            self._assign_server_to_roles(managed, instance_id=instance_id)

        self._write_state("running")

    def start_for_session(self, power_teams_session_id: str, project_folder: str) -> dict[str, tuple[int, int]]:
        from power_teams.db import register_opencode_server

        PROCESS_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        OPENCODE_CONFIG_HOME.mkdir(parents=True, exist_ok=True)
        OPENCODE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        specs = self._server_specs(Path(project_folder).resolve())
        ports: dict[str, tuple[int, int]] = {}
        for spec in specs:
            if is_port_reachable(self.host, spec.port):
                self._assign_spec_to_roles(spec)
                for role in self._roles_for_spec(spec):
                    ports[role] = (spec.port, 0)
                continue
            managed = self._start_one(spec)
            self.servers.append(managed)
            self._write_state("starting")
            wait_for_health(
                self.host,
                managed.spec.port,
                process=managed.process,
                log_path=managed.log_path,
                timeout=self.startup_timeout,
            )
            register_opencode_server(
                power_teams_session_id=power_teams_session_id,
                agent_role=spec.role,
                host=self.host,
                port=managed.spec.port,
                opencode_session_id=None,
                project_folder=project_folder,
                pid=managed.process.pid,
            )
            for role in self._roles_for_spec(spec):
                ports[role] = (managed.spec.port, managed.process.pid)
            instance_id = self._register_managed_server(
                managed,
                power_teams_session_id=power_teams_session_id,
                project_folder=project_folder,
            )
            self._assign_server_to_roles(managed, instance_id=instance_id)

        self._write_state("running")
        return ports

    def stop_for_session(self, power_teams_session_id: str) -> None:
        from power_teams.db import get_opencode_servers_for_session, unregister_opencode_servers_for_session

        servers = get_opencode_servers_for_session(power_teams_session_id)
        for srv in servers:
            pid = srv["pid"]
            if pid:
                try:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                    else:
                        os.kill(pid, 15)
                except (OSError, subprocess.TimeoutExpired):
                    pass

        for managed in self.servers:
            if managed.process.poll() is None:
                stop_process_tree(managed.process)
        self.servers.clear()

        unregister_opencode_servers_for_session(power_teams_session_id)
        self._write_state("stopped")

    def run_forever(self, run_seconds: int | None = None) -> int:
        atexit.register(self.stop)
        try:
            self.start()
            if self.topology == "shared":
                print(f"shared opencode server listening on http://{self.host}:{self.manager_port}")
            else:
                for managed in self.servers:
                    print(f"{managed.spec.name} opencode server listening on http://{self.host}:{managed.spec.port}")
            print("Press Ctrl+C to stop opencode servers.")
            started_at = time.monotonic()
            while run_seconds is None or time.monotonic() - started_at < run_seconds:
                for managed in self.servers:
                    code = managed.process.poll()
                    if code is not None and not self._stopping:
                        raise RuntimeError(
                            f"{managed.spec.name} opencode exited with code {code}; see {managed.log_path}"
                        )
                time.sleep(1)
            return 0
        except KeyboardInterrupt:
            print("\nStopping opencode servers...")
            return 0
        finally:
            self.stop()

    def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        for managed in reversed(self.servers):
            stop_process_tree(managed.process)
            instance_id = self.instance_ids.get(managed.process.pid)
            if instance_id:
                update_opencode_server_status(
                    instance_id,
                    "stopped",
                    stopped_at=utc_now(),
                    stop_reason="supervisor_stop",
                )
        self._write_state("stopped")

    def _start_one(self, spec: ServerSpec) -> ManagedServer:
        log_path = LOG_DIR / f"{spec.name}.log"
        log = log_path.open("a", encoding="utf-8", buffering=1)
        log.write(f"\n[{utc_now()}] starting {spec.name} on {self.host}:{spec.port}\n")

        debug_console = opencode_debug_console_enabled()
        args = build_opencode_serve_args(self.opencode_bin, spec.port, debug_console=debug_console)

        cmd_file = RUNTIME_DIR / "opencode-fire-debug-cmd.txt"
        cmd_file.parent.mkdir(parents=True, exist_ok=True)
        cmd_str = " ".join(f'"{a}"' if " " in str(a) else str(a) for a in args) if isinstance(args, list) else args
        with cmd_file.open("a", encoding="utf-8") as fh:
            fh.write(f"[{utc_now()}] supervisor:{spec.name}:{spec.port} | {cmd_str}\n")

        process = subprocess.Popen(
            args,
            cwd=str(spec.cwd),
            env=opencode_env(),
            stdout=None if debug_console else subprocess.PIPE,
            stderr=None if debug_console else subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            creationflags=opencode_serve_creation_flags(debug_console=debug_console),
        )
        if debug_console:
            log.write(f"[{utc_now()}] debug console enabled; OpenCode logs are visible in the serve shell\n")
            log.close()
        else:
            threading.Thread(
                target=pipe_output,
                args=(process, log, spec.name),
                daemon=True,
            ).start()
        return ManagedServer(spec=spec, process=process, log_path=log_path)

    def _server_specs(self, cwd: Path) -> list[ServerSpec]:
        if self.topology == "per_role":
            reviewer_port = find_free_port(exclude={self.manager_port, self.worker_port})
            chat_port = find_free_port(exclude={self.manager_port, self.worker_port, reviewer_port})
            return [
                ServerSpec("manager", "manager", "general", self.manager_port, cwd),
                ServerSpec("worker", "worker", "general", self.worker_port, cwd),
                ServerSpec("reviewer", "reviewer", "general", reviewer_port, cwd),
                ServerSpec("chat", "chat", "general", chat_port, cwd),
            ]

        # Default topology: one long-lived OpenCode server shared by every
        # Task Hounds role and project. Each project-role still gets a distinct
        # OpenCode session id, so memory is isolated at the conversation layer.
        # Keep POWER_TEAMS_OPENCODE_TOPOLOGY=per_role available for future
        # cases where dedicated servers make sense: cron monitors, direct chat
        # agents, special tool sandboxes, or other skill extensions.
        return [ServerSpec("shared", "shared", "general", self.manager_port, cwd)]

    def _roles_for_spec(self, spec: ServerSpec) -> list[str]:
        if spec.role == "shared":
            return ["manager", "worker", "reviewer", "chat"]
        return [spec.role]

    def _assign_server_to_roles(self, managed: ManagedServer, instance_id: int | None = None) -> None:
        self._assign_spec_to_roles(managed.spec, instance_id=instance_id)

    def _assign_spec_to_roles(self, spec: ServerSpec, instance_id: int | None = None) -> None:
        for role in self._roles_for_spec(spec):
            update_agent(
                role,
                host=self.host,
                port=spec.port,
                state="idle",
                task_complete=0,
            )
            upsert_agent_binding(
                role,
                server_instance_id=instance_id,
                host=self.host,
                port=spec.port,
                opencode_agent=spec.agent,
                binding_source="auto",
            )

    def _register_managed_server(
        self,
        managed: ManagedServer,
        power_teams_session_id: str = "dashboard",
        project_folder: str | None = None,
    ) -> int:
        roles = self._roles_for_spec(managed.spec)
        instance_id = register_opencode_server_instance(
            power_teams_session_id=power_teams_session_id,
            agent_role=managed.spec.role,
            host=self.host,
            port=managed.spec.port,
            owner="power_teams",
            managed=True,
            status="running",
            pid=managed.process.pid,
            cwd=str(managed.spec.cwd),
            command=f'"{self.opencode_bin}" serve --port {managed.spec.port}',
            topology=self.topology,
            roles_json=json.dumps(roles),
            agent_bindings_json=json.dumps({role: managed.spec.port for role in roles}),
            project_session_id=None if power_teams_session_id == "dashboard" else power_teams_session_id,
            started_by="power_teams",
            project_folder=project_folder or str(managed.spec.cwd),
        )
        self.instance_ids[managed.process.pid] = instance_id
        return instance_id

    def _write_state(self, status: str) -> None:
        state = {
            "status": status,
            "updated_at": utc_now(),
            "host": self.host,
            "cwd": str(self.cwd),
            "servers": [
                {
                    "name": item.spec.name,
                    "role": item.spec.role,
                    "agent": item.spec.agent,
                    "port": item.spec.port,
                    "pid": item.process.pid,
                    "running": item.process.poll() is None,
                    "log_path": str(item.log_path),
                }
                for item in self.servers
            ],
        }
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def pipe_output(process: subprocess.Popen, log, name: str) -> None:
    try:
        if not process.stdout:
            return
        for line in process.stdout:
            log.write(line)
    finally:
        log.write(f"[{utc_now()}] {name} output pipe closed\n")
        log.close()


def creation_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _runtime_setting(name: str) -> object:
    settings_path = RUNTIME_DIR / "settings.json"
    if not settings_path.exists():
        return None
    try:
        return json.loads(settings_path.read_text(encoding="utf-8")).get(name)
    except Exception:
        return None


def opencode_debug_console_enabled() -> bool:
    env_value = os.environ.get("POWER_TEAMS_OPENCODE_DEBUG_CONSOLE")
    if env_value is not None:
        return _truthy(env_value)
    return _truthy(_runtime_setting("opencode_debug_console"))


def opencode_isolated_config_enabled() -> bool:
    env_value = os.environ.get("POWER_TEAMS_OPENCODE_ISOLATED_CONFIG")
    if env_value is not None:
        return _truthy(env_value)
    return _truthy(_runtime_setting("opencode_isolated_config"))


def build_opencode_serve_args(opencode_bin: str, port: int, *, debug_console: bool = False) -> str | list[str]:
    args = [opencode_bin, "serve", "--port", str(port)]
    if debug_console:
        args += ["--print-logs", "--log-level", "DEBUG"]
    if os.name == "nt":
        if debug_console:
            command = " ".join([f'"{opencode_bin}"', *args[1:]])
            return [os.environ.get("COMSPEC", "cmd.exe"), "/k", f'"{command}"']
        return " ".join([f'"{opencode_bin}"', *args[1:]])
    return args


def opencode_serve_creation_flags(*, debug_console: bool = False) -> int:
    if os.name != "nt":
        return 0
    if debug_console:
        return getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return creation_flags()


def opencode_env() -> dict[str, str]:
    env = os.environ.copy()
    if opencode_isolated_config_enabled():
        OPENCODE_CONFIG_HOME.mkdir(parents=True, exist_ok=True)
        OPENCODE_DATA_HOME.mkdir(parents=True, exist_ok=True)
        OPENCODE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        env.pop("OPENCODE_HOME", None)
        env["XDG_CONFIG_HOME"] = str(OPENCODE_CONFIG_HOME)
        env["XDG_DATA_HOME"] = str(OPENCODE_DATA_HOME)
        env["OPENCODE_CONFIG_DIR"] = str(OPENCODE_CONFIG_DIR)
    return env


def stop_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def find_free_port(exclude: set[int] | None = None) -> int:
    exclude = exclude or set()
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in exclude:
            return port


def is_port_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_health(
    host: str,
    port: int,
    *,
    process: subprocess.Popen,
    log_path: Path,
    timeout: int = 90,
) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://{host}:{port}/global/health"
    last_error = None
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            tail = read_tail(log_path)
            raise RuntimeError(
                f"opencode server exited before becoming healthy on {url}; "
                f"exit={code}; log={log_path}\n{tail}"
            )
        try:
            with urlrequest.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(
        f"opencode server did not become healthy on {url}: {last_error}; log={log_path}"
    )


def read_tail(path: Path, max_chars: int = 4000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def status() -> dict:
    if not STATE_PATH.exists():
        return {"status": "missing", "state_path": str(STATE_PATH), "servers": []}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Supervise manager/worker opencode serve processes")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve-opencode", help="Start manager and worker opencode servers")
    serve.add_argument("--manager-port", type=int, default=None)
    serve.add_argument("--worker-port", type=int, default=None)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--cwd", type=Path, default=ROOT)
    serve.add_argument("--opencode-bin", default=None)
    serve.add_argument("--startup-timeout", type=int, default=90)
    serve.add_argument("--run-seconds", type=int, default=None)

    sub.add_parser("opencode-status", help="Show last supervisor state")

    args = parser.parse_args(argv)
    if args.command == "serve-opencode":
        supervisor = OpenCodeSupervisor(
            manager_port=args.manager_port,
            worker_port=args.worker_port,
            host=args.host,
            cwd=args.cwd,
            opencode_bin=args.opencode_bin,
            startup_timeout=args.startup_timeout,
        )
        return supervisor.run_forever(run_seconds=args.run_seconds)
    if args.command == "opencode-status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
