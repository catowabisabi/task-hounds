from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT / "core" / "runtime"
DB_PATH = ROOT / "core" / "db" / "power_teams.db"
MAX_FIELD_CHARS = 4000

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def now() -> str:
    return time.strftime("%H:%M:%S")


def print_step(name: str, data: Any) -> None:
    print(f"\n[{now()}] {name}")
    if isinstance(data, (dict, list)):
        print(json.dumps(compact(data), ensure_ascii=False, indent=2, default=str))
    else:
        print(data)


def compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): compact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(v) for v in value[:20]] + ([f"... truncated {len(value) - 20} more items"] if len(value) > 20 else [])
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"... <truncated {len(value) - MAX_FIELD_CHARS} chars>"
    return value


def request_json(method: str, url: str, body: dict[str, Any] | None = None, timeout: float = 10.0) -> tuple[int | None, Any, float]:
    started = time.monotonic()
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            elapsed = time.monotonic() - started
            try:
                return resp.status, json.loads(raw) if raw else None, elapsed
            except json.JSONDecodeError:
                return resp.status, raw, elapsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        elapsed = time.monotonic() - started
        try:
            data: Any = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            data = raw
        return exc.code, data, elapsed
    except Exception as exc:
        elapsed = time.monotonic() - started
        return None, {"error": type(exc).__name__, "detail": str(exc)}, elapsed


def read_settings_file() -> dict[str, Any]:
    path = RUNTIME_DIR / "settings.json"
    if not path.exists():
        return {"error": f"settings file not found: {path}"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc), "path": str(path)}


def db_snapshot(session_id: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {"db_path": str(DB_PATH), "exists": DB_PATH.exists()}
    if not DB_PATH.exists():
        return result
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        if session_id:
            row = con.execute(
                """SELECT id, name, workspace_path, manager_session_id, worker_session_id,
                          reviewer_session_id, chat_session_id, updated_at
                   FROM project_sessions WHERE id=?""",
                (session_id,),
            ).fetchone()
            result["project_session"] = dict(row) if row else None
        chat = con.execute(
            """SELECT name, host, port, model, opencode_agent, state, session_id,
                      last_error, updated_at
               FROM agent_registry WHERE name='chat'"""
        ).fetchone()
        result["chat_agent"] = dict(chat) if chat else None
        messages = con.execute(
            "SELECT id, sender, length(content) AS chars, created_at FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 5",
            (session_id or "",),
        ).fetchall() if session_id else []
        result["recent_chat_messages"] = [dict(row) for row in messages]
        con.close()
    except Exception as exc:
        result["error"] = str(exc)
    return result


def tail_file(path: Path, lines: int = 20) -> list[str]:
    if not path.exists():
        return [f"not found: {path}"]
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except Exception as exc:
        return [f"failed reading {path}: {exc}"]


def probe_http(base_url: str, path: str, timeout: float = 5.0) -> dict[str, Any]:
    status, data, elapsed = request_json("GET", f"{base_url.rstrip('/')}{path}", timeout=timeout)
    if path == "/session" and isinstance(data, list):
        data = {
            "count": len(data),
            "sample": [
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "agent": item.get("agent"),
                    "updated": (item.get("time") or {}).get("updated") if isinstance(item.get("time"), dict) else None,
                }
                for item in data[:5]
                if isinstance(item, dict)
            ],
        }
    return {"path": path, "status": status, "elapsed_s": round(elapsed, 3), "data": data}


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Task Hounds chat backend without UI.")
    parser.add_argument("--base", default="http://127.0.0.1:8766", help="FastAPI base URL")
    parser.add_argument("--message", default="diagnostic ping: reply with one short sentence", help="Chat message to send")
    parser.add_argument("--send-timeout", type=float, default=90.0, help="Timeout seconds for /api/chat/send")
    parser.add_argument("--skip-send", action="store_true", help="Only inspect status; do not send chat message")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    print_step("diagnostic_start", {"base": base, "root": str(ROOT), "send_timeout_s": args.send_timeout})

    for endpoint in ("/api/health", "/api/settings", "/api/chat/status", "/api/agents", "/api/runtime/status"):
        status, data, elapsed = request_json("GET", f"{base}{endpoint}", timeout=10)
        print_step(f"GET {endpoint}", {"status": status, "elapsed_s": round(elapsed, 3), "data": data})

    settings = read_settings_file()
    active_session = settings.get("active_project_session") if isinstance(settings, dict) else None
    print_step("settings_file", settings)
    print_step("db_before_send", db_snapshot(active_session))

    chat_status_code, chat_status, _ = request_json("GET", f"{base}/api/chat/status", timeout=10)
    binding = chat_status.get("binding") if isinstance(chat_status, dict) else None
    if binding:
        host = binding.get("host") or "127.0.0.1"
        port = binding.get("port")
        oc_base = f"http://{host}:{port}"
        print_step("opencode_binding_probe", {
            "binding": binding,
            "session_list": probe_http(oc_base, "/session"),
        })
        snap = db_snapshot(active_session)
        sid = ((snap.get("project_session") or {}).get("chat_session_id") if isinstance(snap, dict) else None)
        if sid:
            print_step("opencode_chat_session_probe", probe_http(oc_base, f"/session/{sid}"))
    else:
        print_step("opencode_binding_probe", {"status": chat_status_code, "error": "no chat binding from /api/chat/status"})

    if not args.skip_send:
        print_step("POST /api/chat/send starting", {"message": args.message})
        status, data, elapsed = request_json(
            "POST",
            f"{base}/api/chat/send",
            {"content": args.message},
            timeout=args.send_timeout,
        )
        print_step("POST /api/chat/send finished", {"status": status, "elapsed_s": round(elapsed, 3), "data": data})
        if status is None:
            print_step("chat_send_timeout_hint", "The HTTP request did not finish before timeout. The hang is inside /api/chat/send or the OpenCode call it waits for.")

    print_step("diagnostic_summary", {
        "fastapi_reachable": True,
        "chat_send_tested": not args.skip_send,
        "next_interpretation": [
            "If /api/health fails: backend is not listening on --base.",
            "If /api/chat/status fails: chat binding/runtime discovery is broken.",
            "If opencode_chat_session_probe fails: DB has a stale chat_session_id.",
            "If /api/chat/send times out while elapsed logs appear: OpenCode run is hanging/silent after send_to_agent starts.",
        ],
    })

    print_step("db_after_send", db_snapshot(active_session))
    print_step("runner_log_tail", tail_file(RUNTIME_DIR / "logs" / "runner.log"))
    if active_session:
        print_step("chat_stream_tail", tail_file(RUNTIME_DIR / "sessions" / active_session / "agent_files" / "chat_stream.txt"))
        print_step("chat_debug_tail", tail_file(RUNTIME_DIR / "sessions" / active_session / "agent_files" / "chat_debug.jsonl", lines=10))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
