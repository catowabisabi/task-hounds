import json
import os
import re
import socket
import subprocess
import sys
import time
import threading
import queue
from datetime import datetime, timezone
from urllib import request as urlrequest, error as urlerror
UTC = timezone.utc
from functools import wraps

# API Authentication
def get_api_secret_key():
    """Get API_SECRET_KEY from environment, defaulting to empty string for backward compatibility."""
    return os.environ.get("API_SECRET_KEY", "")

def require_auth(func):
    """Decorator that checks X-API-Key header against API_SECRET_KEY env var."""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        # If no API_SECRET_KEY is set, skip auth (backward compatibility)
        secret_key = get_api_secret_key()
        if secret_key:
            api_key = self.headers.get("X-API-Key", "")
            if not api_key or api_key != secret_key:
                self.send_error(401)
                return
        return func(self, *args, **kwargs)
    return wrapper

# Protected endpoints patterns
PROTECTED_GET_PATHS = (
    "/api/agents",
    "/api/sessions",
    "/api/sessions/archived",
    "/api/chat/status",
    "/api/chat/messages",
)

PROTECTED_MUTATION_PATHS = (
    "/api/sessions",
    "/api/agents",
    "/api/run-cycle",
    "/api/run-cycle/stop",
    "/api/loop/start",
    "/api/loop/stop",
    "/api/session/reset",
    "/api/opencode_send_stream",
    "/api/port_checks",
    "/api/suggestion",
    "/api/suggestions/unscoped",
    "/api/suggestion/pause",
    "/api/suggestion/release",
    "/api/suggestion/done",
    "/api/suggestion/new",
    "/api/manager-messages",
    "/api/chat/send",
    "/api/handoff",
    "/api/files/user_input",
    "/api/stream/",
    "/api/settings",
)

def is_protected_path(path, method):
    """Check if path requires authentication."""
    if method == "GET":
        return any(path.startswith(p) for p in PROTECTED_GET_PATHS)
    else:  # POST, PUT, DELETE
        return any(path.startswith(p) for p in PROTECTED_MUTATION_PATHS)

def check_api_auth(handler):
    """Check API auth for a handler instance. Returns True if authorized, False to reject."""
    secret_key = get_api_secret_key()
    if not secret_key:
        return True  # No auth configured, allow all
    api_key = handler.headers.get("X-API-Key", "")
    return bool(api_key and api_key == secret_key)

"""Legacy http.server helpers.

Task Hounds runtime entrypoints should use api.fastapi_server. This module is
kept while shared helpers are extracted for the FastAPI app.
"""

from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib import request as urlrequest

# Bug 5: 強制 UTF-8 輸出，防止 Windows cp1252 導致 UnicodeEncodeError / emoji 亂碼
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
APP = Path(__file__).resolve().parent
WEB_DIST = ROOT / "ui" / "web" / "dist"


def _load_env_files() -> None:
    env_paths = [ROOT / ".env", ROOT / "config" / ".env"]
    try:
        from dotenv import load_dotenv

        for env_path in env_paths:
            if env_path.exists():
                load_dotenv(env_path, override=False, encoding="utf-8-sig")
        return
    except Exception:
        pass

    for env_path in env_paths:
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env_files()

if WEB_DIST.exists():
    STATIC_DIR = WEB_DIST
else:
    print("[WARN] ui/web/dist/ not found — falling back to legacy UI (core/api/). Please run: cd ui/web && npm run build", flush=True)
    STATIC_DIR = APP

RUNTIME_DIR = Path(os.environ.get("POWER_TEAMS_RUNTIME_DIR", str(ROOT / "core" / "runtime")))
RUNTIME_FILES = RUNTIME_DIR / "agent_files"
DB_PATH = Path(os.environ.get("POWER_TEAMS_DB", str(ROOT / "core" / "db" / "power_teams.db")))
PYTHONPATH_ENTRIES = [*(os.environ.get("PYTHONPATH", "").split(os.pathsep) if os.environ.get("PYTHONPATH") else []), str(ROOT / "core"), str(ROOT / "backend")]
for _entry in reversed(PYTHONPATH_ENTRIES):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)
RUN_LOG = RUNTIME_DIR / "logs" / "desktop-run-cycle.log"
MANAGER_STREAM = RUNTIME_FILES / "manager_stream.txt"
WORKER_STREAM = RUNTIME_FILES / "worker_stream.txt"
MANAGER_TIMER = RUNTIME_FILES / "manager_next.txt"
WORKER_TIMER = RUNTIME_FILES / "worker_next.txt"
DEFAULT_STREAM_AGENTS = ("manager", "worker", "reviewer", "chat")
RUNTIME_FILES.mkdir(parents=True, exist_ok=True)
RUN_LOG.parent.mkdir(parents=True, exist_ok=True)

def agent_stream_path(name: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", name)
    return RUNTIME_FILES / f"{safe}_stream.txt"

def active_agent_stream_path(name: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", name)
    try:
        settings = read_settings()
        session_id = settings.get("active_project_session")
        if session_id:
            return RUNTIME_DIR / "sessions" / session_id / "agent_files" / f"{safe}_stream.txt"
    except Exception:
        pass
    return agent_stream_path(safe)

def agent_timer_path(name: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", name)
    return RUNTIME_FILES / f"{safe}_next.txt"

for f in [MANAGER_STREAM, WORKER_STREAM, MANAGER_TIMER, WORKER_TIMER]:
    if not f.exists():
        f.write_text("", encoding="utf-8")
for agent_name in DEFAULT_STREAM_AGENTS:
    for f in (agent_stream_path(agent_name), agent_timer_path(agent_name)):
        if not f.exists():
            f.write_text("", encoding="utf-8")

def read_runtime(sub_path: str) -> str:
    p = RUNTIME_DIR / sub_path
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()

def write_runtime(sub_path: str, value: str) -> None:
    p = RUNTIME_DIR / sub_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value, encoding="utf-8")

def active_runtime_file(name: str) -> Path:
    try:
        settings = read_settings()
        session_id = settings.get("active_project_session")
        if session_id:
            return RUNTIME_DIR / "sessions" / session_id / "agent_files" / name
    except Exception:
        pass
    return RUNTIME_FILES / name

def read_active_runtime_file(name: str) -> str:
    path = active_runtime_file(name)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()

def write_active_runtime_file(name: str, value: str) -> None:
    path = active_runtime_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    legacy = RUNTIME_FILES / name
    if legacy != path:
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(value, encoding="utf-8")

# ── Debug log helper: prints to stdout AND writes to file ─────────────────────
_DEBUG_LOG_FILE = ROOT / "docs" / "debug-logs" / "start-loop-trace.log"
try:
    _DEBUG_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

def debug_log(msg: str) -> None:
    """Write debug message to both stdout and the debug log file."""
    ts = datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with _DEBUG_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def append_text(path: Path, value: str) -> None:
    with path.open("a", encoding="utf-8") as h:
        h.write(value)

def get_db_agents():
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, role, host, port, model, opencode_agent, "
            "state, task_complete, current_step, step_source, current_step_started_at, "
            "last_stream_at, last_seen, last_error, session_id, "
            "COALESCE(backend_type,'opencode') as backend_type, backend_config_json "
            "FROM agent_registry ORDER BY role, name"
        ).fetchall()
        agents = [dict(r) for r in rows]
        bindings = conn.execute("SELECT * FROM agent_runtime_bindings").fetchall()
        by_role = {b["role"]: b for b in bindings}
        for agent in agents:
            binding = by_role.get(agent.get("name")) or by_role.get(agent.get("role"))
            if binding:
                agent["host"] = binding["host"] or agent["host"]
                agent["port"] = int(binding["port"] or agent["port"])
                agent["model"] = binding["model"]
                agent["opencode_agent"] = binding["opencode_agent"] or agent["opencode_agent"]
                agent["binding_source"] = binding["binding_source"]
        return agents
    except Exception as e:
        return [{"error": str(e)}]


def _db():
    """Return a db module instance (lazy import)."""
    from power_teams import db as _db_mod
    # Ensure the module uses the same DB_PATH as this server (supports POWER_TEAMS_DB env override)
    _db_mod.DB_PATH = DB_PATH
    return _db_mod


def get_handoff_data():
    session_id = get_active_project_session_id()
    try:
        if session_id == "legacy":
            return {}
        row = _db().get_latest_handoff(session_id=session_id, path=DB_PATH)
        if row is None:
            return {}
        return dict(row)
    except Exception as e:
        return {"error": str(e)}


def get_handoff_versions():
    try:
        session_id = get_active_project_session_id()
        if session_id == "legacy":
            return []
        rows = _db().list_handoff_versions(session_id=session_id, path=DB_PATH)
        return [dict(r) for r in rows]
    except Exception as e:
        return []


def get_suggestion_data():
    session_id = get_active_project_session_id()
    try:
        if session_id != "legacy":
            row = _db().get_active_suggestion(session_id=session_id, path=DB_PATH)
        else:
            row = _db().get_active_suggestion(path=DB_PATH)
        if row is None:
            return None
        data = dict(row)
        status = data.get("status") or "pending"
        queue_status = {
            "pending": "queued_for_manager",
            "released": "queued_for_worker",
            "worker_done": "manager_reviewing",
            "done": "processed",
            "paused": "paused",
        }.get(status, status)
        data["queue_status"] = queue_status
        data["status_label"] = {
            "queued_for_manager": "Queued for manager",
            "queued_for_worker": "Queued for worker",
            "manager_reviewing": "Manager reviewing",
            "processed": "Processed",
            "paused": "Paused",
        }.get(queue_status, status)
        return data
    except Exception as e:
        return {"error": str(e)}


def get_unscoped_suggestions_data():
    try:
        rows = _db().list_unscoped_active_suggestions(path=DB_PATH)
        data = []
        for row in rows:
            item = dict(row)
            item["scope_warning"] = "historical_unscoped"
            item["cleanup_only"] = True
            data.append(item)
        return data
    except Exception as e:
        return {"error": str(e)}


def get_manager_messages_data():
    session_id = get_active_project_session_id()
    try:
        if session_id != "legacy":
            rows = _db().list_manager_messages(session_id=session_id, limit=50, path=DB_PATH)
        else:
            rows = _db().list_manager_messages(limit=50, path=DB_PATH)
        settings = read_settings()
        processed = settings.get("processed_manager_message_ids", {})
        last_seen = int(processed.get(session_id, 0) or 0)
        manager_state = ""
        try:
            manager_state = (dict(_db().get_agent("manager") or {}).get("state") or "").lower()
        except Exception:
            manager_state = ""
        data = []
        for row in rows:
            item = dict(row)
            content = item.get("content") or ""
            is_human = content.startswith("Human message to manager:")
            if is_human and int(item.get("id") or 0) > last_seen:
                queue_status = "manager_processing" if manager_state == "busy" else "queued_for_manager"
            elif is_human:
                queue_status = "processed"
            else:
                queue_status = "manager_response"
            item["is_human"] = is_human
            item["queue_status"] = queue_status
            item["status_label"] = {
                "queued_for_manager": "Queued for manager",
                "manager_processing": "Manager processing",
                "processed": "Processed",
                "manager_response": "Manager response",
            }.get(queue_status, queue_status)
            data.append(item)
        return data
    except Exception as e:
        return []

def read_settings() -> dict:
    settings_path = RUNTIME_DIR / "settings.json"
    try:
        if settings_path.exists():
            return json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    return {}


def write_settings(data: dict) -> None:
    settings_path = RUNTIME_DIR / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_active_project_session_id() -> str:
    return read_settings().get("active_project_session") or "legacy"


def get_pending_human_directive() -> dict | None:
    session_id = get_active_project_session_id()
    content = read_active_runtime_file("user_input.txt").lstrip("\ufeff").strip()
    if session_id == "legacy":
        return {"id": None, "directive": content, "session_id": session_id} if content else None
    try:
        row = _db().get_latest_user_directive(session_id, status="pending", path=DB_PATH)
        if row:
            return dict(row)
        if content:
            directive_id = _db().add_user_directive(session_id, content, path=DB_PATH)
            return {"id": directive_id, "directive": content, "session_id": session_id, "status": "pending"}
        return None
    except Exception:
        return None


def get_chat_messages_data(limit: int = 50) -> list:
    import sqlite3
    session_id = get_active_project_session_id()
    try:
        _db().init_db(DB_PATH)
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, session_id, sender, content, created_at
                   FROM chat_messages
                   WHERE session_id=?
                   ORDER BY id DESC
                   LIMIT ?""",
                (session_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]
    except Exception as e:
        return [{"error": str(e)}]


def render_chat_stream_from_history(limit: int = 80) -> None:
    stream_file = active_agent_stream_path("chat")
    stream_file.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for msg in get_chat_messages_data(limit=limit):
        if msg.get("error"):
            continue
        sender = msg.get("sender") or "chat"
        content = msg.get("content") or ""
        prefix = "You" if sender == "user" else "Chat"
        lines.append(json.dumps({"t": "text", "text": f"{prefix}: {content}"}, ensure_ascii=False))
    stream_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def get_chat_runtime_status() -> dict:
    try:
        from power_teams.db import get_agent_binding
        binding = get_agent_binding("chat", path=DB_PATH)
    except Exception as exc:
        return {"enabled": False, "reason": f"binding_error: {exc}", "binding": None}
    if not binding:
        return {"enabled": False, "reason": "chat role is not bound to an OpenCode server", "binding": None}
    host = binding["host"] or "127.0.0.1"
    port = int(binding["port"] or 0)
    try:
        with socket.create_connection((host, port), timeout=1.5):
            pass
    except OSError:
        return {
            "enabled": False,
            "reason": f"chat binding is not reachable at {host}:{port}",
            "binding": dict(binding),
        }
    return {"enabled": True, "reason": "chat_binding_reachable", "binding": dict(binding)}

def is_opencode_http_reachable(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str]:
    if os.name == "nt":
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
            if "opencode" not in name:
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
                    if "opencode" not in (wsl.stdout or "").lower():
                        return False, f"WSL listener at {host}:{port} is not opencode"
                else:
                    return False, f"Listener at {host}:{port} is not an opencode process"
        except Exception:
            return False, f"Cannot verify opencode process at {host}:{port}"
    base = f"http://{host}:{int(port)}"
    for path in ("/", "/health", "/session"):
        try:
            req = urlrequest.Request(base + path, headers={"Accept": "application/json"})
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                return True, f"HTTP {resp.status} {path}"
        except urlerror.HTTPError as e:
            if e.code in (200, 204, 400, 401, 403, 404, 405):
                return True, f"HTTP {e.code} {path}"
        except Exception:
            pass
    return False, f"No OpenCode HTTP response at {base}"

def update_agent_state(name: str, state: str = None, task_complete=None, **extra_fields) -> None:
    import sqlite3
    allowed_extra = {"current_step", "step_source", "current_step_started_at", "last_stream_at", "last_error", "last_seen"}
    fields = []
    values = []
    if state is not None:
        fields.append("state = ?")
        values.append(state)
    if task_complete is not None:
        fields.append("task_complete = ?")
        values.append(int(bool(task_complete)))
    for key, value in extra_fields.items():
        if key not in allowed_extra:
            continue
        fields.append(f"{key} = ?")
        values.append(value)
    if not fields:
        return
    if "last_seen" not in extra_fields:
        fields.append("last_seen = ?")
        values.append(utc_now())
    values.append(name)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(f"UPDATE agent_registry SET {', '.join(fields)} WHERE name = ?", values)
        conn.commit()
    finally:
        conn.close()

def write_timer(path: Path, seconds: int) -> None:
    mins = seconds // 60
    secs = seconds % 60
    path.write_text(f"{mins}m {secs}s", encoding="utf-8")

_cycle_lock = threading.Lock()
_cycle_running = False
_cycle_process = None
_cycle_stop_requested = False
_loop_process = None
_dashboard_supervisor = None
_opencode_startup_timeout = 90
_opencode_enabled = True

def stream_process_output(process, stream_file: Path | None = None) -> int:
    """Write subprocess output as soon as bytes arrive, without duplicating runner streams."""
    if not process.stdout:
        return process.wait()
    try:
        while True:
            chunk = process.stdout.read(1)
            if chunk:
                if stream_file is not None:
                    append_text(stream_file, chunk)
                append_text(RUN_LOG, chunk)
                continue
            if process.poll() is not None:
                break
            time.sleep(0.05)
        tail = process.stdout.read()
        if tail:
            if stream_file is not None:
                append_text(stream_file, tail)
            append_text(RUN_LOG, tail)
    finally:
        process.wait()
    return process.returncode

def _agent_ports_reachable() -> bool:
    agents = get_db_agents()
    expected = {"manager", "worker", "reviewer", "chat"}
    by_name = {agent.get("name"): agent for agent in agents if isinstance(agent, dict)}
    if not expected.issubset(by_name):
        return False
    for name in expected:
        agent = by_name[name]
        try:
            with socket.create_connection((agent.get("host") or "127.0.0.1", int(agent.get("port") or 0)), timeout=1.5):
                pass
        except OSError:
            append_text(RUN_LOG, f"[{utc_now()}] {name} opencode port unreachable: {agent.get('host')}:{agent.get('port')}\n")
            return False
    return True


def ensure_opencode_servers() -> None:
    global _dashboard_supervisor, _opencode_enabled
    debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 7] ensure_opencode_servers() entry. _opencode_enabled={_opencode_enabled}")
    if not _opencode_enabled:
        append_text(RUN_LOG, f"[{utc_now()}] opencode disabled for this backend process\n")
        debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 7] BLOCKED: opencode disabled")
        raise RuntimeError("opencode_disabled")
    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 7] Reconciling opencode runtime...")
        reconciled = OpenCodeLifecycleManager(db_path=DB_PATH).reconcile_runtime(start_if_missing=True)
        if reconciled.get("selected"):
            append_text(RUN_LOG, f"[{utc_now()}] opencode runtime selected {reconciled['selected']}\n")
            debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 7] Runtime reconciled OK: {reconciled['selected']}")
            return
        started = reconciled.get("started") or {}
        if isinstance(started, dict) and started.get("error"):
            raise RuntimeError(started["error"])
        raise RuntimeError("opencode_runtime_unavailable")
    except Exception as exc:
        if "opencode command not found" in str(exc):
            _opencode_enabled = False
            append_text(RUN_LOG, f"[{utc_now()}] opencode disabled: {exc}\n")
            raise RuntimeError("opencode_disabled") from exc
        append_text(RUN_LOG, f"[{utc_now()}] opencode lifecycle ensure failed: {exc}\n")
        debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 7] Lifecycle ensure failed: {exc}")
        raise


def run_mvp_cycle():
    global _cycle_running, _cycle_process, _cycle_stop_requested
    with _cycle_lock:
        if _cycle_running:
            return
        _cycle_running = True
        _cycle_stop_requested = False

    def target():
        global _cycle_running, _cycle_process
        try:
            ensure_opencode_servers()
            update_agent_state("manager", state="busy", task_complete=0)
            MANAGER_STREAM.write_text("", encoding="utf-8")
            WORKER_STREAM.write_text("", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join(PYTHONPATH_ENTRIES)
            append_text(RUN_LOG, f"\n[{utc_now()}] starting runner --once\n")
            append_text(MANAGER_STREAM, f"[{utc_now()}] starting file-bridge runner --once\n")
            _cycle_process = subprocess.Popen(
                [sys.executable, "-m", "power_teams.mvp.runner", "--once"],
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            returncode = stream_process_output(_cycle_process)
            returncode = _cycle_process.returncode
            append_text(RUN_LOG, f"\n[{utc_now()}] runner --once returncode={returncode}\n")
            if returncode != 0 and not _cycle_stop_requested:
                update_agent_state("manager", state="error")
            else:
                update_agent_state("manager", state="idle")
        except Exception as exc:
            append_text(RUN_LOG, f"[{utc_now()}] error: {exc}\n")
            append_text(MANAGER_STREAM, f"\n[error] {exc}\n")
            try:
                update_agent_state("manager", state="error")
            except Exception:
                pass
        finally:
            _cycle_process = None
            _cycle_running = False

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

def stop_mvp_cycle():
    global _cycle_process, _cycle_running, _cycle_stop_requested
    _cycle_stop_requested = True
    stopped = False
    if _cycle_process and _cycle_process.poll() is None:
        _cycle_process.terminate()
        stopped = True
    _cycle_running = False
    update_agent_state("manager", state="idle")
    update_agent_state("worker", state="idle")
    append_text(MANAGER_STREAM, f"\n[{utc_now()}] file-bridge cycle stopped by user\n")
    append_text(RUN_LOG, f"[{utc_now()}] cycle stop requested stopped={stopped}\n")
    return stopped

def start_mvp_loop():
    global _loop_process
    debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6] start_mvp_loop() entry. _loop_process={_loop_process}")
    if _loop_process and _loop_process.poll() is None:
        debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6] BLOCKED: loop process already running (pid={_loop_process.pid})")
        return False
    debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6] Reading user_input.txt via read_active_runtime_file...")
    pending_directive = get_pending_human_directive()
    directive = (pending_directive or {}).get("directive", "")
    debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6] directive length={len(directive)}, preview: {directive[:80]!r}")
    if not directive:
        debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6] BLOCKED: no pending human directive detected")
        append_text(RUN_LOG, f"[{utc_now()}] loop start blocked: no pending human directive\n")
        stream_file = active_agent_stream_path("manager")
        stream_file.parent.mkdir(parents=True, exist_ok=True)
        append_text(stream_file, json.dumps({"t": "error", "msg": "Start Loop needs a pending Human Directive for this project/session."}) + "\n")
        return None
    debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6] Directive OK, clearing stream files...")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(PYTHONPATH_ENTRIES)
    
    # Clear stream files at loop start
    active_agent_stream_path("manager").write_text("", encoding="utf-8")
    active_agent_stream_path("worker").write_text("", encoding="utf-8")
    MANAGER_STREAM.write_text("", encoding="utf-8")
    WORKER_STREAM.write_text("", encoding="utf-8")
    write_active_runtime_file("work_0001_status.txt", "idle\n")
    
    debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6] Calling ensure_opencode_servers...")
    try:
        ensure_opencode_servers()
        debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6] ensure_opencode_servers() returned OK")
    except Exception as exc:
        debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6] ensure_opencode_servers() RAISED: {exc}")
        raise
    debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6] Spawning subprocess...")
    _loop_process = subprocess.Popen(
        [sys.executable, "-m", "power_teams.mvp.runner"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    
    debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6] Subprocess spawned, pid={_loop_process.pid}")
    
    # Start a thread to capture output and write to stream files
    def capture_output():
        try:
            stream_process_output(_loop_process)
        except Exception as e:
            append_text(RUN_LOG, f"[{utc_now()}] capture error: {e}\n")
    
    threading.Thread(target=capture_output, daemon=True).start()
    
    append_text(RUN_LOG, f"[{utc_now()}] auto loop started pid={_loop_process.pid}\n")
    return True

def stop_mvp_loop():
    global _loop_process
    debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6-STOP] stop_mvp_loop() called. _loop_process={_loop_process}")
    if not _loop_process or _loop_process.poll() is not None:
        debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6-STOP] No running process found, returning False")
        return False
    debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 6-STOP] Terminating process pid={_loop_process.pid}")
    _loop_process.terminate()
    append_text(RUN_LOG, f"[{utc_now()}] auto loop stopped pid={_loop_process.pid}\n")
    
    # Update agent states to idle
    update_agent_state("manager", state="idle")
    update_agent_state("worker", state="idle")
    
    return True

def loop_status():
    return {
        "running": bool(_loop_process and _loop_process.poll() is None),
        "pid": _loop_process.pid if _loop_process and _loop_process.poll() is None else None,
    }

def utc_now():
    return datetime.now(UTC).isoformat()

def fetch_json(url, timeout=4):
    req = urlrequest.Request(url, headers={"Accept": "application/json"})
    with urlrequest.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def model_options(provider_payload):
    models = []
    all_providers = provider_payload.get("all") or []
    connected = provider_payload.get("connected") or []
    connected_ids = {item if isinstance(item, str) else item.get("id") for item in connected}
    providers = [
        item for item in all_providers
        if not connected_ids or item.get("id") in connected_ids
    ]
    for provider in providers:
        pid = provider.get("id")
        for mid, model in (provider.get("models") or {}).items():
            status = model.get("status")
            if status and status != "active":
                continue
            models.append({
                "value": f"{pid}/{model.get('id') or mid}",
                "label": f"{provider.get('name') or pid} / {model.get('name') or mid}",
                "provider": pid,
                "model": model.get("id") or mid,
            })
    return models

def agent_key(value):
    return (value or "").replace("\u200b", "").strip().lower()

def resolve_opencode_agent(provider, name):
    if not name:
        return name
    try:
        agents = provider.get_config().get("agent", {})
    except Exception:
        return name
    wanted = agent_key(name)
    for actual in agents:
        if agent_key(actual) == wanted:
            return actual
    return name

def repair_mojibake(value):
    if not isinstance(value, str):
        return value
    markers = ("Ãƒ", "Ã‚", "Ã¦", "Ã¥", "Ã§", "Ã¨", "Ã©")
    if not any(marker in value for marker in markers):
        return value
    for enc in ("cp1252", "latin1"):
        try:
            fixed = value.encode(enc, errors="ignore").decode("utf-8")
        except UnicodeError:
            continue
        if fixed and fixed != value:
            return fixed
    return value

def extract_reasoning(message):
    chunks = []
    for part in message.get("parts", []):
        if part.get("type") in ("reasoning", "thinking"):
            text = part.get("reasoning") or part.get("thinking") or part.get("text") or ""
            if text:
                chunks.append(repair_mojibake(text))
    return "\n".join(chunks)

def split_answer_and_thinking(text, reasoning=""):
    text = repair_mojibake(text or "").strip()
    reasoning = repair_mojibake(reasoning or "").strip()
    if not text:
        return "", reasoning
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) < 2:
        return text, reasoning
    thinking_prefixes = ("the user", "let me", "i need", "i should", "i will", "i'll", "this is", "since the user", "we need")
    if any(prefix in paragraph.lower() for paragraph in paragraphs[:-1] for prefix in thinking_prefixes):
        merged = "\n\n".join(part for part in [reasoning, "\n\n".join(paragraphs[:-1])] if part)
        return paragraphs[-1].strip(), merged
    return text, reasoning

def extract_tools(message):
    tools = message.get("tool_calls") or []
    rows = []
    subagents = []
    for tool in tools:
        name = tool.get("tool") or "?"
        inp = tool.get("input") or {}
        if isinstance(inp, dict):
            detail = (
                inp.get("command")
                or inp.get("filePath")
                or inp.get("path")
                or inp.get("pattern")
                or inp.get("description")
                or json.dumps(inp, ensure_ascii=False)
            )
        else:
            detail = str(inp)
        output = repair_mojibake(str(tool.get("output") or ""))
        rows.append({"tool": name, "status": tool.get("status") or "", "detail": repair_mojibake(str(detail)), "output": output[:4000]})
        if name == "task":
            meta = tool.get("metadata") or {}
            match = re.search(r"\btask_id:\s*(ses_[A-Za-z0-9]+)", output)
            subagents.append({
                "agent": inp.get("subagent_type") or "?",
                "description": inp.get("description") or meta.get("title") or "",
                "session_id": meta.get("sessionId") or (match.group(1) if match else ""),
                "status": tool.get("status") or "",
            })
    return rows, subagents

def sse_event(kind, payload):
    return f"data: {json.dumps({'type': kind, **payload}, ensure_ascii=False)}\n\n".encode("utf-8")

class Handler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/"):
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._api_get()
        else:
            super().do_GET()

    def do_PUT(self):
        if self.path.startswith("/api/"):
            if not check_api_auth(self):
                self.send_error(401)
                return
        if self.path.startswith("/api/sessions/archive/"):
            self._session_archive_toggle()
        elif self.path.startswith("/api/agents/"):
            self._update_agent()
        elif self.path == "/api/files/user_input":
            self._api_put()
        elif self.path == "/api/handoff":
            self._handoff_put()
        elif self.path == "/api/suggestion":
            self._suggestion_put()
        elif self.path.startswith("/api/stream/"):
            self._stream_put()
        elif self.path.startswith("/api/workspaces/"):
            parts = self.path.split("/")
            if len(parts) == 4:
                ws_id = parts[3]
                self._workspace_put(ws_id)
            else:
                self.send_error(404)
        elif self.path.startswith("/api/project-sessions/"):
            parts = self.path.split("/")
            if len(parts) == 4:
                session_id = parts[3]
                self._project_session_delete(session_id)
            else:
                self.send_error(404)
        elif self.path == "/api/plan":
            self._plan_put()
        elif self.path == "/api/runtime/policy":
            self._runtime_policy()
        elif self.path == "/api/settings":
            self._settings_put()
        else:
            self.send_error(404)

    def do_PATCH(self):
        if self.path.startswith("/api/"):
            if not check_api_auth(self):
                self.send_error(401)
                return
        if self.path.startswith("/api/project-sessions/"):
            parts = self.path.split("/")
            if len(parts) == 4:
                self._project_session_patch(parts[3])
            else:
                self.send_error(404)
        elif self.path.startswith("/api/todos/"):
            parts = self.path.split("/")
            if len(parts) == 4:
                self._todos_patch(parts[3])
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            if not check_api_auth(self):
                self.send_error(401)
                return
            if self.path.startswith("/api/sessions/archive/"):
                parts = self.path.split("/")
                if len(parts) == 5:
                    session_key = parts[-1]
                    self._session_archive(session_key)
                else:
                    self.send_error(404)
            elif self.path.startswith("/api/workspaces/"):
                parts = self.path.split("/")
                if len(parts) == 4:
                    self._workspace_delete(parts[3])
                else:
                    self.send_error(404)
            elif self.path.startswith("/api/project-sessions/"):
                parts = self.path.split("/")
                if len(parts) == 4:
                    self._project_session_delete(parts[3])
                else:
                    self.send_error(404)
            elif self.path.startswith("/api/todos/"):
                parts = self.path.split("/")
                if len(parts) == 4:
                    self._todos_delete(parts[3])
                else:
                    self.send_error(404)
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def _session_archive(self, session_key: str):
        row = _db().list_live_sessions(path=DB_PATH)
        session = next((s for s in row if s.get("session_key") == session_key), None)
        if session:
            _db().create_session_arch(
                session_key=session_key,
                session_name=session.get("session_name", session_key),
                agent_name=session.get("agent_name"),
                folder_relation=session.get("folder_relation", ""),
                worker_status=session.get("worker_status", ""),
                token_usage=session.get("token_usage", 0),
                path=DB_PATH
            )
        self._json({"ok": True, "archived": session_key})

    def _session_archive_toggle(self):
        parts = self.path.split("/")
        if len(parts) != 5:
            self.send_error(404)
            return
        session_key = parts[-1]
        is_archived = _db().list_sessions_arch(path=DB_PATH)
        already = any(s.get("session_key") == session_key for s in is_archived)
        if already:
            self._json({"ok": True, "state": "archived", "session_key": session_key})
        else:
            self._session_archive(session_key)

    def do_POST(self):
        if self.path == "/api/health":
            self._health()
        elif self.path == "/api/settings":
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._settings_save()
        elif self.path == "/api/run-cycle":
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._run_cycle()
        elif self.path == "/api/run-cycle/stop":
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._run_cycle_stop()
        elif self.path == "/api/loop/start":
            if not check_api_auth(self):
                debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 4] Auth FAILED for /api/loop/start")
                self.send_error(401)
                return
            debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 4] Route matched /api/loop/start, auth OK, dispatching _loop_start")
            self._loop_start()
        elif self.path == "/api/loop/stop":
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._loop_stop()
        elif self.path == "/api/session/reset":
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._session_reset()
        elif self.path == "/api/opencode_send_stream":
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._opencode_send_stream()
        elif self.path == "/api/port_checks":
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._port_check()
        elif self.path in ("/api/suggestion/pause", "/api/suggestion/release",
                           "/api/suggestion/done", "/api/suggestion/new"):
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._suggestion_action()
        elif self.path == "/api/manager-messages":
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._manager_message_post()
        elif self.path == "/api/chat/send":
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._chat_send()
        elif self.path == "/api/clear-all":
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._clear_all()
        elif self.path.startswith("/api/stream/") and self.path.endswith("/clear"):
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._stream_clear()
        elif self.path.startswith("/api/agents/") and self.path.endswith("/kill"):
            if not check_api_auth(self):
                self.send_error(401)
                return
            parts = self.path.split("/")
            agent_role = parts[3] if len(parts) >= 4 else None
            self._agent_kill(agent_role)
        elif self.path.startswith("/api/agents/") and self.path.endswith("/health"):
            if not check_api_auth(self):
                self.send_error(401)
                return
            self._agent_health_check()
        elif self.path == "/api/pick-folder":
            self._pick_folder()
        elif self.path == "/api/workspaces":
            self._workspace_create()
        elif self.path.startswith("/api/workspaces/") and self.path.endswith("/activate"):
            ws_id = self.path.split("/")[3]
            self._workspace_activate(ws_id)
        elif self.path.startswith("/api/workspaces/") and self.path.endswith("/relink"):
            ws_id = self.path.split("/")[3]
            self._workspace_relink(ws_id)
        elif self.path.startswith("/api/workspaces/") and self.path.endswith("/new-session"):
            parts = self.path.split("/")
            if len(parts) == 5:
                ws_id = parts[3]
                self._workspace_new_session(ws_id)
            else:
                self.send_error(404)
        elif self.path.startswith("/api/project-sessions/") and self.path.endswith("/switch"):
            session_id = self.path.split("/")[3]
            self._project_session_switch(session_id)
        elif self.path.startswith("/api/project-sessions/"):
            parts = self.path.split("/")
            if len(parts) == 4:
                session_id = parts[3]
                self._project_session_patch(session_id)
            else:
                self.send_error(404)
        elif self.path == "/api/runtime/opencode/start":
            self._runtime_opencode_start()
        elif self.path == "/api/runtime/opencode/discover":
            self._runtime_opencode_discover()
        elif self.path == "/api/runtime/opencode/attach":
            self._runtime_opencode_attach()
        elif self.path == "/api/runtime/opencode/test":
            self._runtime_opencode_test()
        elif self.path == "/api/runtime/opencode/ignore":
            self._runtime_opencode_ignore()
        elif self.path.startswith("/api/runtime/opencode/") and self.path.count("/") >= 4:
            self._runtime_opencode_action()
        elif self.path == "/api/runtime/stop-all":
            self._runtime_stop_all()
        elif self.path == "/api/runtime/checkpoint":
            self._runtime_checkpoint()
        elif self.path.startswith("/api/runtime/checkpoints/") and len(self.path.split("/")) >= 5:
            self._runtime_checkpoint_action()
        elif self.path.startswith("/api/runtime/bindings/"):
            parts = self.path.split("/")
            role = parts[4] if len(parts) >= 5 else None
            self._runtime_binding(role)
        elif self.path == "/api/runtime/policy":
            self._runtime_policy()
        elif self.path == "/api/runtime/active-work":
            self._runtime_active_work()
        elif self.path.startswith("/api/agents/") and self.path.endswith("/clear-error"):
            parts = self.path.split("/")
            agent_role = parts[3] if len(parts) >= 4 else None
            self._agent_clear_error(agent_role)
        elif self.path.startswith("/api/agents/") and self.path.endswith("/retry"):
            parts = self.path.split("/")
            agent_role = parts[3] if len(parts) >= 4 else None
            self._agent_retry(agent_role)
        elif self.path.startswith("/api/agents/") and self.path.endswith("/mark-resolved"):
            parts = self.path.split("/")
            agent_role = parts[3] if len(parts) >= 4 else None
            self._agent_mark_resolved(agent_role)
        elif self.path == "/api/todos":
            self._todos_post()
        elif self.path == "/api/debug-logs":
            self._debug_logs()
        else:
            self.send_error(404)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _api_get(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/health":
            self._health()
            return

        if path == "/api/settings":
            self._settings_get()
            return

        if path == "/api/agents":
            data = get_db_agents()

        elif path == "/api/files/user_input":
            data = {"content": read_active_runtime_file("user_input.txt")}

        elif path == "/api/user-input/has-content":
            data = {"has_content": bool(read_active_runtime_file("user_input.txt").strip())}

        elif path == "/api/directive/status":
            directive = get_pending_human_directive()
            directive_content = directive["directive"] if directive else ""
            data = {"has_directive": bool(directive), "directive_content": directive_content}

        elif path == "/api/files/tasks":
            data = {"content": read_runtime("agent_files/tasks.md")}

        elif path == "/api/files/worker_report":
            session_id = get_active_project_session_id()
            row = _db().get_latest_worker_report(session_id, path=DB_PATH) if session_id != "legacy" else None
            data = {"content": row["report"] if row else read_active_runtime_file("worker_report.md")}

        elif path == "/api/files/manager_feedback":
            messages = get_manager_messages_data()
            content = messages[0]["content"] if messages else read_runtime("agent_files/manager_feedback.md")
            data = {"content": content}

        elif path == "/api/files/manager_msg_user":
            data = {"content": read_runtime("agent_files/manager_msg_user.md")}

        elif path == "/api/files/work_status":
            data = {"content": read_runtime("agent_files/work_0001_status.txt")}

        elif path == "/api/session_state":
            data = {"content": read_runtime("sessions/session_state.json")}

        elif path.startswith("/api/stream/"):
            name = path.split("/")[-1]
            stream_path = active_agent_stream_path(name)
            data = {"content": stream_path.read_text(encoding="utf-8") if stream_path.exists() else ""}

        elif path.startswith("/api/timer/"):
            name = path.split("/")[-1]
            data = {"content": agent_timer_path(name).read_text(encoding="utf-8") if agent_timer_path(name).exists() else ""}

        elif path == "/api/loop/status":
            data = loop_status()

        elif path == "/api/handoff":
            data = get_handoff_data()

        elif path == "/api/handoff/versions":
            data = get_handoff_versions()

        elif path == "/api/suggestion":
            data = get_suggestion_data() or {}

        elif path == "/api/suggestions/unscoped":
            data = get_unscoped_suggestions_data()

        elif path == "/api/manager-messages":
            data = get_manager_messages_data()

        elif path == "/api/chat/status":
            data = get_chat_runtime_status()

        elif path == "/api/chat/messages":
            raw_limit = (query.get("limit", ["50"])[0] or "50").strip()
            limit = int(raw_limit) if raw_limit.isdigit() else 50
            data = get_chat_messages_data(limit=limit)

        elif path == "/api/opencode_options":
            self._opencode_options(query)
            return

        elif path.startswith("/api/debug/"):
            name = path.split("/")[-1]
            data = {"content": read_runtime(f"agent_files/{name}_debug.jsonl")}

        elif path == "/api/backends":
            try:
                from power_teams.runtime.backend_registry import list_backends
                data = {"backends": list_backends()}
            except Exception as e:
                data = {"backends": ["opencode"], "error": str(e)}

        elif path == "/api/opencode/models":
            data = self._opencode_models()

        elif path == "/api/runtime/status":
            try:
                from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
                mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
                data = mgr.get_runtime_health()
            except Exception as e:
                data = {"error": str(e)}

        elif path == "/api/runtime/opencode":
            try:
                from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
                mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
                mgr.refresh_external_servers()
                servers = mgr.list_managed_servers() + mgr.list_external_servers() + mgr.list_unknown_servers()
                data = {"servers": [s for s in servers if s.get("status") == "running"]}
            except Exception as e:
                data = {"servers": [], "error": str(e)}

        elif path == "/api/runtime/opencode/discover":
            try:
                from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
                mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
                results = mgr.discover_external()
                data = {"discovered": results}
            except Exception as e:
                data = {"discovered": [], "error": str(e)}

        elif self.path.startswith("/api/runtime/opencode/") and len(self.path.split("/")) >= 5:
            parts = self.path.split("/")
            action = parts[3]
            instance_id = parts[4] if len(parts) > 4 else None
            try:
                from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
                mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
                if action == "start":
                    payload = self._read_json_body()
                    result = mgr.start_managed_server(
                        port=payload.get("port"),
                        topology=payload.get("topology", "shared"),
                        project_session_id=payload.get("project_session_id"),
                    )
                    data = result
                elif action == "stop" and instance_id:
                    result = mgr.stop_managed_server(int(instance_id))
                    data = result
                elif action == "restart" and instance_id:
                    result = mgr.restart_managed_server(int(instance_id))
                    data = result
                elif action == "attach":
                    payload = self._read_json_body()
                    result = mgr.attach_external_server(payload.get("host", "127.0.0.1"), payload.get("port", 18765))
                    data = result
                elif action == "refresh" and instance_id:
                    result = mgr.refresh_server_health(int(instance_id))
                    data = result
                else:
                    self.send_error(404)
                    return
            except Exception as e:
                data = {"error": str(e)}

        elif path == "/api/runtime/stop-all":
            try:
                from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
                mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
                raw_results = mgr.stop_all_managed()
                # Flatten to match frontend StopAllResponse interface
                results = []
                for r in raw_results:
                    result_obj = r.get("result", {})
                    results.append({
                        "server_id": str(r.get("instance_id", "")),
                        "ok": bool(result_obj.get("ok", False)),
                        "error": result_obj.get("error"),
                    })
                data = {"ok": True, "results": results}
            except Exception as e:
                data = {"error": str(e)}

        elif path == "/api/runtime/checkpoints":
            try:
                checkpoints = _db().list_checkpoints(path=DB_PATH)
                data = {"checkpoints": [dict(c) for c in checkpoints]}
            except Exception as e:
                data = {"checkpoints": [], "error": str(e)}

        elif path == "/api/runtime/checkpoint":
            try:
                payload = self._read_json_body()
                from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
                mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
                result = mgr.create_runtime_checkpoint(
                    project_session_id=payload.get("project_session_id"),
                    workspace_id=payload.get("workspace_id"),
                    reason=payload.get("reason", "manual"),
                    notes=payload.get("notes"),
                )
                data = result
            except Exception as e:
                data = {"error": str(e)}

        elif self.path.startswith("/api/runtime/checkpoints/") and len(self.path.split("/")) >= 5:
            parts = self.path.split("/")
            cp_id = parts[4]
            action = parts[5] if len(parts) > 5 else None
            try:
                if action == "resume":
                    row = _db().get_latest_checkpoint(project_session_id=None, path=DB_PATH)
                    restore_result = None
                    if row:
                        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
                        mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
                        restore_result = mgr.restore_checkpoint_to_registry(int(row["id"]))
                    data = {"checkpoint": dict(row) if row else None, "restore": restore_result}
                elif action == "archive":
                    _db().archive_checkpoint(int(cp_id), path=DB_PATH)
                    data = {"ok": True}
                else:
                    row = _db().get_opencode_server_by_id(int(cp_id), path=DB_PATH) if cp_id.isdigit() else None
                    data = {"checkpoint": dict(row) if row else None}
            except Exception as e:
                data = {"error": str(e)}

        elif path.startswith("/api/runtime/bindings/"):
            role = path.split("/")[-1]
            try:
                from power_teams.db import get_agent_binding, list_agent_bindings
                bindings = list_agent_bindings(path=DB_PATH)
                if role in ("manager", "worker", "reviewer", "chat"):
                    row = get_agent_binding(role, path=DB_PATH)
                    data = {"binding": dict(row) if row else None}
                else:
                    data = {"bindings": [dict(b) for b in bindings]}
            except Exception as e:
                data = {"error": str(e)}

        elif path == "/api/runtime/policy":
            try:
                from power_teams.db import get_runtime_policy
                policy = get_runtime_policy(path=DB_PATH)
                data = {"policy": policy}
            except Exception as e:
                data = {"error": str(e)}

        elif path == "/api/runtime/active-work":
            try:
                active, reason = _db().has_active_work(
                    session_id=get_active_project_session_id(),
                    path=DB_PATH,
                )
                data = {"active_work": active, "reason": reason}
            except Exception as e:
                data = {"active_work": False, "reason": str(e)}

        elif path == "/api/sessions":
            live = _db().list_live_sessions(path=DB_PATH)
            arch_count = _db().get_sessions_arch_count(path=DB_PATH)
            data = {"live": live, "live_count": len(live), "archived_count": arch_count}

        elif path == "/api/sessions/archived":
            sessions = _db().list_sessions_arch(path=DB_PATH)
            data = {"sessions": [dict(s) for s in sessions]}

        elif path.startswith("/api/sessions/archive/"):
            data = {"ok": True}

        elif path == "/api/workspaces":
            data = self._workspace_list_data()

        elif path.startswith("/api/workspaces/"):
            parts = self.path.split("/")
            if len(parts) >= 4:
                ws_id = parts[3]
                if len(parts) == 5 and parts[4] == "sessions":
                    data = self._workspace_sessions_data(ws_id)
                elif len(parts) == 4:
                    row = self._workspace_row(ws_id)
                    data = row if row else {"error": "not found"}
                else:
                    self.send_error(404)
                    return
            else:
                self.send_error(404)
                return

        elif path == "/api/plan":
            self._plan_get()
            return

        elif path == "/api/todos":
            self._todos_get()
            return

        elif path == "/api/settings":
            self._settings_get()
            return

        else:
            self.send_error(404)
            return

        self._json(data)

    def _api_put(self):
        if self.path != "/api/files/user_input":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(body)
        except Exception:
            self.send_error(400)
            return

        content = str(payload.get("content", "")).strip()
        write_active_runtime_file("user_input.txt", content)
        directive_id = None
        session_id = get_active_project_session_id()
        if session_id != "legacy":
            if content:
                directive_id = _db().add_user_directive(session_id, content, path=DB_PATH)
            else:
                row = _db().get_latest_user_directive(session_id, status="pending", path=DB_PATH)
                if row:
                    _db().update_user_directive_status(int(row["id"]), "cleared", path=DB_PATH)
        self._json({"ok": True, "directive_id": directive_id})

    def _update_agent(self):
        # path: /api/agents/{name}
        parts = self.path.split("/")
        if len(parts) != 4 or not parts[3]:
            self.send_error(404)
            return
        name = parts[3]

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(body)
        except Exception:
            self.send_error(400)
            return

        import sqlite3
        try:
            conn = sqlite3.connect(DB_PATH)
            
            # Build dynamic update query based on provided fields
            updates = []
            values = []
            
            if "host" in payload:
                updates.append("host = ?")
                values.append(payload["host"])
            if "port" in payload:
                updates.append("port = ?")
                values.append(int(payload["port"]))
            if "model" in payload:
                updates.append("model = ?")
                values.append(payload["model"])
            if "opencode_agent" in payload:
                updates.append("opencode_agent = ?")
                values.append(payload["opencode_agent"] or "general")
            if "state" in payload:
                updates.append("state = ?")
                values.append(payload["state"])
            if "task_complete" in payload:
                updates.append("task_complete = ?")
                values.append(int(bool(payload["task_complete"])))
            if "session_id" in payload:
                updates.append("session_id = ?")
                values.append(payload["session_id"])
            if "backend_type" in payload:
                updates.append("backend_type = ?")
                values.append(payload["backend_type"])
            if "backend_config_json" in payload:
                updates.append("backend_config_json = ?")
                values.append(payload["backend_config_json"])
            
            updates.append("updated_at = CURRENT_TIMESTAMP")
            values.append(name)
            
            if updates:
                query = f"UPDATE agent_registry SET {', '.join(updates)} WHERE name = ?"
                conn.execute(query, values)
                conn.commit()
            conn.close()
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)
            return

        session_file = ROOT / "core" / "runtime" / "sessions" / "session_state.json"
        if session_file.exists():
            try:
                sessions = json.loads(session_file.read_text(encoding="utf-8"))
                if name in sessions:
                    del sessions[name]
                    session_file.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        self._json({"ok": True})

    def _agent_health_check(self):
        # path: /api/agents/{name}/health
        parts = self.path.strip("/").split("/")
        # parts = ['api', 'agents', '{name}', 'health']
        if len(parts) != 4:
            self.send_error(404)
            return
        name = parts[2]
        agents = get_db_agents()
        row = next((a for a in agents if a.get("name") == name), None)
        if not row:
            self._json({"ok": False, "error": f"Agent '{name}' not found"}, 404)
            return
        if int(self.headers.get("Content-Length", 0) or 0):
            try:
                payload = self._read_json_body()
                row = dict(row)
                for key in ("host", "port", "model", "opencode_agent", "backend_type", "backend_config_json"):
                    if key in payload:
                        row[key] = payload[key]
            except Exception:
                pass
        try:
            from power_teams.runtime.backend_registry import get_backend
            adapter = get_backend(row)
            result = adapter.health()
            self._json(result)
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _run_cycle(self):
        if not _opencode_enabled:
            self._json({
                "ok": False,
                "error": "opencode_disabled",
                "message": "Run cycle requires OpenCode. Start backend without --no-opencode.",
            }, 503)
            return
        if not get_pending_human_directive():
            self._json({
                "ok": False,
                "error": "no_directive",
                "message": "Run Once needs a pending Human Directive for this project/session.",
            }, 409)
            return
        run_mvp_cycle()
        self._json({"ok": True, "message": "MVP cycle started"})

    def _run_cycle_stop(self):
        stopped = stop_mvp_cycle()
        self._json({"ok": True, "stopped": stopped, "message": "MVP cycle stop requested"})

    def _loop_start(self):
        debug_log("[DEBUG-LAUNCH-PAD] [STEP 5] _loop_start() called")
        try:
            started = start_mvp_loop()
            debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 5] start_mvp_loop() returned: {started!r}")
        except Exception as exc:
            debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 5] start_mvp_loop() RAISED EXCEPTION: {exc}")
            import traceback
            traceback.print_exc()
            self._json({"ok": False, "error": str(exc)}, 500)
            return
        if started is None:
            debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 5] Returning 409 no_directive. loop_status={loop_status()}")
            self._json({
                "ok": False,
                "started": False,
                "error": "no_directive",
                "message": "Start Loop needs a Human Directive for this project/session.",
                **loop_status(),
            }, 409)
            return
        debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 5] Returning 200. loop_status={loop_status()}")
        self._json({"ok": True, "started": started, **loop_status()})

    def _loop_stop(self):
        debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 5-STOP] _loop_stop() called")
        stopped = stop_mvp_loop()
        debug_log(f"[DEBUG-LAUNCH-PAD] [STEP 5-STOP] stop_mvp_loop() returned: {stopped!r}, loop_status={loop_status()}")
        self._json({"ok": True, "stopped": stopped, **loop_status()})

    def _debug_logs(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            msg = data.get("msg", "")
            source = data.get("source", "frontend")
            debug_log(f"[DEBUG-LAUNCH-PAD] [FRONTEND] [{source}] {msg}")
            self._json({"ok": True})
        except Exception as exc:
            debug_log(f"[DEBUG-LAUNCH-PAD] [FRONTEND] debug_log endpoint error: {exc}")
            self._json({"ok": False, "error": str(exc)}, 500)

    def _session_reset(self):
        try:
            stop_mvp_cycle()
            stop_mvp_loop()
            import sqlite3
            conn = sqlite3.connect(DB_PATH)
            try:
                conn.execute(
                    "UPDATE agent_registry SET session_id = NULL, state = 'idle', task_complete = 0"
                )
                conn.execute(
                    "DELETE FROM chat_messages WHERE session_id = ?",
                    (get_active_project_session_id(),),
                )
                conn.commit()
            finally:
                conn.close()
            session_file = RUNTIME_DIR / "sessions" / "session_state.json"
            session_file.parent.mkdir(parents=True, exist_ok=True)
            session_file.write_text("{}", encoding="utf-8")
            if _opencode_enabled:
                ensure_opencode_servers()
            for agent_name in DEFAULT_STREAM_AGENTS:
                message = "[system] sessions reset; next run will create fresh OpenCode sessions\n"
                stream_file = active_agent_stream_path(agent_name)
                stream_file.parent.mkdir(parents=True, exist_ok=True)
                stream_file.write_text(message, encoding="utf-8")
                legacy_stream = agent_stream_path(agent_name)
                if legacy_stream != stream_file:
                    legacy_stream.parent.mkdir(parents=True, exist_ok=True)
                    legacy_stream.write_text(message, encoding="utf-8")
            write_runtime("agent_files/work_0001_status.txt", "idle\n")
            append_text(RUN_LOG, f"[{utc_now()}] sessions reset by user\n")
            self._json({"ok": True})
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, 500)

    def _clear_all(self):
        try:
            stop_mvp_cycle()
            stop_mvp_loop()
            for agent_name in DEFAULT_STREAM_AGENTS:
                stream_file = active_agent_stream_path(agent_name)
                stream_file.parent.mkdir(parents=True, exist_ok=True)
                stream_file.write_text("", encoding="utf-8")
                legacy_stream = agent_stream_path(agent_name)
                if legacy_stream != stream_file:
                    legacy_stream.write_text("", encoding="utf-8")
            for rel in (
                "agent_files/worker_report.md",
                "agent_files/manager_feedback.md",
                "agent_files/manager_msg_user.md",
                "agent_files/tasks.md",
                "agent_files/work_0001_status.txt",
            ):
                write_runtime(rel, "idle\n" if rel.endswith("work_0001_status.txt") else "")
            session_id = get_active_project_session_id()
            with sqlite3.connect(DB_PATH) as conn:
                for table in ("suggestion_queue", "manager_messages", "session_plan", "session_todos", "project_handoff"):
                    try:
                        conn.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
                    except sqlite3.Error:
                        pass
                try:
                    conn.execute("UPDATE agent_registry SET state='idle', task_complete=0, last_error=NULL")
                except sqlite3.Error:
                    conn.execute("UPDATE agent_registry SET state='idle', task_complete=0")
                conn.commit()
            append_text(RUN_LOG, f"[{utc_now()}] clear-all session={session_id}\n")
            self._json({"ok": True, "session_id": session_id})
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, 500)

    def _stream_put(self):
        """Handle PUT /api/stream/{name} to clear stream content."""
        parts = self.path.split("/")
        if len(parts) != 4 or not parts[3]:
            self.send_error(404)
            return
        name = parts[3]
        
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(body)
        except Exception:
            self.send_error(400)
            return
        
        stream_file = active_agent_stream_path(name)
        stream_file.parent.mkdir(parents=True, exist_ok=True)
        stream_file.write_text("", encoding="utf-8")
        legacy_stream = agent_stream_path(name)
        if legacy_stream != stream_file:
            legacy_stream.write_text("", encoding="utf-8")
        self._json({"ok": True})

    def _stream_clear(self):
        parts = self.path.split("/")
        if len(parts) != 5 or not parts[3]:
            self.send_error(404)
            return
        name = parts[3]
        stream_file = active_agent_stream_path(name)
        stream_file.parent.mkdir(parents=True, exist_ok=True)
        stream_file.write_text("", encoding="utf-8")
        legacy_stream = agent_stream_path(name)
        legacy_stream.write_text("", encoding="utf-8")
        self._json({"ok": True})

    def _agent_kill(self, agent_role: str | None):
        if agent_role not in DEFAULT_STREAM_AGENTS:
            self._json({"ok": False, "error": "unknown agent"}, 404)
            return
        killed = []
        pid_paths = [
            active_agent_stream_path(agent_role).parent / f"{agent_role}_opencode.pid",
            agent_stream_path(agent_role).parent / f"{agent_role}_opencode.pid",
        ]
        for pid_path in pid_paths:
            try:
                if not pid_path.exists():
                    continue
                pid = int(pid_path.read_text(encoding="utf-8").strip() or "0")
                if pid <= 0:
                    continue
                if os.name == "nt":
                    result = subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        check=False,
                    )
                    if result.returncode != 0:
                        import logging
                        logging.warning(
                            f"taskkill failed for PID {pid}: {result.stderr.decode('utf-8', errors='replace')}"
                        )
                else:
                    os.kill(pid, 15)
                killed.append(pid)
                pid_path.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            from power_teams.db import update_agent
            update_agent(agent_role, state="idle", last_seen=utc_now())
        except Exception:
            pass
        append_text(active_agent_stream_path(agent_role), json.dumps({"t": "sys", "msg": "kill requested"}) + "\n")
        legacy_stream = agent_stream_path(agent_role)
        if legacy_stream != active_agent_stream_path(agent_role):
            append_text(legacy_stream, json.dumps({"t": "sys", "msg": "kill requested"}) + "\n")
        self._json({"ok": True, "killed": killed})

    def _opencode_options(self, query):
        host = (query.get("host", ["127.0.0.1"])[0] or "127.0.0.1").strip()
        raw_port = (query.get("port", ["4096"])[0] or "").strip()
        if not raw_port.isdigit():
            self._json({"error": "port must be a number", "agents": [], "models": []}, 400)
            return
        port = int(raw_port)
        base = f"http://{host}:{port}"
        try:
            agents_payload = fetch_json(f"{base}/agent")
            provider_payload = fetch_json(f"{base}/provider")
        except Exception as exc:
            self._json({"error": str(exc), "agents": [], "models": []}, 502)
            return

        agents = [
            {
                "value": item.get("name"),
                "label": item.get("name"),
                "mode": item.get("mode"),
                "model": item.get("model"),
            }
            for item in agents_payload
            if item.get("mode") != "subagent" and item.get("name")
        ]
        self._json({
            "agents": agents,
            "models": model_options(provider_payload),
            "approval_formats": [
                {"value": "ask", "label": "Ask interactively"},
                {"value": "once", "label": "Approve once"},
                {"value": "always", "label": "Approve always"},
                {"value": "reject", "label": "Reject"},
            ],
            "output_modes": [
                {"value": "answer", "label": "Final answer only"},
                {"value": "debug", "label": "Answer + tool summary"},
                {"value": "raw-stream", "label": "Raw stream"},
                {"value": "subagents", "label": "Show subagents"},
            ],
        })

    def _opencode_models(self):
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        try:
            mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
            servers = mgr.list_managed_servers() + mgr.list_external_servers()
            for srv in servers:
                if srv.get("status") != "running":
                    continue
                host = srv.get("host", "127.0.0.1")
                port = srv.get("port")
                if not port:
                    continue
                base = f"http://{host}:{port}"
                try:
                    provider_payload = fetch_json(f"{base}/config/providers", timeout=3)
                    models = []
                    all_providers = provider_payload.get("providers") or []
                    for provider in all_providers:
                        pid = provider.get("id")
                        provider_models = provider.get("models") or {}
                        for mid, mdata in provider_models.items():
                            status = (mdata if isinstance(mdata, dict) else {}).get("status", "active")
                            if status != "active":
                                continue
                            pname = provider.get("name") or pid
                            mname = (mdata if isinstance(mdata, dict) else {}).get("name") or mid
                            models.append({"id": f"{pid}/{mid}", "name": f"{pname} / {mname}"})
                    if models:
                        return {"models": models}
                except Exception:
                    continue
            return {"models": [], "note": "No reachable opencode servers with configured models"}
        except Exception as e:
            return {"models": [], "error": str(e)}

    def _settings_get(self):
        """GET /api/settings — return current settings.json content."""
        self._json(read_settings())

    def _settings_save(self):
        """POST /api/settings — merge incoming fields into settings.json."""
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        current = read_settings()
        current.update(payload)
        write_settings(current)
        self._json({"ok": True})

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body or "{}")

    def _port_check(self):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        raw_port = str(payload.get("port", "")).strip()
        host = str(payload.get("host", "127.0.0.1")).strip() or "127.0.0.1"
        if not raw_port.isdigit():
            self._json({"error": "port must be a number"}, 400)
            return
        port = int(raw_port)
        try:
            with socket.create_connection((host, port), timeout=1.5):
                self._json({"ok": True, "is_running": 1, "output": f"{host}:{port} is running"})
        except OSError as exc:
            self._json({"ok": True, "is_running": 0, "output": f"{host}:{port} is not reachable ({exc})"})

    def _opencode_send_stream(self):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        raw_port = str(payload.get("port", "")).strip()
        host = str(payload.get("host", "127.0.0.1")).strip() or "127.0.0.1"
        prompt = str(payload.get("prompt", "")).strip()
        extra_input = str(payload.get("extra_input", "")).strip()
        if extra_input:
            prompt = f"{prompt}\n\nExtra input:\n{extra_input}".strip()
        if not raw_port.isdigit() or not prompt:
            self.wfile.write(sse_event("error", {"message": "port and prompt are required"}))
            return

        port = int(raw_port)
        options = payload.get("options") or {}
        try:
            timeout = max(5, min(600, int(options.get("timeout") or 120)))
        except (TypeError, ValueError):
            timeout = 120
        model = str(payload.get("model", "")).strip() or None
        agent = str(payload.get("agent", "")).strip() or "general"
        events = queue.Queue()

        def worker():
            for _entry in reversed(PYTHONPATH_ENTRIES):
                if _entry not in sys.path:
                    sys.path.insert(0, _entry)
            from power_teams.integrations.opencode_provider import OpencodeServeProvider
            provider = OpencodeServeProvider(host=host, port=port, model=model, agent=agent, timeout=timeout)
            session = None
            response_text = ""
            reasoning_text = ""
            tools = []
            subagents = []
            try:
                events.put(("status", {"message": f"Creating session on {host}:{port}..."}))
                session = provider.create_session(title=prompt[:80])
                actual_agent = resolve_opencode_agent(provider, agent)
                events.put(("agent", {"agent": actual_agent.replace("\u200b", ""), "model": model or "", "session_id": session["id"]}))

                def on_delta(part_type, chunk):
                    events.put((part_type, {"text": repair_mojibake(str(chunk))}))

                raw_reply = provider.send_message(
                    session["id"],
                    prompt,
                    model=model,
                    agent=actual_agent,
                    timeout=timeout,
                    on_delta=on_delta,
                )
                response_text = repair_mojibake(provider.extract_text(raw_reply).strip())
                reasoning_text = extract_reasoning(raw_reply)
                response_text, reasoning_text = split_answer_and_thinking(response_text, reasoning_text)
                tools, subagents = extract_tools(raw_reply)
            except Exception as exc:
                events.put(("error", {"message": f"Send failed: {exc}"}))
            finally:
                if session and not options.get("keep_session"):
                    try:
                        provider.delete_session(session["id"])
                    except Exception:
                        pass
                events.put(("done", {
                    "response": response_text,
                    "reasoning": reasoning_text,
                    "tools": tools,
                    "subagents": subagents,
                    "output": f"Sent to {host}:{port}" if response_text else "Finished with no final response",
                }))
                events.put((None, None))

        threading.Thread(target=worker, daemon=True).start()
        while True:
            kind, data = events.get()
            if kind is None:
                break
            try:
                self.wfile.write(sse_event(kind, data))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break

    def _handoff_put(self):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        try:
            session_id = get_active_project_session_id()
            scoped_session_id = None if session_id == "legacy" else session_id
            version = _db().upsert_handoff(updated_by="human", path=DB_PATH, session_id=scoped_session_id, **payload)
            data = {"ok": True, "version": version, "session_id": scoped_session_id}
            if scoped_session_id is None:
                data["warning"] = "unscoped_handoff_written_no_active_project_session"
            self._json(data)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    def _suggestion_put(self):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        sid = payload.pop("id", None)
        try:
            if sid:
                _db().update_suggestion(int(sid), path=DB_PATH, **payload)
                self._json({"ok": True})
            else:
                # create new suggestion from human
                new_id = _db().create_suggestion(
                    content=payload.get("content", ""),
                    verification=payload.get("verification"),
                    related_files=payload.get("related_files"),
                    path=DB_PATH,
                )
                self._json({"ok": True, "id": new_id})
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    def _suggestion_action(self):
        """Handle pause / release / done / new via POST."""
        action = self.path.split("/")[-1]   # pause | release | done | new
        try:
            payload = self._read_json_body()
        except Exception:
            payload = {}
        try:
            db = _db()
            if action == "new":
                new_id = db.create_suggestion(
                    content=payload.get("content", ""),
                    verification=payload.get("verification"),
                    related_files=payload.get("related_files"),
                    handoff_version=payload.get("handoff_version"),
                    path=DB_PATH,
                )
                self._json({"ok": True, "id": new_id})
            else:
                sid = payload.get("id")
                row = db.get_active_suggestion(path=DB_PATH)
                target_id = int(sid) if sid else (row["id"] if row else None)
                if target_id is None:
                    self._json({"ok": False, "error": "no active suggestion"}, 404)
                    return
                status = "released" if action == "release" else action
                db.update_suggestion(target_id, status=status, path=DB_PATH)
                self._json({"ok": True, "status": status})
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    def _manager_message_post(self):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        content = payload.get("content", "").strip()
        if not content:
            self._json({"ok": False, "error": "content required"}, 400)
            return
        try:
            new_id = _db().add_manager_message(content, path=DB_PATH)
            self._json({"ok": True, "id": new_id})
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    def _chat_send(self):
        runtime_status = get_chat_runtime_status()
        if not runtime_status.get("enabled"):
            self._json({
                "ok": False,
                "error": "chat_runtime_unavailable",
                "message": runtime_status.get("reason") or "Chat requires OpenCode or a reachable chat role binding.",
            }, 503)
            return
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        content = str(payload.get("content", "")).strip()
        if not content:
            self._json({"ok": False, "error": "content is required"}, 400)
            return

        session_id = get_active_project_session_id()
        role_session_id = f"{session_id}:chat"
        try:
            _db().init_db(DB_PATH)
            from power_teams.skills.db_skill import write_operation
            from power_teams.agents.base import send_to_agent
            from power_teams.db import update_agent

            active_context = _db().get_active_context(path=DB_PATH)
            workspace_path = active_context.get("workspace_path") or ""
            if active_context.get("path_missing") or not workspace_path or not Path(workspace_path).exists():
                self._json({
                    "ok": False,
                    "error": "workspace_path_missing",
                    "message": "Chat requires an active project folder. Relink or create a project first.",
                    "workspace_path": workspace_path,
                }, 409)
                return

            binding = runtime_status.get("binding")
            if binding:
                update_agent(
                    "chat",
                    host=binding.get("host") or "127.0.0.1",
                    port=int(binding.get("port") or 18765),
                    model=binding.get("model"),
                    opencode_agent=binding.get("opencode_agent") or "general",
                )

            user_result = write_operation(
                session_id,
                "chat",
                role_session_id,
                "append_chat_message",
                {"content": content, "sender": "user"},
)
            if not user_result.get("ok"):
                self._json(user_result, 500)
                return
            render_chat_stream_from_history()

            history_messages = get_chat_messages_data(limit=50)
            history_lines = []
            for msg in history_messages:
                sender = msg.get("sender", "")
                content = msg.get("content", "")
                if sender == "user":
                    history_lines.append(f"You: {content}")
                elif sender == "chat":
                    history_lines.append(f"Chat: {content}")
            history_str = "\n".join(history_lines)
            if history_str:
                history_str = f"=== CONVERSATION HISTORY ===\n{history_str}\n=== CURRENT MESSAGE ===\n"
            else:
                history_str = "=== CURRENT MESSAGE ===\n"

            prompt = (
                "You are the Task Hounds Chat agent. You talk directly with the human "
                "about the currently active project session.\n\n"
                "Use the Task Hounds DB Skill when you need project context. Do not read "
                "the SQLite file directly. You may read project context and chat history. "
                "Only create a user directive when the human clearly asks you to turn the "
                "conversation into work for Manager/Worker.\n\n"
                f"Current project_session_id: {session_id}\n"
                f"Your role_session_id: {role_session_id}\n\n"
                f"Current workspace_path: {workspace_path}\n\n"
                "Reply conversationally and concisely. If you create or suggest a directive, "
                "tell the human exactly what you did.\n\n"
                f"Human message:\n{content}"
            )
            prompt = history_str + prompt
            reply = send_to_agent("chat", prompt, max_retries=1, cwd=workspace_path)

            bot_result = write_operation(
                session_id,
                "chat",
                role_session_id,
                "append_chat_message",
                {"content": reply, "sender": "chat"},
            )
            if not bot_result.get("ok"):
                self._json(bot_result, 500)
                return

            self._json({
                "ok": True,
                "reply": reply,
                "messages": get_chat_messages_data(limit=50),
            })
        except Exception as e:
            try:
                from power_teams.db import update_agent
                update_agent("chat", state="error", last_error=str(e)[:500])
            except Exception:
                pass
            self._json({"ok": False, "error": str(e)}, 500)

    def _workspace_list_data(self):
        from power_teams.db import connect
        settings = read_settings()
        active_ws = settings.get("active_workspace_id") or settings.get("workspace_id")
        active_session = settings.get("active_project_session") or settings.get("project_session_id")
        with connect(DB_PATH) as db:
            rows = db.execute(
                """
                SELECT * FROM project_sessions
                 WHERE workspace_id IS NOT NULL
                 ORDER BY is_active DESC, updated_at DESC, created_at DESC
                """
            ).fetchall()
        workspaces = {}
        for row in rows:
            ws_id = row["workspace_id"] or row["id"]
            if ws_id in workspaces:
                continue
            path = row["workspace_path"] or ""
            missing = bool(row["path_missing"]) or bool(path and not Path(path).exists()) or not bool(path)
            label = row["name"] or (Path(path).name if path else ws_id)
            workspaces[ws_id] = {
                "id": ws_id,
                "path": path,
                "label": label,
                "active": ws_id == active_ws or row["id"] == active_session or bool(row["is_active"]),
                "path_missing": missing,
            }
        return list(workspaces.values())

    def _workspace_sessions_data(self, ws_id: str):
        from power_teams.db import connect
        with connect(DB_PATH) as db:
            rows = db.execute(
                """
                SELECT id, workspace_id, name, is_active, created_at
                  FROM project_sessions
                 WHERE workspace_id=?
                 ORDER BY is_active DESC, updated_at DESC, created_at DESC
                """,
                (ws_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _workspace_row(self, ws_id: str):
        rows = self._workspace_list_data()
        return next((row for row in rows if row["id"] == ws_id), None)

    def _workspace_create(self):
        import uuid
        from power_teams.db import (
            connect, get_workspace_fingerprint, normalize_workspace_path,
            is_workspace_path_duplicate,
        )
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        raw_path = (payload.get("path") or "").strip()
        if not raw_path:
            self._json({"ok": False, "error": "path is required"}, 400)
            return
        path_obj = Path(raw_path)
        if not path_obj.exists() or not path_obj.is_dir():
            self._json({"ok": False, "error": "workspace_path_missing", "path": raw_path}, 400)
            return
        norm_path = normalize_workspace_path(raw_path)
        if is_workspace_path_duplicate(norm_path, path=DB_PATH):
            self._json({"ok": False, "error": "workspace_path_duplicate", "path": norm_path}, 409)
            return
        ws_id = f"ws_{uuid.uuid4().hex[:8]}"
        session_id = f"ps_{uuid.uuid4().hex[:8]}"
        label = (payload.get("label") or Path(norm_path).name or ws_id).strip()
        fp = get_workspace_fingerprint(norm_path)
        with connect(DB_PATH) as db:
            db.execute("UPDATE project_sessions SET is_active=0")
            db.execute(
                """
                INSERT INTO project_sessions
                    (id, workspace_id, name, workspace_path, path_missing, workspace_fingerprint, is_active)
                VALUES (?, ?, ?, ?, 0, ?, 1)
                """,
                (session_id, ws_id, label, norm_path, fp),
)
            db.commit()
            settings = dict(read_settings())
        settings.update({
            "active_workspace_id": ws_id,
            "active_project_session": session_id,
            "workspace_id": ws_id,
            "project_session_id": session_id,
            "workspace_path": norm_path,
        })
        self._save_settings(settings)
        self._json({"id": ws_id, "path": norm_path, "label": label, "active": True, "sessions": self._workspace_sessions_data(ws_id)})

    def _health(self):
        import subprocess
        backend_version = "dev"
        try:
            git_dir = ROOT / ".git"
            if git_dir.exists():
                result = subprocess.run(
                    ["git", "describe", "--always"],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    backend_version = result.stdout.strip()
        except Exception:
            pass
        active_context = _db().get_active_context(path=DB_PATH)
        manager_row = None
        agents = get_db_agents()
        for agent in agents:
            if isinstance(agent, dict) and agent.get("name") == "manager":
                manager_row = agent
                break
        shared_host = manager_row.get("host") if manager_row else None
        shared_port = manager_row.get("port") if manager_row else None

        try:
            from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
            lc = OpenCodeLifecycleManager(db_path=DB_PATH)
            health = lc.get_runtime_health(session_id=active_context.get("project_session_id"))
            managed_count = health.get("managed_opencode_count", 0)
            external_count = health.get("external_opencode_count", 0)
            active_work = health.get("active_work", False)
            last_checkpoint = health.get("last_checkpoint")
            role_bindings = health.get("role_bindings", [])
            runtime_policy = health.get("policy", {})
        except Exception:
            managed_count = 0
            external_count = 0
            active_work = False
            last_checkpoint = None
            role_bindings = []
            runtime_policy = {}

        self._json({
            "ok": True,
            "timestamp": utc_now(),
            "backend_version": backend_version,
            "db_path": str(DB_PATH),
            "active_workspace_id": active_context.get("workspace_id"),
            "active_project_session": active_context.get("project_session_id"),
            "shared_opencode_host": shared_host,
            "shared_opencode_port": shared_port,
            "opencode_enabled": _opencode_enabled,
            "managed_opencode_count": managed_count,
            "external_opencode_count": external_count,
            "active_work": active_work,
            "last_checkpoint": last_checkpoint,
            "role_bindings": role_bindings,
            "runtime_policy": runtime_policy,
        })

    def _check_workspace_ready(self, ws_id: str):
        from power_teams.db import connect
        with connect(DB_PATH) as db:
            row = db.execute(
                "SELECT * FROM project_sessions WHERE workspace_id=? ORDER BY is_active DESC, updated_at DESC LIMIT 1",
                (ws_id,),
            ).fetchone()
        if not row:
            return {"error": "workspace_not_found"}
        if not row["workspace_path"]:
            return {"error": "workspace_path_missing"}
        if row["path_missing"] == 1:
            return {"error": "workspace_path_missing"}
        return None

    def _pick_folder(self):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        folder_path = payload.get("path", "").strip()
        if not folder_path:
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                chosen = filedialog.askdirectory(title="Select project folder")
                root.destroy()
                folder_path = str(chosen or "").strip()
            except Exception as exc:
                self._json({"error": f"folder picker unavailable: {exc}"}, 500)
                return
            if not folder_path:
                self._json({"ok": False, "cancelled": True})
                return
        ws_path = Path(folder_path)
        if not ws_path.exists():
            self._json({"error": "path does not exist"}, 400)
            return
        self._json({"ok": True, "path": folder_path})

    def _workspace_activate(self, ws_id: str):
        from power_teams.db import connect
        with connect(DB_PATH) as db:
            row = db.execute(
                "SELECT * FROM project_sessions WHERE workspace_id=? ORDER BY is_active DESC, updated_at DESC LIMIT 1",
                (ws_id,),
            ).fetchone()
            if not row:
                self._json({"error": "workspace not found"}, 404)
                return
            db.execute("UPDATE project_sessions SET is_active=0")
            db.execute("UPDATE project_sessions SET is_active=1 WHERE id=?", (row["id"],))
            db.commit()
        new_settings = dict(read_settings())
        new_settings["active_workspace_id"] = ws_id
        new_settings["active_project_session"] = row["id"]
        new_settings["workspace_id"] = ws_id
        new_settings["workspace_path"] = row["workspace_path"] or ""
        new_settings["project_session_id"] = row["id"]
        self._save_settings(new_settings)
        self._json({"ok": True, "workspace_id": ws_id, "sessions": self._workspace_sessions_data(ws_id)})

    def _workspace_relink(self, ws_id: str):
        from power_teams.db import (
            connect, normalize_workspace_path, get_workspace_fingerprint,
            is_workspace_path_duplicate, check_fingerprint_mismatch,
        )
        with connect(DB_PATH) as db:
            row = db.execute(
                "SELECT * FROM project_sessions WHERE workspace_id=? ORDER BY is_active DESC, updated_at DESC LIMIT 1",
                (ws_id,),
            ).fetchone()
            if not row:
                self._json({"error": "workspace not found"}, 404)
                return
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        new_path = payload.get("path", "").strip()
        if not new_path:
            self._json({"error": "new path is required"}, 400)
            return
        wp = Path(new_path)
        if not wp.exists():
            self._json({"error": "path does not exist"}, 400)
            return
        norm_path = normalize_workspace_path(new_path)
        with connect(DB_PATH) as db:
            duplicate = db.execute(
                "SELECT 1 FROM project_sessions WHERE workspace_path=? AND workspace_id != ? LIMIT 1",
                (norm_path, ws_id),
            ).fetchone()
        if duplicate:
            self._json({"error": "another workspace already uses this path"}, 409)
            return
        old_fp = row["workspace_fingerprint"]
        new_fp = get_workspace_fingerprint(new_path)
        mismatch, mismatch_msg = False, ""
        if old_fp and new_fp:
            mismatch, mismatch_msg = check_fingerprint_mismatch(ws_id, new_path, path=DB_PATH)
            if mismatch:
                self._json({"error": f"fingerprint_mismatch: {mismatch_msg}"}, 409)
                return
        session_id = row["id"]
        with connect(DB_PATH) as db:
            db.execute(
                "UPDATE project_sessions SET workspace_path=?, path_missing=0, workspace_fingerprint=?, updated_at=CURRENT_TIMESTAMP WHERE workspace_id=?",
                (norm_path, new_fp, ws_id),
            )
            db.execute(
                "UPDATE project_handoff SET project_folder_location=? WHERE session_id=?",
                (norm_path, session_id),
            )
            db.commit()
        settings = dict(read_settings())
        if settings.get("active_workspace_id") == ws_id or settings.get("workspace_id") == ws_id:
            settings["workspace_path"] = norm_path
            self._save_settings(settings)
        self._json({"ok": True, "workspace_id": ws_id, "workspace_path": norm_path, "workspace_fingerprint": new_fp})

    def _save_settings(self, settings: dict) -> None:
        settings_file = RUNTIME_DIR / "settings.json"
        settings_file.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def _workspace_new_session(self, ws_id: str):
        import uuid
        from power_teams.db import get_workspace_fingerprint, connect
        with connect(DB_PATH) as db:
            row = db.execute(
                "SELECT * FROM project_sessions WHERE workspace_id=? ORDER BY is_active DESC, updated_at DESC LIMIT 1",
                (ws_id,),
            ).fetchone()
            if not row:
                self._json({"error": "workspace not found"}, 404)
                return
        ws_path = row["workspace_path"]
        if not ws_path:
            self._json({"error": "workspace has no path set, use relink first"}, 400)
            return
        fp = get_workspace_fingerprint(ws_path)
        session_id = f"ps_{uuid.uuid4().hex[:8]}"
        with connect(DB_PATH) as db:
            db.execute(
                "INSERT INTO project_sessions (id, workspace_id, name, workspace_path, path_missing, workspace_fingerprint, is_active) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, ws_id, f"Session {session_id[:8]}", ws_path, 0, fp, 1),
            )
            db.execute("UPDATE project_sessions SET is_active=0 WHERE id != ? AND workspace_id=?", (session_id, ws_id))
            db.commit()
        new_settings = dict(read_settings())
        new_settings["active_workspace_id"] = ws_id
        new_settings["active_project_session"] = session_id
        new_settings["workspace_id"] = ws_id
        new_settings["workspace_path"] = ws_path
        new_settings["project_session_id"] = session_id
        self._save_settings(new_settings)
        self._json({
            "ok": True,
            "session_id": session_id,
            "workspace_id": ws_id,
            "workspace_path": ws_path,
            "sessions": self._workspace_sessions_data(ws_id),
        })

    def _project_session_switch(self, session_id: str):
        from power_teams.db import get_project_session, connect
        row = get_project_session(session_id, path=DB_PATH)
        if not row:
            self._json({"error": "session not found"}, 404)
            return
        ws_id = row["workspace_id"]
        ws_path = row["workspace_path"] or ""
        with connect(DB_PATH) as db:
            db.execute("UPDATE project_sessions SET is_active=0 WHERE workspace_id=?", (ws_id,))
            db.execute("UPDATE project_sessions SET is_active=1 WHERE id=?", (session_id,))
            db.commit()
        new_settings = dict(read_settings())
        new_settings["active_workspace_id"] = ws_id
        new_settings["active_project_session"] = session_id
        new_settings["workspace_id"] = ws_id
        new_settings["workspace_path"] = ws_path
        new_settings["project_session_id"] = session_id
        self._save_settings(new_settings)
        if row["path_missing"] == 1 or not ws_path:
            self._json({"ok": True, "session_id": session_id, "workspace_id": ws_id, "warning": "workspace_path_missing"})
        else:
            self._json({"ok": True, "session_id": session_id, "workspace_id": ws_id})

    def _runtime_opencode_start(self):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        try:
            from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
            mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
            result = mgr.start_managed_server(
                port=payload.get("port"),
                topology=payload.get("topology", "shared"),
                project_session_id=payload.get("project_session_id"),
            )
            if "error" in result:
                self._json(result, 400)
            else:
                self._json({"ok": True, **result})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _runtime_opencode_test(self):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        host = payload.get("host", "127.0.0.1")
        port = int(payload.get("port", 18765))
        is_running, message = is_opencode_http_reachable(host, port)
        self._json({"ok": True, "is_running": is_running, "message": message})

    def _runtime_opencode_ignore(self):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        host = payload.get("host", "127.0.0.1")
        port = int(payload.get("port", 18765))
        import logging
        logging.info(f"Ignored external opencode server: {host}:{port}")
        self._json({"ok": True, "message": f"Server {host}:{port} ignored"})

    def _runtime_opencode_discover(self):
        try:
            from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
            mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
            results = mgr.discover_external()
            self._json({"ok": True, "discovered": results})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _runtime_opencode_attach(self):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        try:
            from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
            mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
            result = mgr.attach_external_server(
                payload.get("host", "127.0.0.1"),
                payload.get("port", 18765),
            )
            if "error" in result:
                self._json(result, 400)
            else:
                self._json({"ok": True, **result})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _runtime_stop_all(self):
        try:
            from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
            mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
            raw_results = mgr.stop_all_managed()
            # Flatten to match frontend StopAllResponse interface
            results = []
            for r in raw_results:
                result_obj = r.get("result", {})
                results.append({
                    "server_id": str(r.get("instance_id", "")),
                    "ok": bool(result_obj.get("ok", False)),
                    "error": result_obj.get("error"),
                })
            self._json({"ok": True, "results": results})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _runtime_checkpoint(self):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        try:
            from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
            mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
            result = mgr.create_runtime_checkpoint(
                project_session_id=payload.get("project_session_id"),
                workspace_id=payload.get("workspace_id"),
                reason=payload.get("reason", "manual"),
                notes=payload.get("notes"),
            )
            self._json({"ok": True, **result})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _runtime_checkpoint_action(self):
        parts = self.path.split("/")
        if len(parts) < 5:
            self.send_error(404)
            return
        cp_id = parts[4]
        action = parts[5] if len(parts) > 5 else None
        try:
            if action == "resume":
                row = _db().get_latest_checkpoint(path=DB_PATH)
                self._json({"checkpoint": dict(row) if row else None})
            elif action == "archive":
                _db().archive_checkpoint(int(cp_id), path=DB_PATH)
                self._json({"ok": True})
            else:
                if cp_id.isdigit():
                    row = _db().get_opencode_server_by_id(int(cp_id), path=DB_PATH)
                else:
                    row = None
                self._json({"checkpoint": dict(row) if row else None})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _runtime_binding(self, role: str | None):
        try:
            from power_teams.db import (
                get_agent_binding,
                list_agent_bindings,
                upsert_agent_binding,
            )
            if role in ("manager", "worker", "reviewer", "chat"):
                if self.command in ("POST", "PUT"):
                    payload = self._read_json_body()
                    host = payload.get("host", "127.0.0.1")
                    port = int(payload.get("port", 18765))
                    opencode_agent = payload.get("opencode_agent", "general")
                    model = payload.get("model")
                    upsert_agent_binding(
                        role,
                        server_instance_id=payload.get("server_instance_id"),
                        host=host,
                        port=port,
                        opencode_agent=opencode_agent,
                        model=model,
                        binding_source=payload.get("binding_source", "user"),
                        path=DB_PATH,
                    )
                    from power_teams.db import update_agent
                    update_agent(role, host=host, port=port, opencode_agent=opencode_agent, model=model)
                row = get_agent_binding(role, path=DB_PATH)
                self._json({"binding": dict(row) if row else None})
            else:
                bindings = list_agent_bindings(path=DB_PATH)
                self._json({"bindings": [dict(b) for b in bindings]})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _runtime_policy(self):
        from power_teams.db import get_runtime_policy, upsert_runtime_policy
        try:
            method = self.command
            if method == "PUT":
                payload = self._read_json_body()
                upsert_runtime_policy(
                    name=payload.get("name", "default"),
                    close_behavior=payload.get("close_behavior", "ask"),
                    background_mode_enabled=bool(payload.get("background_mode_enabled", False)),
                    on_backend_exit=payload.get("on_backend_exit", "stop_managed_opencode"),
                    on_backend_crash_recovery=payload.get("on_backend_crash_recovery", "ask"),
                    on_opencode_crash=payload.get("on_opencode_crash", "mark_error"),
                    max_managed_opencode_servers=int(payload.get("max_managed_opencode_servers", 1)),
                    default_topology=payload.get("default_topology", "shared"),
                    default_shared_port=int(payload.get("default_shared_port", 18765)),
                    allow_external_attach=bool(payload.get("allow_external_attach", True)),
                    allow_unknown_attach=bool(payload.get("allow_unknown_attach", False)),
                    path=DB_PATH,
                )
                self._json({"ok": True})
            else:
                policy = get_runtime_policy(path=DB_PATH)
                self._json({"policy": policy})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _runtime_stop_all(self):
        try:
            from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
            mgr = OpenCodeLifecycleManager(db_path=DB_PATH)
            raw_results = mgr.stop_all_managed()
            # Flatten to match frontend StopAllResponse interface
            results = []
            for r in raw_results:
                result_obj = r.get("result", {})
                results.append({
                    "server_id": str(r.get("instance_id", "")),
                    "ok": bool(result_obj.get("ok", False)),
                    "error": result_obj.get("error"),
                })
            self._json({"ok": True, "results": results})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _runtime_active_work(self):
        try:
            active, reason = _db().has_active_work(
                session_id=get_active_project_session_id(),
                path=DB_PATH,
            )
            self._json({"active_work": active, "reason": reason})
        except Exception as e:
            self._json({"active_work": False, "reason": str(e)})

    def _agent_clear_error(self, agent_role: str | None):
        if not agent_role:
            self.send_error(404)
            return
        try:
            from power_teams.db import update_agent, get_agent
            row = get_agent(agent_role, path=DB_PATH)
            if row and row["state"] == "error":
                update_agent(agent_role, state="idle", last_error=None, last_seen=utc_now())
            else:
                update_agent(agent_role, last_error=None, last_seen=utc_now())
            self._json({"ok": True, "role": agent_role})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _agent_retry(self, agent_role: str | None):
        if not agent_role:
            self.send_error(404)
            return
        try:
            from power_teams.db import update_agent
            update_agent(agent_role, state="idle", last_error=None, task_complete=0)
            self._json({"ok": True, "role": agent_role})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _agent_mark_resolved(self, agent_role: str | None):
        if not agent_role:
            self.send_error(404)
            return
        try:
            from power_teams.db import update_agent, get_agent
            row = get_agent(agent_role, path=DB_PATH)
            if row and row["state"] == "error":
                update_agent(agent_role, state="idle", last_error=None)
            else:
                update_agent(agent_role, last_error=None)
            self._json({"ok": True, "role": agent_role})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _project_session_patch(self, session_id: str):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        name = (payload.get("name") or "").strip()
        if name:
            from power_teams.db import update_project_session
            update_project_session(session_id, path=DB_PATH, name=name)
        self._json({"ok": True, "session_id": session_id, "updated": True, "name": name})

    def _workspace_put(self, ws_id: str):
        try:
            payload = self._read_json_body()
        except Exception:
            self.send_error(400)
            return
        label = payload.get("label", "").strip()
        if label:
            from power_teams.db import connect
            with connect(DB_PATH) as db:
                db.execute(
                    "UPDATE project_sessions SET name=?, updated_at=CURRENT_TIMESTAMP WHERE workspace_id=?",
                    (label, ws_id),
                )
                db.commit()
        self._json({"ok": True, "workspace_id": ws_id, "label": label})

    def _project_session_delete(self, session_id: str):
        from power_teams.db import connect
        with connect(DB_PATH) as db:
            row = db.execute("SELECT workspace_id FROM project_sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                self._json({"error": "session not found"}, 404)
                return
            db.execute("DELETE FROM project_sessions WHERE id=?", (session_id,))
            db.commit()
        self._json({"ok": True, "session_id": session_id})

    def _workspace_delete(self, ws_id: str):
        from power_teams.db import connect
        with connect(DB_PATH) as db:
            db.execute("DELETE FROM project_sessions WHERE workspace_id=?", (ws_id,))
            db.commit()
        settings = dict(read_settings())
        if settings.get("active_workspace_id") == ws_id or settings.get("workspace_id") == ws_id:
            for key in ("active_workspace_id", "active_project_session", "workspace_id", "project_session_id", "workspace_path"):
                settings.pop(key, None)
            self._save_settings(settings)
        self._json({"ok": True, "workspace_id": ws_id})

    def _save_settings(self, settings: dict) -> None:
        settings_path = RUNTIME_DIR / "settings.json"
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            append_text(RUN_LOG, f"[{utc_now()}] _save_settings error: {exc}\n")

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        return json.loads(body)

    # --- Plan endpoints ---

    def _plan_get(self):
        import sqlite3
        session_id = get_active_project_session_id()
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT content, updated_by, updated_at FROM session_plan WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if row:
                    self._json({"content": row[0], "updated_by": row[1], "updated_at": row[2], "session_id": session_id})
                else:
                    self._json({"content": "", "updated_by": None, "updated_at": None, "session_id": session_id})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _plan_put(self):
        import sqlite3
        session_id = get_active_project_session_id()
        try:
            payload = self._read_json_body()
            content = payload.get("content", "")
            updated_by = payload.get("updated_by", "user")
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    """INSERT INTO session_plan (session_id, content, updated_by, updated_at)
                       VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(session_id) DO UPDATE SET
                         content=excluded.content, updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP""",
                    (session_id, content, updated_by),
                )
                conn.commit()
            self._json({"ok": True})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # --- Todos endpoints ---

    def _todos_get(self):
        import sqlite3
        session_id = get_active_project_session_id()
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT id, session_id, parent_id, content, status, priority, position, owner, updated_at
                       FROM session_todos WHERE session_id=? ORDER BY position""",
                    (session_id,),
                ).fetchall()
                self._json([dict(r) for r in rows])
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _todos_post(self):
        import sqlite3, uuid
        session_id = get_active_project_session_id()
        try:
            payload = self._read_json_body()
            content = payload.get("content", "").strip()
            if not content:
                self._json({"error": "content is required"}, 400)
                return
            parent_id = payload.get("parent_id") or None
            status = payload.get("status", "pending")
            priority = payload.get("priority", "medium")
            owner = payload.get("owner", "user")
            # Get max position for parent or top-level
            with sqlite3.connect(DB_PATH) as conn:
                if parent_id:
                    max_pos = conn.execute(
                        "SELECT COALESCE(MAX(position), -1) FROM session_todos WHERE session_id=? AND parent_id=?",
                        (session_id, parent_id),
                    ).fetchone()[0]
                else:
                    max_pos = conn.execute(
                        "SELECT COALESCE(MAX(position), -1) FROM session_todos WHERE session_id=? AND parent_id IS NULL",
                        (session_id,),
                    ).fetchone()[0]
                new_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO session_todos
                         (id, session_id, parent_id, content, status, priority, position, owner, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (new_id, session_id, parent_id, content, status, priority, max_pos + 1, owner),
                )
                conn.commit()
            self._json({"ok": True, "id": new_id})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _todos_patch(self, todo_id: str):
        import sqlite3
        try:
            payload = self._read_json_body()
            with sqlite3.connect(DB_PATH) as conn:
                updates = []
                values = []
                for key in ("status", "content", "priority", "position", "parent_id"):
                    if key in payload:
                        updates.append(f"{key}=?")
                        values.append(payload[key])
                if updates:
                    updates.append("updated_at=CURRENT_TIMESTAMP")
                    values.append(todo_id)
                    query = f"UPDATE session_todos SET {', '.join(updates)} WHERE id=?"
                    conn.execute(query, values)
                    conn.commit()
            self._json({"ok": True})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _todos_delete(self, todo_id: str):
        import sqlite3
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM session_todos WHERE id=? OR parent_id=?", (todo_id, todo_id))
                conn.commit()
            self._json({"ok": True})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # --- Settings endpoint ---

    def _settings_get(self):
        settings = dict(read_settings())
        self._json(settings)

    def _settings_put(self):
        try:
            payload = self._read_json_body()
            settings = dict(read_settings())
            settings.update(payload)
            self._save_settings(settings)
            self._json({"ok": True})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        pass


def main(
    port=8765,
    start_opencode=True,
    manager_port=None,
    worker_port=None,
    startup_timeout=90,
    run_seconds=None,
    auto_loop=False,
):
    global _dashboard_supervisor, _opencode_startup_timeout, _opencode_enabled
    _opencode_startup_timeout = startup_timeout
    _opencode_enabled = start_opencode
    _db().init_db(DB_PATH)
    _db().seed_default_agents(DB_PATH)

    # On crash or force-kill, opencode serve orphans remain.  Register cleanup now so it fires even if we fail later.
    import atexit as _atexit
    def _shutdown_cleanup():
        try:
            from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
            _lc = OpenCodeLifecycleManager(db_path=DB_PATH)
            if _dashboard_supervisor is not None:
                _dashboard_supervisor.stop()
            _lc.stop_all_managed(reason="backend_exit")
            append_text(RUN_LOG, f"[{utc_now()}] shutdown cleanup done\n")
        except Exception as exc:
            append_text(RUN_LOG, f"[{utc_now()}] shutdown cleanup error: {exc}\n")
    _atexit.register(_shutdown_cleanup)

    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager, cleanup_orphan_opencode_servers
        lc = OpenCodeLifecycleManager(db_path=DB_PATH)

        # Phase 1: kill untracked orphan opencode serve processes
        exclude_ports = {port}  # reserve backend UI port
        if manager_port:
            exclude_ports.add(manager_port)
        if worker_port:
            exclude_ports.add(worker_port)
        orphan_result = cleanup_orphan_opencode_servers(db_path=DB_PATH, exclude_ports=exclude_ports)
        append_text(RUN_LOG, f"[{utc_now()}] orphan cleanup: {orphan_result}\n")

        startup_reconcile = lc.reconcile_runtime(
            start_if_missing=False,
            restart_unowned=bool(start_opencode),
        )
        append_text(RUN_LOG, f"[{utc_now()}] startup opencode reconcile: {startup_reconcile}\n")
    except Exception as exc:
        append_text(RUN_LOG, f"[{utc_now()}] startup opencode reconcile failed: {exc}\n")
    if start_opencode:
        try:
            from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager

            post_reconcile = OpenCodeLifecycleManager(db_path=DB_PATH).reconcile_runtime(start_if_missing=True)
            append_text(RUN_LOG, f"[{utc_now()}] startup lifecycle opencode reconcile: {post_reconcile}\n")
            selected = post_reconcile.get("selected")
            if selected:
                print(f"shared opencode server listening on http://{selected['host']}:{selected['port']}")
            else:
                started = post_reconcile.get("started") or {}
                if isinstance(started, dict) and started.get("error"):
                    raise RuntimeError(started["error"])
                raise RuntimeError("opencode_runtime_unavailable")
        except RuntimeError as exc:
            if "opencode command not found" not in str(exc):
                raise
            _opencode_enabled = False
            _dashboard_supervisor = None
            append_text(RUN_LOG, f"[{utc_now()}] opencode disabled at startup: {exc}\n")
            print(f"[WARN] {exc}; starting Task Hounds UI in no-opencode mode")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"Task Hounds UI -> http://127.0.0.1:{port}")
    print(f"Runtime files -> {RUNTIME_FILES}")
    if auto_loop:
        started = start_mvp_loop()
        print(f"MVP runner loop {'started' if started else 'already running'}")
    if run_seconds is not None:
        def stop_later():
            time.sleep(run_seconds)
            server.shutdown()

        threading.Thread(target=stop_later, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    finally:
        stop_mvp_loop()
        if supervisor is not None:
            supervisor.stop()
            if _dashboard_supervisor is supervisor:
                _dashboard_supervisor = None
        server.server_close()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-opencode", action="store_true")
    parser.add_argument("--manager-port", type=int, default=None)
    parser.add_argument("--worker-port", type=int, default=None)
    parser.add_argument("--startup-timeout", type=int, default=90)
    parser.add_argument("--run-seconds", type=int, default=None)
    parser.add_argument("--auto-loop", action="store_true")
    args = parser.parse_args()
    main(
        args.port,
        start_opencode=not args.no_opencode,
        manager_port=args.manager_port,
        worker_port=args.worker_port,
        startup_timeout=args.startup_timeout,
        run_seconds=args.run_seconds,
        auto_loop=args.auto_loop,
    )

