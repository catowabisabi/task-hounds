from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib import request as urlrequest, error as urlerror

from power_teams.db import (
    DB_PATH, connect,
    register_opencode_server_instance, update_opencode_server_status,
    list_opencode_server_instances, get_opencode_server_by_id,
    discover_external_opencode_servers,
    upsert_agent_binding, get_agent_binding, list_agent_bindings, clear_agent_binding,
    get_runtime_policy, upsert_runtime_policy,
    create_checkpoint, get_latest_checkpoint, get_checkpoint_by_id, update_checkpoint_status, archive_checkpoint,
    has_active_work, get_runtime_status_summary,
    get_active_context,
)


_AVAILABLE_AGENTS_CACHE: list[dict] = []
_AVAILABLE_AGENTS_CACHE_AT: float = 0.0

def _load_workspace_cwd() -> Path | None:
    try:
        runtime_dir = Path(__file__).resolve().parents[2] / "runtime"
        settings_path = runtime_dir / "settings.json"
        if settings_path.exists():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            ws_path = data.get("workspace_path")
            if ws_path and Path(ws_path).exists():
                return Path(ws_path)
    except Exception:
        pass
    return None

def get_cached_available_agents() -> list[dict]:
    return _AVAILABLE_AGENTS_CACHE

def discover_available_agents(host: str, port: int, timeout: float = 4.0) -> list[dict]:
    import urllib.request as urlreq
    global _AVAILABLE_AGENTS_CACHE, _AVAILABLE_AGENTS_CACHE_AT
    url = f"http://{host}:{port}/agent"
    try:
        req = urlreq.Request(url, headers={"Accept": "application/json"})
        with urlreq.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return []
            data = json.loads(resp.read().decode("utf-8"))
            agents: list[dict] = []
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        agents.append(item)
                    elif isinstance(item, str):
                        agents.append({"id": item, "name": item})
            _AVAILABLE_AGENTS_CACHE = agents
            import time as _time
            _AVAILABLE_AGENTS_CACHE_AT = _time.monotonic()
            return agents
    except Exception as e:
        print(f"[opencode_lifecycle] agent discovery failed at {url}: {e}")
        return _AVAILABLE_AGENTS_CACHE

ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = Path(os.environ.get("POWER_TEAMS_RUNTIME_DIR", str(ROOT / "core" / "runtime")))
PROCESS_DIR = RUNTIME_DIR / "processes"
LOG_DIR = RUNTIME_DIR / "logs" / "opencode"
OPENCODE_CONFIG_HOME = RUNTIME_DIR / "opencode_home" / ".config"
OPENCODE_DATA_HOME = RUNTIME_DIR / "opencode_home" / ".local" / "share"
OPENCODE_CONFIG_DIR = RUNTIME_DIR / "opencode_config"


def opencode_env() -> dict[str, str]:
    env = os.environ.copy()
    OPENCODE_CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    OPENCODE_DATA_HOME.mkdir(parents=True, exist_ok=True)
    OPENCODE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    env.pop("OPENCODE_HOME", None)
    env["XDG_CONFIG_HOME"] = str(OPENCODE_CONFIG_HOME)
    env["XDG_DATA_HOME"] = str(OPENCODE_DATA_HOME)
    env["OPENCODE_CONFIG_DIR"] = str(OPENCODE_CONFIG_DIR)
    return env


def find_opencode_bin() -> str | None:
    from power_teams.runtime.opencode_binary import find_opencode_bin as _find_opencode_bin
    return _find_opencode_bin(required=False)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_port_reachable(host: str, port: int, timeout: float = 0.1) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_opencode_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    base = f"http://{host}:{int(port)}"
    for path in ("/", "/health", "/session"):
        try:
            req = urlrequest.Request(base + path, headers={"Accept": "application/json"})
            with urlrequest.urlopen(req, timeout=timeout):
                return True
        except urlerror.HTTPError as e:
            if e.code in (200, 204, 400, 401, 403, 404, 405):
                return True
        except Exception:
            pass
    return False


def is_opencode_process_port(port: int) -> bool:
    if os.name != "nt":
        return True
    try:
        ps = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"$c=Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; "
                    "if ($c) { (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName }"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        name = (ps.stdout or "").lower()
        if "opencode" in name:
            return True
        if "wslrelay" in name:
            wsl = subprocess.run(
                [
                    "wsl",
                    "sh",
                    "-lc",
                    f"ss -ltnp 2>/dev/null | grep ':{int(port)} ' | grep -i opencode",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return "opencode" in (wsl.stdout or "").lower()
        return False
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
#  ORPHAN CLEANUP — scan system for untracked opencode serve
# ─────────────────────────────────────────────────────────────

def _list_opencode_pids_with_ports() -> list[dict]:
    """
    Return all running `opencode serve` processes with their listening ports.
    Windows-only (PowerShell).  Returns [{"pid": int, "ports": [int]}, ...].
    """
    if os.name != "nt":
        return []
    try:
        ps_out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                (
                    "Get-CimInstance Win32_Process "
                    "| Where-Object { $_.Name -ieq 'opencode.exe' -and $_.CommandLine -imatch 'serve' } "
                    "| ForEach-Object { "
                    "    $_.ProcessId "
                    "} "
                ),
            ],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if ps_out.returncode != 0 or not ps_out.stdout.strip():
            return []
        pids = []
        for line in ps_out.stdout.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    pids.append({"pid": int(line), "ports": []})
                except ValueError:
                    pass

        # For each PID, find listening ports
        for item in pids:
            port_ps = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    (
                        f"Get-NetTCPConnection -OwningProcess {item['pid']} "
                        "-State Listen -ErrorAction SilentlyContinue "
                        "| Select-Object -ExpandProperty LocalPort"
                    ),
                ],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if port_ps.returncode == 0 and port_ps.stdout.strip():
                for pline in port_ps.stdout.strip().splitlines():
                    pline = pline.strip()
                    if pline:
                        try:
                            item["ports"].append(int(pline))
                        except ValueError:
                            pass
        return pids
    except Exception:
        return []


def _kill_opencode_pid(pid: int) -> bool:
    """Kill an opencode process tree by PID.  Returns True if signal sent."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.kill(pid, 15)
        return True
    except OSError:
        return False


def cleanup_orphan_opencode_servers(
    db_path: Path = DB_PATH,
    host: str = "127.0.0.1",
    exclude_ports: set[int] | None = None,
) -> dict:
    """
    Startup orphan cleanup.

    1. Scan system for ALL `opencode serve` processes → get their ports.
    2. For ports tracked in DB with status='running':
       - If the tracked PID doesn't match any live process → mark stale, kill if truly dead.
       - If the port is reachable → update PID in DB.
    3. For ports NOT in DB (true orphans) → kill the process.
    4. Ports in `exclude_ports` are never touched.

    Returns a summary dict.
    """
    exclude = exclude_ports or set()
    tracked_ports: dict[int, dict] = {}
    for row in list_opencode_server_instances(path=db_path):
        if row["status"] in ("running", "starting"):
            port = int(row["port"])
            tracked_ports[port] = dict(row)

    live_processes = _list_opencode_pids_with_ports()
    live_port_to_pid: dict[int, int] = {}
    for proc in live_processes:
        for port in proc["ports"]:
            live_port_to_pid[port] = proc["pid"]

    result = {"killed_orphans": [], "updated_pids": [], "marked_stale": [], "kept": []}

    # ── Step A: Ports tracked in DB ────────────────────────────
    for port, entry in tracked_ports.items():
        if port in exclude:
            continue
        eid = int(entry["id"])
        tracked_pid = entry.get("pid")

        if port in live_port_to_pid:
            live_pid = live_port_to_pid[port]
            reachable = is_opencode_reachable(host, port, timeout=0.5)
            if reachable:
                # Port alive — update PID if it changed
                if tracked_pid != live_pid:
                    update_opencode_server_status(eid, "running", pid=live_pid, last_seen=utc_now(), path=db_path)
                    result["updated_pids"].append({"port": port, "old_pid": tracked_pid, "new_pid": live_pid})
                else:
                    update_opencode_server_status(eid, "running", last_seen=utc_now(), path=db_path)
                    result["kept"].append({"port": port, "pid": live_pid})
            else:
                # Port occupied but not reachable → stale, kill and update
                _kill_opencode_pid(live_pid)
                update_opencode_server_status(eid, "crashed", stopped_at=utc_now(),
                                              stop_reason="stale_unreachable", path=db_path)
                result["marked_stale"].append({"port": port, "pid": live_pid})
        else:
            # Port tracked in DB but no live process on it → stale entry
            update_opencode_server_status(eid, "crashed", stopped_at=utc_now(),
                                          stop_reason="process_gone", path=db_path)
            result["marked_stale"].append({"port": port, "pid": tracked_pid, "reason": "no_process"})

    # ── Step B: Untracked ports → orphans → kill ───────────────
    for proc in live_processes:
        for port in proc["ports"]:
            if port in exclude:
                continue
            if port in tracked_ports:
                continue  # already handled above
            # True orphan — not in DB, running on system
            _kill_opencode_pid(proc["pid"])
            result["killed_orphans"].append({"port": port, "pid": proc["pid"]})

    return result


def stop_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


class OpenCodeLifecycleManager:
    def __init__(self, db_path: Path = DB_PATH, host: str = "127.0.0.1"):
        self.db_path = db_path
        self.host = host
        self._supervisor = None

    def get_supervisor(self):
        if self._supervisor is None:
            from power_teams.runtime.opencode_supervisor import OpenCodeSupervisor
            self._supervisor = OpenCodeSupervisor(cwd=ROOT)
        return self._supervisor

    def list_managed_servers(self) -> list[dict]:
        rows = list_opencode_server_instances(owner="power_teams", path=self.db_path)
        return [dict(r) for r in rows]

    def list_external_servers(self) -> list[dict]:
        rows = list_opencode_server_instances(owner="external", path=self.db_path)
        return [dict(r) for r in rows]

    def refresh_external_servers(self) -> None:
        rows = list_opencode_server_instances(owner="external", status="running", path=self.db_path)
        for row in rows:
            if not is_opencode_reachable(row["host"], int(row["port"]), timeout=0.5):
                update_opencode_server_status(
                    row["id"],
                    "ignored",
                    stop_reason="not_opencode_process",
                    path=self.db_path,
                )

    def list_unknown_servers(self) -> list[dict]:
        rows = list_opencode_server_instances(owner="unknown", path=self.db_path)
        return [dict(r) for r in rows]

    def refresh_server_health(self, instance_id: int) -> dict:
        row = dict(get_opencode_server_by_id(instance_id, path=self.db_path))
        if not row:
            return {"error": "not_found"}
        host = row["host"]
        port = row["port"]
        pid = row["pid"]
        alive = is_port_reachable(host, port)
        now = utc_now()
        if alive:
            update_opencode_server_status(instance_id, row["status"],
                                          last_seen=now, pid=pid, path=self.db_path)
        else:
            current_status = row["status"]
            new_status = "crashed" if current_status == "running" else current_status
            update_opencode_server_status(instance_id, new_status,
                                          last_seen=now, path=self.db_path)
        return {"reachable": alive, "port": port, "host": host}

    def start_managed_server(
        self,
        port: int | None = None,
        topology: str = "shared",
        project_session_id: str | None = None,
        cwd: Path | None = None,
    ) -> dict:
        opencode_bin = find_opencode_bin()
        if not opencode_bin:
            return {"error": "opencode binary not found"}

        policy = get_runtime_policy(path=self.db_path)
        port = port or policy["default_shared_port"]

        managed_count = len(list_opencode_server_instances(owner="power_teams", status="running", path=self.db_path))
        if managed_count >= policy["max_managed_opencode_servers"]:
            return {"error": f"max_managed_opencode_servers ({policy['max_managed_opencode_servers']}) reached"}

        cwd = cwd or _load_workspace_cwd() or ROOT

        working_dir = cwd
        spec_port = port
        from power_teams.runtime.opencode_supervisor import (
            build_opencode_serve_args,
            opencode_debug_console_enabled,
            opencode_serve_creation_flags,
        )
        debug_console = opencode_debug_console_enabled()
        serve_args = build_opencode_serve_args(opencode_bin, spec_port, debug_console=debug_console)

        cmd_file = RUNTIME_DIR / "opencode-fire-debug-cmd.txt"
        cmd_file.parent.mkdir(parents=True, exist_ok=True)
        cmd_str = " ".join(f'"{a}"' if " " in str(a) else str(a) for a in serve_args) if isinstance(serve_args, list) else str(serve_args)
        with cmd_file.open("a", encoding="utf-8") as fh:
            fh.write(f"[{utc_now()}] lifecycle:{spec_port} | {cmd_str}\n")

        if is_port_reachable(self.host, spec_port):
            existing = list_opencode_server_instances(owner="power_teams", status="running", path=self.db_path)
            for ex in existing:
                if ex["port"] == spec_port:
                    return {"error": f"port {spec_port} already in use by another server"}

        instance_id = register_opencode_server_instance(
            power_teams_session_id=project_session_id or "default",
            agent_role="shared",
            host=self.host,
            port=spec_port,
            owner="power_teams",
            managed=True,
            status="starting",
            pid=None,
            cwd=str(working_dir),
            command=" ".join(str(part) for part in serve_args) if isinstance(serve_args, list) else serve_args,
            topology=topology,
            roles_json=json.dumps(["manager", "worker", "reviewer", "chat"]) if topology == "shared" else None,
            project_session_id=project_session_id,
            started_by="power_teams",
            project_folder=str(working_dir),
            path=self.db_path,
        )

        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_path = LOG_DIR / f"lifecycle-{spec_port}.log"
            log = log_path.open("a", encoding="utf-8", buffering=1)
            log.write(f"\n[{utc_now()}] starting managed lifecycle server on {self.host}:{spec_port}\n")
            proc = subprocess.Popen(
                serve_args,
                cwd=str(working_dir),
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
                threading.Thread(target=_pipe_process_output, args=(proc, log), daemon=True).start()
            update_opencode_server_status(instance_id, "running", pid=proc.pid, path=self.db_path)
            ok, error = wait_for_opencode_http(self.host, spec_port, proc, timeout=30)
            if not ok:
                update_opencode_server_status(
                    instance_id,
                    "crashed",
                    stopped_at=utc_now(),
                    stop_reason="startup_failed",
                    last_error=error,
                    last_error_at=utc_now(),
                    path=self.db_path,
                )
                return {"error": error, "id": instance_id, "pid": proc.pid, "port": spec_port}

            upsert_agent_binding("manager", server_instance_id=instance_id, host=self.host, port=spec_port, binding_source="auto", path=self.db_path)
            upsert_agent_binding("worker", server_instance_id=instance_id, host=self.host, port=spec_port, binding_source="auto", path=self.db_path)
            upsert_agent_binding("reviewer", server_instance_id=instance_id, host=self.host, port=spec_port, binding_source="auto", path=self.db_path)
            upsert_agent_binding("chat", server_instance_id=instance_id, host=self.host, port=spec_port, binding_source="auto", path=self.db_path)
            self._bind_all_roles_to_server({
                "id": instance_id,
                "host": self.host,
                "port": spec_port,
            })

            return {
                "id": instance_id,
                "pid": proc.pid,
                "port": spec_port,
                "host": self.host,
                "status": "running",
            }
        except Exception as e:
            update_opencode_server_status(instance_id, "crashed", last_error=str(e), last_error_at=utc_now(), path=self.db_path)
            return {"error": str(e)}

    def reconcile_runtime(self, *, start_if_missing: bool = False, restart_unowned: bool = False) -> dict:
        checked = []
        stopped = []
        usable = []
        rows = list_opencode_server_instances(owner="power_teams", status="running", path=self.db_path)
        for row in rows:
            item = dict(row)
            host = item.get("host") or self.host
            port = int(item.get("port") or 0)
            reachable = bool(port and is_opencode_reachable(host, port, timeout=0.75))
            item["reachable"] = reachable
            checked.append(item)
            if not reachable or restart_unowned:
                reason = "startup_reconcile_restart" if restart_unowned and reachable else "stale_no_reachable_opencode"
                self.stop_managed_server(int(item["id"]), reason=reason)
                update_opencode_server_status(
                    int(item["id"]),
                    "stopped",
                    stopped_at=utc_now(),
                    stop_reason=reason,
                    last_error=None if reachable else "OpenCode HTTP health check failed",
                    last_error_at=None if reachable else utc_now(),
                    path=self.db_path,
                )
                stopped.append({"id": item["id"], "host": host, "port": port, "reason": reason})
            else:
                update_opencode_server_status(int(item["id"]), "running", last_seen=utc_now(), path=self.db_path)
                usable.append(item)

        if not usable:
            rows = list_opencode_server_instances(owner="power_teams", status="running", path=self.db_path)
            for row in rows:
                host = row["host"] or self.host
                port = int(row["port"] or 0)
                if port and is_opencode_reachable(host, port, timeout=0.75):
                    usable.append(dict(row))

        started = None
        if not usable and start_if_missing:
            started = self.start_managed_server()
            if "error" not in started:
                fresh = get_opencode_server_by_id(int(started["id"]), path=self.db_path)
                if fresh:
                    usable.append(dict(fresh))

        selected = max(usable, key=lambda r: int(r["id"])) if usable else None
        if selected:
            self._bind_all_roles_to_server(selected)

        return {
            "checked": checked,
            "stopped": stopped,
            "started": started,
            "selected": {
                "id": selected["id"],
                "host": selected["host"],
                "port": selected["port"],
            } if selected else None,
            "usable_count": len(usable),
        }

    def _bind_all_roles_to_server(self, server: dict) -> None:
        server_id = int(server["id"])
        host = server.get("host") or self.host
        port = int(server.get("port") or self.port)
        # NEW: fetch available agents at startup (cached for subsequent sends)
        
        discovered = [] #呢一行一定要有, 如果 discovery 丟出 exception，discovered 根本沒有被定義，discovered 變成 undefined，整個 _bind_all_roles_to_server 就會 crash，導致 server 啟動時 role binding 失敗，chat agent 沒有 session，所以 session ID 拿不到。
        try:
            discovered = discover_available_agents(host, port, timeout=4.0)
            ids = [a.get("id") or a.get("name") for a in discovered[:8]]
            print(f"[opencode_lifecycle] discovered {len(discovered)} agents from {host}:{port}: {ids}{'...' if len(discovered) > 8 else ''}")
        except Exception as e:
            print(f"[opencode_lifecycle] agent discovery skipped: {e}")
        with connect(self.db_path) as db:
            for role in ("manager", "worker", "reviewer", "chat"):
                current = db.execute(
                    "SELECT opencode_agent, model FROM agent_registry WHERE name=?",
                    (role,),
                ).fetchone()
                binding = db.execute(
                    "SELECT opencode_agent, model FROM agent_runtime_bindings WHERE role=?",
                    (role,),
                ).fetchone()
                current_agent = (binding["opencode_agent"] if binding else None) or (current["opencode_agent"] if current else None) or "build"
                agent_names = {str(a.get("id") or a.get("name") or "").strip() for a in discovered} if discovered else set()
                agent_modes = {str(a.get("id") or a.get("name") or "").strip(): str(a.get("mode") or "") for a in discovered} if discovered else {}
                if current_agent and current_agent in agent_modes and agent_modes[current_agent] == "subagent":
                    current_agent = "build"
                opencode_agent = current_agent
                model = (binding["model"] if binding else None) or (current["model"] if current else None)
                db.execute(
                    """
                    UPDATE agent_registry
                       SET host=?, port=?, opencode_agent=?, model=?,
                           state=CASE WHEN state='error' THEN 'idle' ELSE state END,
                           updated_at=CURRENT_TIMESTAMP
                     WHERE name=?
                    """,
                    (host, port, opencode_agent, model, role),
                )
                existing = db.execute(
                    "SELECT id FROM agent_runtime_bindings WHERE role=?",
                    (role,),
                ).fetchone()
                if existing:
                    db.execute(
                        """
                        UPDATE agent_runtime_bindings
                           SET server_instance_id=?, host=?, port=?, opencode_agent=?,
                               model=?, binding_source='auto', updated_at=CURRENT_TIMESTAMP
                         WHERE role=?
                        """,
                        (server_id, host, port, opencode_agent, model, role),
                    )
                else:
                    db.execute(
                        """
                        INSERT INTO agent_runtime_bindings
                            (role, server_instance_id, host, port, opencode_agent, model, binding_source)
                        VALUES (?, ?, ?, ?, ?, ?, 'auto')
                        """,
                        (role, server_id, host, port, opencode_agent, model),
                    )
            db.commit()

    def stop_managed_server(self, instance_id: int, reason: str = "user_requested") -> dict:
        row = dict(get_opencode_server_by_id(instance_id, path=self.db_path))
        if not row:
            return {"error": "not_found"}
        if row["owner"] != "power_teams":
            return {"error": "not_managed"}
        pid = row["pid"]
        if pid:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    os.kill(pid, 15)
            except OSError:
                pass
        update_opencode_server_status(instance_id, "stopped", stopped_at=utc_now(), stop_reason=reason, path=self.db_path)
        return {"ok": True, "instance_id": instance_id}

    def restart_managed_server(self, instance_id: int) -> dict:
        stopped = self.stop_managed_server(instance_id, reason="restart")
        if "error" in stopped:
            return stopped
        row = dict(get_opencode_server_by_id(instance_id, path=self.db_path))
        return self.start_managed_server(
            port=row["port"],
            topology=dict(row).get("topology", "shared"),
            project_session_id=dict(row).get("project_session_id"),
            cwd=Path(dict(row).get("cwd")) if dict(row).get("cwd") else None,
        )

    def discover_external(self) -> list[dict]:
        self.refresh_external_servers()
        results = []
        candidate_ports = []
        live_servers = _list_opencode_pids_with_ports()
        live_port_to_pid = {}
        for proc in live_servers:
            for live_port in proc.get("ports", []):
                live_port_to_pid[int(live_port)] = int(proc["pid"])
                candidate_ports.append(int(live_port))
        for binding in list_agent_bindings(path=self.db_path):
            if binding["port"]:
                candidate_ports.append(int(binding["port"]))
        for server in list_opencode_server_instances(status="running", path=self.db_path):
            if server["port"]:
                candidate_ports.append(int(server["port"]))
        ports = [*candidate_ports, 4096, 18765, *range(18750, 18801)]
        for port in ports:
            if any(r["port"] == port for r in results):
                continue
            try:
                if not is_port_reachable(self.host, port, timeout=0.15):
                    continue
                if is_opencode_reachable(self.host, port, timeout=0.5):
                    results.append({
                        "host": self.host,
                        "port": port,
                        "reachable": True,
                        "pid": live_port_to_pid.get(port),
                        "source": "process_scan" if port in live_port_to_pid else "candidate_scan",
                    })
            except Exception:
                pass
        return results

    def attach_external_server(self, host: str, port: int) -> dict:
        port = int(port)
        if not is_opencode_reachable(host, port):
            return {"error": "server not reachable"}
        context = get_active_context(path=self.db_path)
        workspace_path = context.get("workspace_path") or ""
        project_session_id = context.get("project_session_id")
        existing = list_opencode_server_instances(owner="external", status="running", path=self.db_path)
        for row in existing:
            if row["host"] == host and int(row["port"]) == port:
                update_opencode_server_status(row["id"], "running", last_seen=utc_now(), path=self.db_path)
                return {"id": row["id"], "host": host, "port": port, "status": "running", "existing": True}
        instance_id = register_opencode_server_instance(
            power_teams_session_id=project_session_id or "external",
            agent_role="shared",
            host=host,
            port=port,
            owner="external",
            managed=False,
            status="running",
            cwd=workspace_path or None,
            project_session_id=project_session_id,
            started_by="user",
            project_folder=workspace_path,
            path=self.db_path,
        )
        return {"id": instance_id, "host": host, "port": port, "status": "running"}

    def stop_all_managed(self, reason: str = "user_requested", exclude_session_id: str | None = None) -> list[dict]:
        """Stop all managed Power Teams servers, optionally excluding those from a specific session.

        Args:
            reason: Reason for stopping (logged).
            exclude_session_id: If provided, servers whose project_session_id matches this will be skipped.
        """
        results = []

        # Stop all DB-tracked power_teams servers
        tracked_pids: set[int] = set()
        servers = list_opencode_server_instances(owner="power_teams", status="running", path=self.db_path)
        for srv in servers:
            srv_dict = dict(srv)
            if exclude_session_id is not None and srv_dict.get("project_session_id") == exclude_session_id:
                results.append({
                    "instance_id": srv_dict["id"],
                    "result": {"ok": False, "error": "excluded_session", "project_session_id": exclude_session_id},
                    "skipped": True,
                })
                continue
            pid = srv_dict.get("pid")
            if pid:
                tracked_pids.add(int(pid))
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
                except OSError:
                    pass
            update_opencode_server_status(
                srv_dict["id"], "stopped",
                stopped_at=utc_now(), stop_reason=reason, path=self.db_path,
            )
            results.append({
                "instance_id": srv_dict["id"],
                "result": {"ok": True},
            })

        return results

    def _find_remaining_opencode_pids(self, exclude_pids: set[int] | None = None) -> list[int]:
        """Find all opencode process PIDs that are still running, excluding given PIDs."""
        exclude_pids = exclude_pids or set()
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-Process -Name 'opencode' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode == 0 and result.stdout.strip():
                    return [
                        int(pid.strip())
                        for pid in result.stdout.strip().split("\n")
                        if pid.strip() and int(pid.strip()) not in exclude_pids
                    ]
            except Exception:
                pass
        return []

    def create_runtime_checkpoint(
        self,
        project_session_id: str | None,
        workspace_id: str | None,
        reason: str,
        notes: str | None = None,
    ) -> dict:
        agents = list_agent_bindings(path=self.db_path)
        agent_snapshot = json.dumps([dict(a) for a in agents])

        servers = list_opencode_server_instances(path=self.db_path)
        server_snapshot = json.dumps([dict(s) for s in servers])

        bindings = list_agent_bindings(path=self.db_path)
        bindings_snapshot = json.dumps([dict(b) for b in bindings])

        checkpoint_id = create_checkpoint(
            project_session_id=project_session_id,
            workspace_id=workspace_id,
            reason=reason,
            agent_registry_snapshot_json=agent_snapshot,
            opencode_servers_snapshot_json=server_snapshot,
            runtime_bindings_snapshot_json=bindings_snapshot,
            notes=notes,
            path=self.db_path,
        )
        return {"id": checkpoint_id, "project_session_id": project_session_id, "reason": reason}

    def restore_checkpoint_to_registry(self, checkpoint_id: int) -> dict:
        row = get_checkpoint_by_id(checkpoint_id, path=self.db_path)
        if not row:
            return {"error": "checkpoint not found"}
        if not row["agent_registry_snapshot_json"]:
            return {"error": "no agent snapshot in checkpoint"}
        agents = json.loads(row["agent_registry_snapshot_json"])
        restored = 0
        with connect(self.db_path) as db:
            for agent in agents:
                if "name" not in agent:
                    raise KeyError("checkpoint agent snapshot missing name; snapshot is not agent_registry data")
                identifier = agent["name"]
                cur = db.execute("""
                    UPDATE agent_registry SET
                        session_id = COALESCE(?, session_id),
                        state = COALESCE(?, state),
                        relations_json = COALESCE(?, relations_json),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE name = ? OR role = ?
                """, (
                    agent.get("session_id"),
                    agent.get("state"),
                    agent.get("relations_json"),
                    identifier,
                    identifier,
                ))
                restored += cur.rowcount
                if cur.rowcount != 1:
                    raise LookupError(f"checkpoint restore for agent {identifier} affected {cur.rowcount} rows")
            db.commit()
        return {"restored": restored, "checkpoint_id": checkpoint_id}

    def get_runtime_health(self, session_id: str | None = None) -> dict:
        summary = get_runtime_status_summary(session_id=session_id, path=self.db_path)
        all_servers = list_opencode_server_instances(status="running", path=self.db_path)
        server_health = []
        for srv in all_servers:
            alive = False
            if srv["status"] == "running":
                alive = is_port_reachable(srv["host"], srv["port"])
            server_health.append({
                "id": srv["id"],
                "owner": srv["owner"],
                "managed": srv["managed"],
                "status": srv["status"],
                "host": srv["host"],
                "port": srv["port"],
                "pid": srv["pid"],
                "reachable": alive,
                "topology": srv["topology"],
                "last_seen": srv["last_seen"],
                "last_error": srv["last_error"],
            })
        return {
            **summary,
            "servers": server_health,
        }


def creation_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _pipe_process_output(proc: subprocess.Popen, log) -> None:
    try:
        if proc.stdout:
            for line in proc.stdout:
                log.write(line)
    finally:
        log.write(f"[{utc_now()}] output pipe closed\n")
        log.close()


def wait_for_opencode_http(host: str, port: int, proc: subprocess.Popen, timeout: int = 30) -> tuple[bool, str | None]:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            return False, f"opencode exited before health check passed: exit={code}"
        if is_opencode_reachable(host, port, timeout=1.0):
            return True, None
        last_error = f"not reachable at {host}:{port}"
        time.sleep(0.5)
    return False, last_error or "health check timed out"
