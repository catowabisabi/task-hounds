"""
base.py — Shared utilities for all Task Hounds agents.

Extracted from mvp/runner.py.  All agent modules (manager, worker, reviewer)
import from here.  No agent-specific logic lives here.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Project root & runtime dirs ──────────────────────────────────────────────

RUNTIME_DIR = os.environ.get("POWER_TEAMS_RUNTIME_DIR") or "core/runtime"
ROOT = Path(__file__).resolve().parents[3]
RUNTIME_PATH = ROOT / RUNTIME_DIR

DEFAULT_AGENT_FALLBACKS = tuple(os.environ.get("POWER_TEAMS_AGENT_FALLBACKS", "sisyphus-junior,build").split(","))
DEFAULT_MODEL_FALLBACKS = tuple(os.environ.get("POWER_TEAMS_MODEL_FALLBACKS", "minimax-coding-plan/MiniMax-M2.7").split(","))

try:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from power_teams.db import (
    DB_PATH,
    add_manager_message,
    connect,
    create_reviewer_session,
    create_suggestion,
    get_active_reviewer_session,
    get_active_suggestion,
    get_agent,
    get_agent_binding,
    get_latest_handoff,
    get_reviewer_feedback,
    resolve_role_opencode_session,
    save_role_opencode_session,
    init_db,
    is_reviewer_timeout,
    list_manager_messages,
    mark_reviewer_timeout,
    seed_default_agents,
    update_agent,
    update_project_session,
    update_reviewer_session,
    update_suggestion,
    upsert_handoff,
)

LEGACY_FILES_DIR = RUNTIME_PATH / "agent_files"
SESSIONS_DIR = RUNTIME_PATH / "sessions"
SESSION_STATE = SESSIONS_DIR / "session_state.json"
LOG_DIR = RUNTIME_PATH / "logs"
RUN_LOG = LOG_DIR / "runner.log"
OPENCODE_CONFIG_HOME = RUNTIME_PATH / "opencode_home" / ".config"
OPENCODE_DATA_HOME = RUNTIME_PATH / "opencode_home" / ".local" / "share"
OPENCODE_CONFIG_DIR = RUNTIME_PATH / "opencode_config"
SETTINGS_FILE = RUNTIME_PATH / "settings.json"


# ── Helpers defined early so other helpers can call them ─────────────────────

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_runtime_files() -> None:
    files_dir().mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    defaults = {
        user_input_path():    "",
        worker_report_path(): "# Worker Report\n",
        worker_status_path(): "idle\n",
    }
    for path, value in defaults.items():
        if not path.exists() or (path == worker_status_path() and not path.read_text(encoding="utf-8").strip()):
            path.write_text(value, encoding="utf-8")


# ── File path helpers ─────────────────────────────────────────────────────────

def files_dir() -> Path:
    """Per-session runtime folder, falling back to the legacy shared dir
    when no project session is active."""
    sid = None
    try:
        if SETTINGS_FILE.exists():
            sid = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("active_project_session")
    except Exception:
        sid = None
    if sid:
        d = SESSIONS_DIR / sid / "agent_files"
        d.mkdir(parents=True, exist_ok=True)
        return d
    LEGACY_FILES_DIR.mkdir(parents=True, exist_ok=True)
    return LEGACY_FILES_DIR


# Back-compat: a `FILES_DIR` module attribute for any external import.
FILES_DIR = LEGACY_FILES_DIR


def user_input_path()    -> Path: return files_dir() / "user_input.txt"
def worker_report_path() -> Path: return files_dir() / "worker_report.md"
def worker_status_path() -> Path: return files_dir() / "work_0001_status.txt"


# Legacy constant names (deprecated; use the helpers above instead)
USER_INPUT    = LEGACY_FILES_DIR / "user_input.txt"
WORKER_REPORT = LEGACY_FILES_DIR / "worker_report.md"
WORKER_STATUS = LEGACY_FILES_DIR / "work_0001_status.txt"


# ── Language / settings helpers ───────────────────────────────────────────────

LANG_INSTRUCTIONS = {
    "en":    "You MUST respond entirely in English. All output, explanations, and structured tags must be in English.",
    "zh-tw": "【語言指令】你必須完全使用繁體中文回應。所有輸出、說明、結構化標籤內容都必須是繁體中文。嚴禁使用英文（程式碼除外）。",
    "ja":    "【言語指令】すべての応答を日本語で行ってください。説明・構造化タグの内容もすべて日本語で記述してください（コードを除く）。",
}


def get_language_instruction() -> str:
    try:
        if SETTINGS_FILE.exists():
            s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            lang = s.get("language", "en")
            instr = LANG_INSTRUCTIONS.get(lang, f"You MUST respond entirely in {lang}.")
            if s.get("force_thinking_language"):
                lang_names = {"en": "English", "zh-tw": "繁體中文", "ja": "日本語"}
                lang_name = lang_names.get(lang, lang)
                instr += f" Your internal reasoning and thinking process must also be in {lang_name}."
            return instr
    except Exception:
        pass
    return ""


def get_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def get_active_session_id() -> str | None:
    return get_settings().get("active_project_session")


def get_active_workspace_path() -> str | None:
    path = get_settings().get("workspace_path")
    if path and Path(path).exists():
        return path
    return None


# ── Session-scoped DB wrappers ────────────────────────────────────────────────
# Automatically inject the active project session_id so all data is
# associated with the correct session without changing every call site.

def _sid() -> str | None:
    return get_active_session_id()


def _add_manager_message(content: str) -> int:
    return add_manager_message(content, session_id=_sid())


def _create_suggestion(content, verification=None, related_files=None, handoff_version=None) -> int:
    return create_suggestion(content, verification=verification,
                             related_files=related_files, handoff_version=handoff_version,
                             session_id=_sid())


def _upsert_handoff(updated_by="manager", **fields) -> int:
    return upsert_handoff(updated_by=updated_by, session_id=_sid(), **fields)


def _get_active_suggestion():
    return get_active_suggestion(session_id=_sid())


def _get_latest_handoff():
    return get_latest_handoff(session_id=_sid())


def _list_manager_messages():
    return list_manager_messages(session_id=_sid())


# ── Manager format instruction blocks (shared by manager.py) ──────────────────

_FORCE_PLANNING_INSTRUCTION = """
=== FORCE PLANNING MODE (REQUIRED — NON-NEGOTIABLE) ===
Every response MUST contain a <PLAN>...</PLAN> block. The plan area in the UI
is wired to read this block and a missing or empty <PLAN> is treated as a
manager failure for this cycle.

Format:

<PLAN>
## Goal
[One sentence: what we are trying to achieve in this cycle]

## Steps
1. [Step one — specific, actionable]
2. [Step two]
...

## Success Criteria
- [ ] [How we know this is done]
</PLAN>

Rules:
- The <PLAN> block MUST appear BEFORE <SUGGESTION_CONTENT>.
- The plan may be updated each cycle. The UI replaces the previous plan with
  the latest one you emit, so always include the full current plan, not a diff.
- Do NOT skip the plan even if "nothing has changed" — emit the same plan again.
===========================================
"""

_FORCE_TODO_INSTRUCTION = """
=== FORCE TODO MODE (REQUIRED — NON-NEGOTIABLE) ===
Every response MUST contain a <TODO_LIST>...</TODO_LIST> block. The Todo rail
in the UI is wired to read this block. You control top-level items; worker
adds sub-items under each one.

Every response MUST also contain a <TODO_UPDATE_JSON>...</TODO_UPDATE_JSON>
block. This JSON is the authoritative machine-readable todo update. Do not
claim a todo is done in MANAGER_MESSAGE unless this JSON marks it completed.

CRITICAL: If you write anything meaningful in <PLAN>, you MUST translate it
into concrete tasks in <TODO_LIST>. Do not keep implementation details only in
the planning text. The todo list is the executable project control surface.

Format (one item per line, exactly these status markers):

<TODO_LIST>
- [ ] pending task one
- [→] in-progress task two
- [✓] completed task three
- [✗] blocked task four — reason
</TODO_LIST>

JSON format (valid JSON only, no markdown fences):

<TODO_UPDATE_JSON>
{
  "items": [
    {
      "id": "existing todo id when available",
      "content": "short stable todo title",
      "status": "pending|in_progress|completed|blocked",
      "position": 0
    }
  ]
}
</TODO_UPDATE_JSON>

Rules:
- Always emit the FULL current todo list (not a diff). The UI replaces your
  top-level items with this list each cycle, preserving worker sub-items
  where the top-level text matches.
- Always emit the FULL current todo JSON in TODO_UPDATE_JSON. Prefer existing
  todo IDs from the current todo context when available.
- Top-level items must be short (under ~80 chars) and unique within the list.
- Update status markers based on actual worker progress, not aspirations.
- Do NOT skip the <TODO_LIST> even if the list is unchanged — emit it again.
- Do NOT create a todo whose content says "No further task needed"; use
  <DIRECTIVE_COMPLETE/> when no further work is required.
===========================================
"""


# ── Global state ──────────────────────────────────────────────────────────────

# Bug 4: consecutive-silence counter — fail task only after ≥3 silences
_agent_silence_count: dict[str, int] = {}


# ── File I/O helpers ──────────────────────────────────────────────────────────

def read_text(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def append_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(value)


def repair_mojibake(value: str) -> str:
    if not isinstance(value, str):
        return value
    text = value
    for _ in range(3):
        if not any(marker in text for marker in ("Ã", "Â", "â")):
            break
        fixed = None
        for enc in ("cp1252", "latin1"):
            try:
                fixed = text.encode(enc, errors="ignore").decode("utf-8")
            except UnicodeError:
                continue
            if fixed and fixed != text:
                text = fixed
                break
        else:
            break
    return text


def log(msg: str) -> None:
    stamp = utc_now()
    line = f"[{stamp}] {msg}\n"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="", flush=True)


# ── TMUX idle detection ───────────────────────────────────────────────────────

def check_tmux_idle(session_name: str = "power-teams", pane: str = "0") -> bool:
    """
    Check if the TMUX session pane shows idle state.

    Returns:
        True   — pane is idle (shows expected idle markers)
        False  — pane is busy
        None   — TMUX unavailable or session not found
    """
    session_name = os.environ.get("POWER_TEAMS_TMUX_SESSION", session_name)
    pane = os.environ.get("POWER_TEAMS_TMUX_PANE", pane)
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", f"{session_name}:{pane}", "-p"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        content = result.stdout
        idle_markers = [
            "Ask anything",
            "Sisyphus - Ultraworker",
        ]
        return any(marker in content for marker in idle_markers)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def send_via_tmux(
    prompt: str,
    session_name: str = "power-teams",
    pane: str = "0",
    end_marker: str = "ULW",
) -> bool:
    """
    Send a prompt to a TMUX pane via set-buffer + paste-buffer.
    Returns True on success, False on failure.
    """
    session_name = os.environ.get("POWER_TEAMS_TMUX_SESSION", session_name)
    pane = os.environ.get("POWER_TEAMS_TMUX_PANE", pane)
    full_text = prompt.rstrip("\n") + f"\n{end_marker}\n"
    try:
        subprocess.run(
            ["tmux", "set-buffer", full_text],
            check=True, timeout=5, capture_output=True,
        )
        subprocess.run(
            ["tmux", "paste-buffer", "-t", f"{session_name}:{pane}"],
            check=True, timeout=5, capture_output=True,
        )
        log(f"send_via_tmux: prompt ({len(full_text)} chars) pasted to {session_name}:{pane}")
        return True
    except FileNotFoundError:
        log("send_via_tmux: tmux command not found")
        return False
    except subprocess.CalledProcessError as exc:
        log(f"send_via_tmux: tmux returned non-zero: {exc.returncode}")
        return False
    except subprocess.TimeoutExpired:
        log("send_via_tmux: tmux command timed out")
        return False


def wait_for_tmux_idle(
    session_name: str = "power-teams",
    pane: str = "0",
    timeout_s: int = 600,
    poll_interval_s: float = 5.0,
) -> bool:
    """Block until TMUX pane is idle or timeout_s elapses. Returns True if idle."""
    session_name = os.environ.get("POWER_TEAMS_TMUX_SESSION", session_name)
    pane = os.environ.get("POWER_TEAMS_TMUX_PANE", pane)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = check_tmux_idle(session_name, pane)
        if result is True:
            return True
        time.sleep(poll_interval_s)
    return False


# ── Manager lock (file-based, prevents concurrent manager_cycle) ──────────────

def _acquire_manager_lock() -> bool:
    lock_path = RUNTIME_PATH / "manager.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            content = lock_path.read_text().strip()
            parts = content.split("|")
            if len(parts) >= 2:
                old_pid = int(parts[0])
                age = time.time() - float(parts[1])
                try:
                    os.kill(old_pid, 0)
                    if age < 300:
                        log(f"_acquire_manager_lock: lock held by pid={old_pid}, age={age:.0f}s — skipping")
                        return False
                except OSError:
                    log(f"_acquire_manager_lock: stale lock found, pid={old_pid} dead — removing")
                    lock_path.unlink()
        except (ValueError, OSError):
            lock_path.unlink()

    lock_path.write_text(f"{os.getpid()}|{time.time()}")
    log("_acquire_manager_lock: lock acquired")
    return True


def _release_manager_lock() -> None:
    lock_path = RUNTIME_PATH / "manager.lock"
    if lock_path.exists():
        try:
            lock_path.unlink()
            log("_release_manager_lock: lock released")
        except OSError:
            pass


# ── opencode environment & session helpers ────────────────────────────────────

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


def opencode_bin() -> str:
    from power_teams.runtime.opencode_binary import find_opencode_bin
    return find_opencode_bin(required=True)


def load_sessions() -> dict:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    if not SESSION_STATE.exists():
        return {}
    return json.loads(SESSION_STATE.read_text(encoding="utf-8"))


def save_sessions(state: dict) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _reuse_opencode_sessions() -> bool:
    value = str(os.environ.get("POWER_TEAMS_REUSE_OPENCODE_SESSIONS", "1")).lower()
    return value not in {"0", "false", "no", "off"}


def _runtime_timeout_seconds(name: str, env_name: str, default: int) -> int:
    settings = get_settings()
    legacy_name = name.removesuffix("_seconds")
    value = settings.get(name, settings.get(legacy_name, os.environ.get(env_name, default)))
    try:
        return max(30, int(value))
    except (TypeError, ValueError):
        return default


def _abort_opencode_session(base_url: str, session_id: str | None, agent_name: str) -> bool:
    if not session_id:
        return False
    try:
        import urllib.request as _urlreq
        req = _urlreq.Request(
            f"{base_url}/session/{session_id}/abort",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        _urlreq.urlopen(req, timeout=5).close()
        log(f"{agent_name}: aborted OpenCode session {session_id[:16]}")
        return True
    except Exception as exc:
        log(f"{agent_name}: abort session {session_id[:16]} failed: {exc}")
        return False


# ── opencode health check & auto-restart ─────────────────────────────────────

def _is_opencode_process(host: str, port: int) -> bool:
    if os.name != "nt":
        return True
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$c=Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; "
             "if ($c) { (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName }"],
            capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        name = (ps.stdout or "").strip().lower()
        if "opencode" in name:
            return True
        if "wslrelay" in name:
            wsl = subprocess.run(
                ["wsl", "sh", "-lc",
                 f"ss -ltnp 2>/dev/null | grep ':{port} ' | grep -i opencode"],
                capture_output=True, text=True, timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return "opencode" in (wsl.stdout or "").lower()
        return False
    except Exception:
        return False


def _managed_opencode_pid_for_port(host: str, port: int) -> int | None:
    try:
        with connect(DB_PATH) as db:
            row = db.execute(
                """
                SELECT pid FROM opencode_server_instances
                 WHERE host=? AND port=? AND owner='power_teams' AND managed=1 AND status='running'
                 ORDER BY id DESC LIMIT 1
                """,
                (host, int(port)),
            ).fetchone()
        if row and row["pid"]:
            return int(row["pid"])
    except Exception:
        pass
    return None


def _ping_opencode_port(host: str, port: int, timeout: float = 2.0) -> bool:
    import urllib.request as _urlreq
    for path in ("/global/health", "/session", "/"):
        try:
            with _urlreq.urlopen(f"http://{host}:{port}{path}", timeout=timeout) as resp:
                if resp.status in (200, 204, 400, 401, 403, 404, 405):
                    return True
        except Exception:
            pass
    return False


def _restart_opencode_server(agent_name: str, host: str, port: int) -> bool:
    if not _is_opencode_process(host, port):
        log(f"{agent_name}: port {host}:{port} not owned by opencode — skipping auto-restart (external server?)")
        return False
    managed_pid = _managed_opencode_pid_for_port(host, port)
    if managed_pid:
        log(f"{agent_name}: stopping managed opencode pid={managed_pid} on {host}:{port} before restart")
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(managed_pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                os.kill(managed_pid, 15)
            time.sleep(1)
        except Exception as exc:
            log(f"{agent_name}: failed stopping managed opencode pid={managed_pid}: {exc}")
    else:
        log(f"{agent_name}: port {host}:{port} is opencode but not a managed running DB instance — skipping auto-restart")
        return False
    log(f"{agent_name}: opencode server not responding on {host}:{port} — attempting restart")
    bin_path = opencode_bin()
    if bin_path == "opencode":
        log(f"{agent_name}: opencode binary not found, cannot restart server")
        return False

    from power_teams.runtime.opencode_supervisor import (
        build_opencode_serve_args as _oc_serve_args,
        opencode_env as _oc_env,
        opencode_debug_console_enabled as _oc_debug_console,
        opencode_serve_creation_flags as _oc_serve_flags,
        LOG_DIR as _OC_LOG_DIR,
    )
    _OC_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _OC_LOG_DIR / f"{agent_name}_restart.log"
    try:
        log_f = log_path.open("a", encoding="utf-8", buffering=1)
        log_f.write(f"\n[restart] restarting {agent_name} on {host}:{port}\n")
        debug_console = _oc_debug_console()
        serve_args = _oc_serve_args(bin_path, port, debug_console=debug_console)
        proc = subprocess.Popen(
            serve_args,
            cwd=str(ROOT),
            env=_oc_env(),
            stdout=None if debug_console else subprocess.PIPE,
            stderr=None if debug_console else subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            creationflags=_oc_serve_flags(debug_console=debug_console),
        )
        if debug_console:
            log_f.write("[restart] debug console enabled; OpenCode logs are visible in the serve shell\n")
            log_f.close()
        else:
            threading.Thread(
                target=lambda: [log_f.write(line) for line in (proc.stdout or [])],
                daemon=True,
            ).start()
        log(f"{agent_name}: restart process pid={proc.pid}, waiting up to 30s for health")
    except Exception as exc:
        log(f"{agent_name}: failed to launch restart process: {exc}")
        return False

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _ping_opencode_port(host, port, timeout=1.0):
            log(f"{agent_name}: opencode server back up on port {port}")
            return True
        time.sleep(1)
    log(f"{agent_name}: opencode server did NOT become healthy within 30s after restart")
    return False


def is_insufficient_balance_error(error_msg: str) -> bool:
    if not isinstance(error_msg, str):
        return False
    error_lower = error_msg.lower()
    indicators = [
        "insufficient_balance", "insufficient quota", "quota exceeded",
        "payment required", "402", "余额不足", "餘額不足",
        "credits error", "billing", "no payment method",
    ]
    return any(indicator in error_lower for indicator in indicators)


# ── Event schema normalization (stream → {type, text, raw}) ──────────────────

def extract_text(ev: dict) -> str:
    """Pure: extract text content from any opencode event, zero side effects."""
    txt = ""
    part = ev.get("part")
    if isinstance(part, dict):
        txt = part.get("text") or part.get("content") or ""
    if not txt:
        txt = ev.get("text") or ""
    if not txt:
        content = ev.get("content")
        if isinstance(content, str):
            txt = content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    txt = block.get("text") or block.get("content") or ""
                    if txt:
                        break
    return txt.strip()


def normalize_event(ev: dict) -> dict:
    """Thin wrapper: normalize any opencode stream event."""
    return {"type": ev.get("type", ""), "text": extract_text(ev), "raw": ev}


def _record_missing_text(ev: dict, tag: str = "") -> None:
    """Lightweight telemetry: log non-control events that carry no text."""
    etype = ev.get("type", "")
    if etype in ("step_start", "step_finish", "error"):
        return
    log(f"normalize: {tag}: type={etype} no text — check if schema changed")


def _fetch_attached_session_text(base_url: str, session_id: str) -> str:
    import urllib.request as _urlreq

    try:
        req = _urlreq.Request(
            f"{base_url}/session/{session_id}/message",
            headers={"Accept": "application/json"},
        )
        with _urlreq.urlopen(req, timeout=10) as resp:
            messages = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log(f"attach fallback: failed reading session messages for {session_id[:16]}: {exc}")
        return ""

    def _message_role(item: dict) -> str:
        info = item.get("info") or {}
        message = item.get("message") or {}
        return str(info.get("role") or item.get("role") or message.get("role") or "").lower()

    def _collect_text(value) -> list[str]:
        texts: list[str] = []
        if isinstance(value, str):
            if value.strip():
                texts.append(value.strip())
        elif isinstance(value, list):
            for child in value:
                texts.extend(_collect_text(child))
        elif isinstance(value, dict):
            if value.get("type") in (None, "text", "assistant", "message"):
                for key in ("text", "content", "message", "value"):
                    if key in value:
                        texts.extend(_collect_text(value.get(key)))
            for key in ("parts", "part"):
                if key in value:
                    texts.extend(_collect_text(value.get(key)))
        return texts

    for item in reversed(messages or []):
        if not isinstance(item, dict):
            continue
        if _message_role(item) != "assistant":
            continue
        texts = _collect_text(item.get("parts") or item.get("content") or item.get("message") or item)
        if texts:
            return "\n".join(texts).strip()
    return ""


def _create_attached_session(base_url: str, agent_name: str) -> str | None:
    import urllib.request as _urlreq

    try:
        req = _urlreq.Request(
            f"{base_url}/session",
            data=b"{}",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        with _urlreq.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        sid = payload.get("id") if isinstance(payload, dict) else None
        if sid:
            log(f"{agent_name}: pre-created OpenCode session {sid[:16]}")
            return sid
    except Exception as exc:
        log(f"{agent_name}: pre-create OpenCode session failed: {exc}")
    return None


def _attached_session_exists(base_url: str, session_id: str) -> bool:
    import urllib.error as _urlerr
    import urllib.request as _urlreq

    try:
        req = _urlreq.Request(
            f"{base_url}/session/{session_id}",
            headers={"Accept": "application/json"},
        )
        with _urlreq.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except _urlerr.HTTPError:
        return False
    except Exception as exc:
        log(f"attach session check failed for {session_id[:16]}: {exc}")
        return False


# ── Core: send prompt to an opencode agent ───────────────────────────────────

def send_to_agent(agent_name: str, prompt: str, max_retries: int = 1, cwd: str | None = None) -> str:
    """
    Send a prompt through the managed OpenCode server using `opencode run --attach`.
    Some OpenCode versions do not stream final text on stdout in attach mode;
    when that happens we read the completed assistant message from the server.
    """
    agent_row = dict(get_agent(agent_name))
    try:
        binding = get_agent_binding(agent_name, path=DB_PATH)
        if binding:
            agent_row["host"] = binding["host"] or agent_row["host"]
            agent_row["port"] = int(binding["port"] or agent_row["port"])
            agent_row["model"] = binding["model"]
            agent_row["opencode_agent"] = binding["opencode_agent"] or agent_row["opencode_agent"]
    except Exception:
        pass
    cwd = cwd or get_active_workspace_path() or str(ROOT)
    cwd_path = Path(cwd)
    if not cwd_path.exists():
        raise RuntimeError(f"active project folder is missing: {cwd}")
    stream_file = files_dir() / f"{agent_name}_stream.txt"
    if agent_name == "chat":
        stream_file.parent.mkdir(parents=True, exist_ok=True)
        if stream_file.exists() and stream_file.read_text(encoding="utf-8").strip():
            append_text(stream_file, json.dumps({"t": "sys", "msg": "new chat turn"}) + "\n")
        else:
            write_text(stream_file, "")
    else:
        write_text(stream_file, "")

    project_session_id = get_active_session_id() or "legacy"
    role_session_id = f"{project_session_id}:{agent_name}"
    lang_instr = get_language_instruction()
    if lang_instr:
        current_language = get_settings().get("language", "en")
        prompt = (
            f"[LANGUAGE DIRECTIVE — MANDATORY]\n"
            f"Current UI language: {current_language}\n"
            f"{lang_instr}\n\n"
            f"{prompt}"
        )

    if agent_name != "chat":
        db_skill_snippet = (
            f"[TASK HOUNDS DB SKILL — AVAILABLE WHEN NEEDED]\n"
            f"project_session_id: {project_session_id}\n"
            f"role: {agent_name}\n"
            f"role_session_id: {role_session_id}\n"
            "Do not read DB skill docs from the active project workspace. "
            "Only use the DB skill when project memory or controlled writes are needed.\n\n"
        )
        prompt = db_skill_snippet + prompt

    base_url = f"http://{agent_row['host']}:{agent_row['port']}"
    sessions = load_sessions()
    project_role_key = f"{project_session_id}:{agent_name}"
    existing_session = None

    def _clear_stored_session() -> None:
        sessions.pop(project_role_key, None)
        if project_session_id != "legacy":
            column = f"{agent_name}_session_id"
            if column in {"manager_session_id", "worker_session_id", "reviewer_session_id", "chat_session_id"}:
                try:
                    update_project_session(project_session_id, **{column: None})
                except Exception as exc:
                    log(f"{agent_name}: failed clearing {column} for {project_session_id}: {exc}")
        update_agent(agent_name, session_id=None)
        save_sessions(sessions)

    if _reuse_opencode_sessions():
        if project_session_id != "legacy":
            existing_session = resolve_role_opencode_session(
                project_session_id,
                agent_name,
                require_existing=False,
                allow_agent_registry_fallback=False,
            )
        session_entry = sessions.get(project_role_key)
        if not existing_session and isinstance(session_entry, dict):
            if session_entry.get("dir") == str(cwd_path):
                existing_session = session_entry.get("id")
        elif not existing_session and project_role_key.startswith("legacy:"):
            existing_session = session_entry
        if not existing_session and project_role_key.startswith("legacy:"):
            existing_session = agent_row["session_id"]
        if existing_session and str(agent_row.get("state") or "").lower() == "error" and agent_row.get("last_error"):
            log(f"{agent_name}: ignoring stored OpenCode session after error state; starting fresh")
            _clear_stored_session()
            existing_session = None

    def _available_agents_map() -> dict[str, str]:
        try:
            from power_teams.runtime.opencode_lifecycle import get_cached_available_agents
            return {
                str(a.get("id") or a.get("name") or "").strip(): str(a.get("mode") or "")
                for a in get_cached_available_agents()
            }
        except Exception:
            return {}

    def _resolve_opencode_agent(fallback_index: int = -1) -> str | None:
        user_value = str(agent_row.get("opencode_agent") or "").strip()
        agents_map = _available_agents_map()

        if fallback_index < 0:
            if user_value and user_value.lower() not in {"", "default"}:
                mode = agents_map.get(user_value, "")
                if mode != "subagent":
                    return user_value
                log(f"{agent_name}: opencode_agent='{user_value}' is subagent, skipping to fallback chain")

        for i, fallback in enumerate(DEFAULT_AGENT_FALLBACKS):
            if fallback_index >= 0 and i < fallback_index:
                continue
            mode = agents_map.get(fallback, "")
            if not agents_map or mode == "primary":
                return fallback

        if agents_map:
            for name, mode in agents_map.items():
                if mode == "primary":
                    return name
        return None

    def _resolve_model(fallback_index: int = -1) -> str | None:
        user_value = str(agent_row.get("model") or "").strip()
        if fallback_index < 0:
            if user_value:
                return user_value

        if fallback_index < 0:
            fallback_index = 0
        if fallback_index < len(DEFAULT_MODEL_FALLBACKS):
            return DEFAULT_MODEL_FALLBACKS[fallback_index]
        return None

    current_agent_fallback = -1
    current_model_fallback = -1

    resolved_agent = _resolve_opencode_agent(current_agent_fallback)
    resolved_model = _resolve_model(current_model_fallback)

    update_agent(agent_name, state="busy", last_seen=utc_now())
    resolved_agent_for_log = resolved_agent or "<opencode default>"
    resolved_model_for_log = resolved_model or "<opencode default>"
    session_display = existing_session[:16] if existing_session else "<new>"
    log(f"{agent_name}: opencode run agent={resolved_agent_for_log} model={resolved_model_for_log}"
        f" role_session={project_role_key}"
        f" opencode_session={session_display}")
    append_text(stream_file,
                json.dumps({"t": "sys", "msg": f"opencode run agent={resolved_agent_for_log} model={resolved_model_for_log}"}) + "\n")

    def _build_cmd(session_id: str | None, agent: str | None = None, model: str | None = None) -> list[str]:
        cmd = [
            opencode_bin(), "run",
            "--attach", base_url,
            "--format", "json",
            "--thinking",
            "--dangerously-skip-permissions",
            "--dir", str(cwd_path),
        ]
        if agent:
            cmd += ["--agent", agent]
        #if model:
            #cmd += ["--model", model]
        if session_id:
            cmd += ["--session", session_id]
        cmd.append(prompt)
        return cmd

    _oc_host = agent_row["host"]
    _oc_port = agent_row["port"]
    if not _ping_opencode_port(_oc_host, _oc_port):
        log(f"{agent_name}: health check FAILED on {_oc_host}:{_oc_port} — trying auto-restart")
        append_text(stream_file, json.dumps({"t": "sys", "msg": "opencode server not responding — attempting restart", "kind": "warn"}) + "\n")
        _restart_opencode_server(agent_name, _oc_host, _oc_port)
        time.sleep(3)

    if existing_session and not _attached_session_exists(base_url, existing_session):
        log(f"{agent_name}: stale OpenCode session {existing_session[:16]} not found on {base_url}; starting fresh")
        append_text(stream_file, json.dumps({"t": "sys", "msg": "stale OpenCode session not found on server; starting fresh", "kind": "warn"}) + "\n")
        _clear_stored_session()
        existing_session = None

    last_error = None
    session_id = existing_session
    if not session_id and _reuse_opencode_sessions():
        session_id = _create_attached_session(base_url, agent_name)
        if session_id:
            sessions[project_role_key] = {"id": session_id, "dir": str(cwd_path)}
            save_sessions(sessions)
            if project_session_id != "legacy":
                save_role_opencode_session(project_session_id, agent_name, session_id)
            else:
                update_agent(agent_name, session_id=session_id)

    for attempt in range(max_retries + 1):
        cmd = _build_cmd(session_id, agent=resolved_agent, model=resolved_model)
        text_parts: list[str] = []
        captured_sid: str | None = None
        start = time.monotonic()
        running = [True]

        def _heartbeat():
            prev = 0
            while running[0]:
                elapsed = int(time.monotonic() - start)
                if elapsed >= prev + 30:
                    prev = elapsed
                    log(f"{agent_name}: {elapsed}s elapsed (deep thinking in progress...)")
                    append_text(stream_file,
                                json.dumps({"t": "sys", "msg": f"{elapsed}s elapsed", "kind": "elapsed"}) + "\n")
                time.sleep(5)

        def _permission_watcher():
            import urllib.request as _urlreq
            import urllib.error as _urlerr
            while running[0]:
                try:
                    req = _urlreq.Request(f"{base_url}/permission",
                                         headers={"Accept": "application/json"})
                    with _urlreq.urlopen(req, timeout=3) as resp:
                        pending = json.loads(resp.read().decode())
                    for perm in (pending or []):
                        pid = perm.get("id")
                        pname = perm.get("permission", "?")
                        patterns = perm.get("patterns", [])
                        if not pid:
                            continue
                        body = json.dumps({"reply": "always"}).encode()
                        preq = _urlreq.Request(
                            f"{base_url}/permission/{pid}/reply",
                            data=body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        try:
                            _urlreq.urlopen(preq, timeout=3)
                            msg = f"[permission:auto-approved] {pname} {patterns}"
                            log(f"{agent_name}: {msg}")
                            append_text(stream_file,
                                        json.dumps({"t": "permission", "tool": pname, "patterns": patterns}) + "\n")
                            print(msg, flush=True)
                        except Exception:
                            pass
                except (_urlerr.URLError, Exception):
                    pass
                time.sleep(2)

        hb = threading.Thread(target=_heartbeat, daemon=True)
        hb.start()
        pw = threading.Thread(target=_permission_watcher, daemon=True)
        pw.start()

        SILENCE_TIMEOUT = _runtime_timeout_seconds("silence_timeout_seconds", "POWER_TEAMS_SILENCE_TIMEOUT", 480)
        HARD_TIMEOUT = _runtime_timeout_seconds("hard_timeout_seconds", "POWER_TEAMS_HARD_TIMEOUT", 1200)
        PID_FILE = files_dir() / f"{agent_name}_opencode.pid"

        try:
            env = opencode_env()
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                env=env,
                cwd=cwd,
            )

            write_text(PID_FILE, str(proc.pid))
            debug_file = files_dir() / f"{agent_name}_debug.jsonl"
            write_text(debug_file, "")

            def _emit(obj: dict) -> None:
                obj.setdefault("ts", time.time())
                append_text(stream_file, json.dumps(obj, ensure_ascii=False) + "\n")

            last_output_time = [time.monotonic()]
            silence_killed = [False]

            def _watchdog():
                while running[0]:
                    time.sleep(10)
                    if not running[0]:
                        break
                    elapsed_total = time.monotonic() - start
                    elapsed_silent = time.monotonic() - last_output_time[0]
                    if elapsed_total > HARD_TIMEOUT:
                        log(f"{agent_name}: HARD TIMEOUT ({HARD_TIMEOUT}s) — killing opencode pid={proc.pid}")
                        _emit({"t": "error", "msg": f"Hard timeout after {int(elapsed_total)}s — process killed"})
                        update_agent(agent_name, state="error", last_error=f"Hard timeout after {int(elapsed_total)}s", last_seen=utc_now())
                        _abort_opencode_session(base_url, captured_sid or session_id, agent_name)
                        try: proc.kill()
                        except Exception: pass
                        break
                    if elapsed_silent > SILENCE_TIMEOUT:
                        partial_so_far = "\n".join(text_parts).strip()
                        if partial_so_far:
                            partial_file = files_dir() / f"{agent_name}_partial.txt"
                            try:
                                partial_file.write_text(partial_so_far, encoding="utf-8")
                                log(f"{agent_name}: saved {len(partial_so_far)} chars of partial progress to {partial_file.name}")
                            except Exception:
                                pass
                        _agent_silence_count[agent_name] = _agent_silence_count.get(agent_name, 0) + 1
                        sc = _agent_silence_count[agent_name]
                        silence_killed[0] = True
                        log(f"{agent_name}: SILENCE TIMEOUT ({SILENCE_TIMEOUT}s no output) — silence #{sc} — killing opencode pid={proc.pid}")
                        _emit({"t": "error", "msg": f"Silence timeout ({SILENCE_TIMEOUT}s no output) — process killed (silence #{sc}/3)"})
                        update_agent(agent_name, state="error", last_error=f"Silence timeout after {SILENCE_TIMEOUT}s no output", last_seen=utc_now())
                        _abort_opencode_session(base_url, captured_sid or session_id, agent_name)
                        try: proc.kill()
                        except Exception: pass
                        break

            wd = threading.Thread(target=_watchdog, daemon=True)
            wd.start()

            for raw in proc.stdout:
                last_output_time[0] = time.monotonic()
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                append_text(debug_file, raw + "\n")
                try:
                    ev = json.loads(raw)
                    #==================test
                    append_text(
                        debug_file,
                        json.dumps({
                            "raw": ev,
                            "type": ev.get("type"),
                            "text": extract_text(ev),
                            "keys": list(ev.keys())
                        }, ensure_ascii=False) + "\n"
                    )
                    #==================test end
                    etype = ev.get("type", "")

                    if not captured_sid:
                        captured_sid = ev.get("sessionID")




                    if etype == "":
                        print("⚠️ EMPTY TYPE EVENT:", ev)

                    if etype not in ("text", "reasoning", "thinking", "assistant", "message", "tool_use", "step_start", "step_finish", "error"):
                        print("UNKNOWN EVENT:", ev)


                    if etype == "text":
                        n = normalize_event(ev)
                        txt = n["text"].strip()
                        if not txt:
                            _record_missing_text(ev, tag="text")
                        if txt:
                            text_parts.append(txt)
                            _emit({"t": "text", "text": txt})
                            print(txt, flush=True)

                    elif etype in ("reasoning", "thinking"):
                        n = normalize_event(ev)
                        txt = n["text"].strip()
                        if not txt:
                            _record_missing_text(ev, tag="reasoning")
                        if txt:
                            _emit({"t": "think", "text": txt})
                            preview = txt[:200].replace("\n", " ")
                            print(f"[think] {preview}{'...' if len(txt) > 200 else ''}", flush=True)

                    elif etype in ("assistant", "message"):
                        content = ev.get("content") or ev.get("message") or ""
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    txt = (block.get("text") or "").strip()
                                    if txt:
                                        text_parts.append(txt)
                                        _emit({"t": "text", "text": txt})
                                        print(txt, flush=True)
                        elif isinstance(content, str) and content.strip():
                            text_parts.append(content.strip())
                            _emit({"t": "text", "text": content.strip()})
                            print(content.strip(), flush=True)

                    elif etype == "tool_use":
                        part = ev.get("part") or {}
                        tool_name = part.get("tool", "?")
                        state = part.get("state") or {}
                        inp = state.get("input") or {}
                        out = state.get("output") or ""
                        err = state.get("error") or ""
                        status = (state.get("status") or "running")
                        _emit({
                            "t": "tool",
                            "name": tool_name,
                            "status": status,
                            "input": inp if isinstance(inp, dict) else {"_": str(inp)[:300]},
                            "output": str(out)[:400] if out else "",
                            "error": str(err)[:300] if err else "",
                        })
                        print(f"[tool: {tool_name}] {str(inp)[:120]}", flush=True)

                    elif etype == "step_finish":
                        part = ev.get("part") or {}
                        tokens = part.get("tokens") or {}
                        cost = part.get("cost") or 0
                        _emit({
                            "t": "step_end",
                            "reason": part.get("reason", ""),
                            "tokens": tokens,
                            "cost": cost,
                        })

                    elif etype == "error":
                        err_msg = str(ev.get("error", "unknown error"))
                        log(f"{agent_name}: session error: {err_msg}")
                        _emit({"t": "error", "msg": err_msg})

                except json.JSONDecodeError:
                    _emit({"t": "raw", "text": raw})

            proc.wait()
            running[0] = False
            try: PID_FILE.unlink(missing_ok=True)
            except Exception: pass

            if proc.returncode != 0:
                stderr_out = proc.stderr.read()

                if silence_killed[0]:
                    sc = _agent_silence_count.get(agent_name, 0)
                    if sc >= 3:
                        log(f"{agent_name}: silence count={sc} >= 3 — marking task as FAILED")
                        append_text(stream_file, json.dumps({"t": "error", "msg": f"Task failed: {sc} consecutive silence timeouts"}) + "\n")
                        _agent_silence_count[agent_name] = 0
                        raise RuntimeError(f"{agent_name} failed: {sc} consecutive silence timeouts")
                    elif attempt < max_retries:
                        log(f"{agent_name}: silence #{sc}/3 — restarting with continuation prompt")
                        append_text(stream_file, json.dumps({"t": "sys", "msg": f"Silence timeout #{sc}/3 — restarting process", "kind": "warn"}) + "\n")
                        partial_file = files_dir() / f"{agent_name}_partial.txt"
                        partial_saved = ""
                        try:
                            if partial_file.exists():
                                partial_saved = partial_file.read_text(encoding="utf-8").strip()
                        except Exception:
                            pass
                        if partial_saved:
                            prompt = (
                                f"[RESUME AFTER SILENCE TIMEOUT — attempt {attempt + 2}]\n"
                                f"The previous run was killed due to silence timeout ({SILENCE_TIMEOUT}s). "
                                f"Below is the partial progress so far. Please continue from where it left off:\n\n"
                                f"=== PARTIAL PROGRESS ===\n{partial_saved[:3000]}\n\n"
                                f"=== ORIGINAL TASK ===\n{prompt}"
                            )
                        session_id = None
                        _clear_stored_session()
                        time.sleep(3)
                        last_error = RuntimeError(f"silence timeout #{sc}")
                        continue

                _is_agent_error = any(kw in stderr_out.lower() for kw in ("subagent", "not a primary agent", "session not found", "agent not found"))
                if _is_agent_error:
                    next_agent_fi = current_agent_fallback + 1
                    next_model_fi = current_model_fallback
                    tried_next = False
                    if next_agent_fi < len(DEFAULT_AGENT_FALLBACKS):
                        resolved_agent = _resolve_opencode_agent(next_agent_fi) or "build"
                        resolved_model = _resolve_model(next_model_fi)
                        current_agent_fallback = next_agent_fi
                        session_id = None
                        _clear_stored_session()
                        log(f"{agent_name}: agent/model error — falling back to agent={resolved_agent} model={resolved_model}")
                        append_text(stream_file, json.dumps({"t": "sys", "msg": f"Falling back: agent={resolved_agent} model={resolved_model}", "kind": "warn"}) + "\n")
                        last_error = RuntimeError(f"agent error, trying fallback: {stderr_out[:200]}")
                        continue
                    if next_model_fi < len(DEFAULT_MODEL_FALLBACKS):
                        resolved_model = _resolve_model(next_model_fi)
                        current_model_fallback = next_model_fi
                        session_id = None
                        _clear_stored_session()
                        log(f"{agent_name}: agent error (no more agent fallbacks) — falling back to model={resolved_model}")
                        append_text(stream_file, json.dumps({"t": "sys", "msg": f"Falling back model: {resolved_model}", "kind": "warn"}) + "\n")
                        last_error = RuntimeError(f"agent error, trying model fallback: {stderr_out[:200]}")
                        continue

                raise RuntimeError(
                    f"opencode run exited {proc.returncode}: {stderr_out[:300]}"
                )

            _agent_silence_count[agent_name] = 0

            if captured_sid and _reuse_opencode_sessions():
                sessions[project_role_key] = {"id": captured_sid, "dir": str(cwd_path)}
                save_sessions(sessions)
                if project_session_id != "legacy":
                    save_role_opencode_session(project_session_id, agent_name, captured_sid)
                else:
                    update_agent(agent_name, session_id=captured_sid)

            result = "\n".join(text_parts).strip()
            elapsed = int(time.monotonic() - start)

            if not result:
                fallback_sid = captured_sid or session_id
                if fallback_sid:
                    fallback_text = _fetch_attached_session_text(base_url, fallback_sid)
                    if fallback_text:
                        result = fallback_text
                        text_parts.append(fallback_text)
                        append_text(stream_file, json.dumps({"t": "text", "text": fallback_text}, ensure_ascii=False) + "\n")
                        log(f"{agent_name}: recovered {len(fallback_text)} chars from attached session message API")

            if not result and proc.returncode == 0:
                try:
                    _stderr_text = proc.stderr.read()
                except Exception:
                    _stderr_text = ""
                if any(kw in _stderr_text.lower() for kw in ("subagent", "not a primary agent")):
                    log(f"{agent_name}: exit-0 subagent detected — stderr: {_stderr_text[:200]}")
                    append_text(stream_file, json.dumps({"t": "sys", "msg": f"subagent exit-0: {_stderr_text.strip()[:100]}", "kind": "warn"}) + "\n")
                    _is_agent_error = True
                    next_agent_fi = current_agent_fallback + 1
                    next_model_fi = current_model_fallback
                    if next_agent_fi < len(DEFAULT_AGENT_FALLBACKS):
                        resolved_agent = _resolve_opencode_agent(next_agent_fi) or "build"
                        resolved_model = _resolve_model(next_model_fi)
                        current_agent_fallback = next_agent_fi
                        session_id = None
                        _clear_stored_session()
                        log(f"{agent_name}: agent/model error (exit-0) — falling back to agent={resolved_agent} model={resolved_model}")
                        append_text(stream_file, json.dumps({"t": "sys", "msg": f"Falling back: agent={resolved_agent} model={resolved_model}", "kind": "warn"}) + "\n")
                        last_error = RuntimeError(f"exit-0 subagent: {_stderr_text[:200]}")
                        continue
                    if next_model_fi < len(DEFAULT_MODEL_FALLBACKS):
                        resolved_model = _resolve_model(next_model_fi)
                        current_model_fallback = next_model_fi
                        session_id = None
                        _clear_stored_session()
                        log(f"{agent_name}: agent error (exit-0, no agent fallbacks) — falling back to model={resolved_model}")
                        append_text(stream_file, json.dumps({"t": "sys", "msg": f"Falling back model: {resolved_model}", "kind": "warn"}) + "\n")
                        last_error = RuntimeError(f"exit-0 subagent: {_stderr_text[:200]}")
                        continue
                    append_text(stream_file, json.dumps({"t": "error", "msg": "All agent/model fallbacks exhausted for subagent exit-0"}) + "\n")
                    raise RuntimeError(f"exit-0 subagent, all fallbacks exhausted: {_stderr_text[:200]}")

            if not result and proc.returncode == 0:
                try:
                    _stderr_text = proc.stderr.read()
                except Exception:
                    _stderr_text = ""
                if any(kw in _stderr_text.lower() for kw in ("subagent", "not a primary agent")):
                    log(f"{agent_name}: exit-0 subagent detected — stderr: {_stderr_text[:200]}")
                    append_text(stream_file, json.dumps({"t": "sys", "msg": f"subagent exit-0: {_stderr_text.strip()[:100]}", "kind": "warn"}) + "\n")
                    _is_agent_error = True
                    next_agent_fi = current_agent_fallback + 1
                    next_model_fi = current_model_fallback
                    if next_agent_fi < len(DEFAULT_AGENT_FALLBACKS):
                        resolved_agent = _resolve_opencode_agent(next_agent_fi) or "build"
                        resolved_model = _resolve_model(next_model_fi)
                        current_agent_fallback = next_agent_fi
                        session_id = None
                        _clear_stored_session()
                        log(f"{agent_name}: agent/model error (exit-0) — falling back to agent={resolved_agent} model={resolved_model}")
                        append_text(stream_file, json.dumps({"t": "sys", "msg": f"Falling back: agent={resolved_agent} model={resolved_model}", "kind": "warn"}) + "\n")
                        last_error = RuntimeError(f"exit-0 subagent: {_stderr_text[:200]}")
                        continue
                    if next_model_fi < len(DEFAULT_MODEL_FALLBACKS):
                        resolved_model = _resolve_model(next_model_fi)
                        current_model_fallback = next_model_fi
                        session_id = None
                        _clear_stored_session()
                        log(f"{agent_name}: agent error (exit-0, no agent fallbacks) — falling back to model={resolved_model}")
                        append_text(stream_file, json.dumps({"t": "sys", "msg": f"Falling back model: {resolved_model}", "kind": "warn"}) + "\n")
                        last_error = RuntimeError(f"exit-0 subagent: {_stderr_text[:200]}")
                        continue

            if not result:
                if not captured_sid and attempt < max_retries:
                    log(f"{agent_name}: no stdout session id from {base_url}; restarting managed OpenCode server")
                    append_text(stream_file, json.dumps({"t": "sys", "msg": "no OpenCode session id returned; restarting managed server", "kind": "warn"}) + "\n")
                    _restart_opencode_server(agent_name, _oc_host, _oc_port)
                    session_id = None
                    _clear_stored_session()
                    time.sleep(3)
                    last_error = RuntimeError("no session id from opencode")
                    continue
                if attempt < max_retries:
                    log(f"{agent_name}: 0 chars after {elapsed}s — retrying with fresh session")
                    append_text(stream_file, json.dumps({"t": "sys", "msg": "0-char response, retrying...", "kind": "warn"}) + "\n")
                    session_id = None
                    _clear_stored_session()
                    time.sleep(3)
                    last_error = RuntimeError("empty response from opencode")
                    continue
                append_text(stream_file, json.dumps({"t": "error", "msg": "OpenCode returned no text output"}) + "\n")
                raise RuntimeError("empty response from opencode")

            if agent_name != "chat" and _reuse_opencode_sessions() and len(result) < 120 and result.endswith("?") and "<" not in result and captured_sid:
                log(f"{agent_name}: got greeting '{result}' — re-sending to warmed session {captured_sid[:16]}")
                append_text(stream_file, json.dumps({"t": "sys", "msg": "greeting detected, re-sending...", "kind": "warn"}) + "\n")
                session_id = captured_sid
                last_error = RuntimeError(f"greeting only: {result!r}")
                time.sleep(2)
                continue

            log(f"{agent_name}: done in {elapsed}s — {len(result)} chars")
            if resolved_agent and resolved_agent != str(agent_row.get("opencode_agent") or "").strip():
                try:
                    update_agent(agent_name, opencode_agent=resolved_agent)
                    log(f"{agent_name}: wrote back resolved agent '{resolved_agent}' to DB")
                except Exception:
                    pass
            if resolved_model and resolved_model != str(agent_row.get("model") or "").strip():
                try:
                    update_agent(agent_name, model=resolved_model)
                    log(f"{agent_name}: wrote back resolved model '{resolved_model}' to DB")
                except Exception:
                    pass
            update_agent(agent_name, state="idle", last_seen=utc_now())
            return result

        except Exception as exc:
            running[0] = False
            error_msg = str(exc)
            last_error = exc

            if is_insufficient_balance_error(error_msg):
                log(f"{agent_name}: ⚠️ INSUFFICIENT BALANCE detected. Stopping retries immediately.")
                log(f"{agent_name}: 💡 Please add credits at: https://opencode.ai/billing")
                append_text(stream_file, json.dumps({"t": "error", "msg": "Insufficient balance. No point retrying."}) + "\n")
                append_text(stream_file, json.dumps({"t": "sys", "msg": "Add credits: https://opencode.ai/billing", "kind": "info"}) + "\n")
                try:
                    _add_manager_message(
                        f"⚠️ API Error: Insufficient balance or quota exceeded. "
                        f"Please add credits to continue. Error: {error_msg[:200]}"
                    )
                except Exception:
                    pass
                update_agent(agent_name, state="error", last_seen=utc_now())
                raise RuntimeError(
                    f"{agent_name} failed: Insufficient API balance. Please recharge at https://opencode.ai/billing"
                ) from exc

            is_connection_error = isinstance(exc, (ConnectionRefusedError, ConnectionError)) or \
                any(kw in error_msg.lower() for kw in ("connection refused", "connectionerror", "failed to connect", "errno 111", "winerror 10061"))
            if is_connection_error and attempt < max_retries:
                log(f"{agent_name}: connection error detected — attempting opencode server restart (attempt {attempt + 1})")
                append_text(stream_file, json.dumps({"t": "sys", "msg": f"Connection refused — restarting opencode server (retry {attempt + 1})", "kind": "warn"}) + "\n")
                _restart_opencode_server(agent_name, _oc_host, _oc_port)
                time.sleep(3)
                session_id = None
                _clear_stored_session()
                last_error = exc
                continue

            if attempt < max_retries:
                log(f"{agent_name} attempt {attempt + 1} failed: {exc}  retrying with fresh session")
                append_text(stream_file, json.dumps({"t": "sys", "msg": f"Retry {attempt + 1}: {str(exc)[:200]}", "kind": "warn"}) + "\n")
                session_id = None
                _clear_stored_session()
                time.sleep(3)
            else:
                log(f"{agent_name} all {max_retries + 1} attempts failed")
                append_text(stream_file, json.dumps({"t": "error", "msg": str(exc)[:400]}) + "\n")

    update_agent(agent_name, state="error", last_seen=utc_now())
    raise RuntimeError(
        f"{agent_name} failed after {max_retries + 1} attempts: {last_error}"
    ) from last_error


# ── Handoff helpers ───────────────────────────────────────────────────────────

def handoff_summary(handoff) -> str:
    """Build a compact human-readable summary from a handoff DB row."""
    if handoff is None:
        return "(no project handoff yet)"

    parts = [f"=== PROJECT HANDOFF v{handoff['version']} ===\n"]

    def _field(label, key):
        val = handoff[key]
        if not val:
            return
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list) and parsed:
                parts.append(f"\n{label}:")
                for idx, item in enumerate(parsed, start=1):
                    if isinstance(item, dict):
                        summary = f"  {idx}. " + "; ".join(f"{k}: {v}" for k, v in item.items())
                        parts.append(summary)
                    else:
                        parts.append(f"  {idx}. {item}")
            elif isinstance(parsed, dict):
                parts.append(f"\n{label}: {json.dumps(parsed, ensure_ascii=False)}")
            else:
                parts.append(f"\n{label}: {val}")
        except (json.JSONDecodeError, TypeError):
            parts.append(f"\n{label}: {val}")

    _field("Human Requirements", "human_requirements")
    _field("Working Direction", "working_direction")
    _field("File Structure", "file_structure")
    _field("Important Files", "important_files")
    _field("Available Scripts", "available_scripts")
    _field("Existing Solutions", "existing_solutions")
    _field("References/Demos", "references_demos")
    _field("Macro Flow (Phases)", "macro_flow")
    _field("Current Task", "current_task")
    _field("Current Micro Flow", "current_micro_flow")
    _field("Known Bugs", "known_bugs")
    _field("Tested Files", "tested_files")
    _field("Completion Criteria", "completion_criteria")
    _field("Human Concerns", "human_concerns")
    _field("Project Folder", "project_folder_location")

    return "\n".join(parts)


def _extract_section(text: str, tag: str) -> str:
    """Extract content between XML-like tags. Returns empty string if not found."""
    pattern = rf"<{tag}>(.*?)</{tag}>"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


# ── Plan / TODO persistence ───────────────────────────────────────────────────

_TODO_STATUS_MAP = {
    " ": "pending",
    "x": "completed", "X": "completed", "✓": "completed", "✔": "completed",
    "→": "in_progress", ">": "in_progress",
    "✗": "blocked", "x*": "blocked", "!": "blocked",
}


def _persist_plan(content: str, updated_by: str = "manager") -> bool:
    """UPSERT the plan for the active session. Returns True if anything was written."""
    if not content.strip():
        return False
    sid = _sid()
    if not sid:
        log("Plan present but no active session — skipped")
        return False
    try:
        with connect(DB_PATH) as conn:
            conn.execute(
                """INSERT INTO session_plan (session_id, content, updated_by, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(session_id) DO UPDATE SET
                     content=excluded.content,
                     updated_by=excluded.updated_by,
                     updated_at=CURRENT_TIMESTAMP""",
                (sid, content.strip(), updated_by),
            )
            conn.commit()
        log(f"Plan saved ({len(content)} chars, by={updated_by})")
        return True
    except Exception as exc:
        log(f"Failed to save plan: {exc}")
        return False


def _parse_todo_block(block: str) -> list[dict]:
    """Parse a <TODO_LIST> block into [{content, status}] entries."""
    out: list[dict] = []
    line_re = re.compile(r"^\s*[-*]\s*\[([^\]]+)\]\s*(.+?)\s*$")
    for raw in block.splitlines():
        m = line_re.match(raw)
        if not m:
            continue
        marker = m.group(1).strip()
        text = m.group(2).strip()
        if not text:
            continue
        status = _TODO_STATUS_MAP.get(marker)
        if status is None:
            status = _TODO_STATUS_MAP.get(marker.lower(), "pending")
        out.append({"content": text, "status": status})
    return out


def _persist_todos(items: list[dict], owner: str = "manager") -> bool:
    """Sync manager-owned top-level todos with the parsed list."""
    if not items:
        return False
    sid = _sid()
    if not sid:
        log("Todos present but no active session — skipped")
        return False
    import uuid
    try:
        with connect(DB_PATH) as conn:
            existing = conn.execute(
                """SELECT id, content, status, position FROM session_todos
                   WHERE session_id=? AND parent_id IS NULL
                   ORDER BY position""",
                (sid,)
            ).fetchall()
            existing_by_content = {dict(r)["content"]: dict(r) for r in existing}
            kept_ids: set[str] = set()
            for pos, it in enumerate(items):
                content = it["content"]
                status  = it.get("status", "pending")
                if content in existing_by_content:
                    row = existing_by_content[content]
                    kept_ids.add(row["id"])
                    conn.execute(
                        "UPDATE session_todos SET status=?, position=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (status, pos, row["id"]),
                    )
                else:
                    new_id = str(uuid.uuid4())
                    conn.execute(
                        """INSERT INTO session_todos
                             (id, session_id, parent_id, content, status, priority, position, owner)
                           VALUES (?, ?, NULL, ?, ?, 'medium', ?, ?)""",
                        (new_id, sid, content, status, pos, owner),
                    )
            for row_content, row in existing_by_content.items():
                if row["id"] in kept_ids:
                    continue
                conn.execute(
                    "DELETE FROM session_todos WHERE id=? OR parent_id=?",
                    (row["id"], row["id"]),
                )
            conn.commit()
        log(f"Todos synced: {len(items)} items, by={owner}")
        return True
    except Exception as exc:
        log(f"Failed to save todos: {exc}")
        return False


def _parse_todo_update_json(block: str) -> list[dict]:
    if not block or not block.strip():
        return []
    try:
        payload = json.loads(block.strip())
    except Exception as exc:
        log(f"Invalid TODO_UPDATE_JSON: {exc}")
        return []
    raw_items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        return []
    items: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "").strip()
        status = str(raw.get("status") or "pending").strip().lower()
        if status not in {"pending", "in_progress", "completed", "blocked"}:
            status = "pending"
        item = {
            "id": str(raw.get("id") or "").strip() or None,
            "content": content,
            "status": status,
            "priority": str(raw.get("priority") or "medium").strip() or "medium",
            "position": raw.get("position"),
        }
        if item["id"] or item["content"]:
            items.append(item)
    return items


def _persist_todo_update_json(items: list[dict], owner: str = "manager") -> bool:
    if not items:
        return False
    sid = _sid()
    if not sid:
        log("TODO_UPDATE_JSON present but no active session — skipped")
        return False
    import uuid
    try:
        with connect(DB_PATH) as conn:
            existing = conn.execute(
                """SELECT id, content, status, position FROM session_todos
                   WHERE session_id=? AND parent_id IS NULL
                   ORDER BY position, id""",
                (sid,),
            ).fetchall()
            by_id = {dict(row)["id"]: dict(row) for row in existing}
            by_content = {dict(row)["content"]: dict(row) for row in existing}
            kept_ids: set[str] = set()
            for pos, item in enumerate(items):
                content = item.get("content") or ""
                status = item.get("status") or "pending"
                item_id = item.get("id")
                try:
                    position = int(item.get("position")) if item.get("position") is not None else pos
                except (TypeError, ValueError):
                    position = pos

                row = by_id.get(item_id) if item_id else None
                if row is None and content:
                    row = by_content.get(content)

                if row:
                    kept_ids.add(row["id"])
                    conn.execute(
                        """UPDATE session_todos
                           SET content=?, status=?, priority=?, position=?, owner=?, updated_at=CURRENT_TIMESTAMP
                           WHERE id=? AND session_id=?""",
                        (content or row["content"], status, item.get("priority") or "medium", position, owner, row["id"], sid),
                    )
                elif content and not content.lower().startswith("no further"):
                    new_id = item_id or str(uuid.uuid4())
                    kept_ids.add(new_id)
                    conn.execute(
                        """INSERT INTO session_todos
                             (id, session_id, parent_id, content, status, priority, position, owner)
                           VALUES (?, ?, NULL, ?, ?, ?, ?, ?)""",
                        (new_id, sid, content, status, item.get("priority") or "medium", position, owner),
                    )

            for row in existing:
                row = dict(row)
                if row["id"] in kept_ids:
                    continue
                conn.execute("DELETE FROM session_todos WHERE id=? OR parent_id=?", (row["id"], row["id"]))
            conn.commit()
        log(f"TODO_UPDATE_JSON synced: {len(items)} items, by={owner}")
        return True
    except Exception as exc:
        log(f"Failed to save TODO_UPDATE_JSON: {exc}")
        return False


def _persist_plan_and_todos_from(response: str, owner: str = "manager") -> None:
    """Extract <PLAN> and <TODO_UPDATE_JSON> from a manager response and write to DB."""
    plan = _extract_section(response, "PLAN")
    if plan:
        _persist_plan(plan, updated_by=owner)
    todo_json = _extract_section(response, "TODO_UPDATE_JSON")
    if todo_json:
        items = _parse_todo_update_json(todo_json)
        if items and _persist_todo_update_json(items, owner=owner):
            return
        log("TODO_UPDATE_JSON present but invalid or empty; skipped TODO_LIST fallback")
        return
    log("TODO_UPDATE_JSON missing; skipped TODO_LIST fallback")
    return


def apply_handoff_update(manager_response: str, updated_by: str = "manager"):
    """Parse HANDOFF_UPDATE block from manager response and persist to DB."""
    manager_response = repair_mojibake(manager_response or "")
    raw = _extract_section(manager_response, "HANDOFF_UPDATE")
    if not raw and "</HANDOFF_UPDATE>" in manager_response:
        before = manager_response.split("</HANDOFF_UPDATE>", 1)[0]
        start = before.rfind("{")
        if start != -1:
            raw = before[start:].strip()
    if not raw:
        return None

    fields: dict = {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            fields = {k: v for k, v in data.items() if v is not None}
    except json.JSONDecodeError:
        fields = {"current_task": raw}

    if not fields:
        return None

    new_ver = _upsert_handoff(updated_by=updated_by, **fields)
    log(f"Handoff updated to version {new_ver} by {updated_by}")
    return new_ver

