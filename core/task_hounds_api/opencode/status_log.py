"""Startup/status logging for managed OpenCode serve.

This log is deliberately separate from the per-call emit log. It records what
OpenCode servers existed when Task Hounds starts, what we attempted to stop,
what we started, and which role bindings point where.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from task_hounds_api.db import ROOT
from task_hounds_api.opencode.log_rotation import rotate_if_needed

_WRITE_LOCK = threading.Lock()
_BOOT_ID = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}-pid{os.getpid()}"


def log_path() -> Path:
    explicit = os.environ.get("TASK_HOUNDS_OPENCODE_SERVE_STATUS_LOG")
    if explicit:
        return Path(explicit)
    return ROOT / "core" / "runtime" / "logs" / "opencode" / "opencode_serve_status.log"


def append(event: str, data: dict[str, Any] | None = None) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "boot_id": _BOOT_ID,
        "event": event,
        "data": _redact(data or {}),
    }
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _WRITE_LOCK:
            rotate_if_needed(path, incoming_bytes=len(line.encode("utf-8")) + 1)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
    except Exception:
        pass


def snapshot(event: str, extra: dict[str, Any] | None = None) -> None:
    data: dict[str, Any] = {
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "opencode_processes": list_local_opencode_processes(),
        "bindings": list_bindings(),
        "servers": list_registered_servers(),
        "policy": runtime_policy(),
        "settings": settings_snapshot(),
    }
    if extra:
        data.update(extra)
    append(event, data)


def list_local_opencode_processes() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    script = (
        "$rows = Get-CimInstance Win32_Process -Filter \"Name='opencode.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,CreationDate; "
        "$rows | ConvertTo-Json -Depth 4"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        raw = (result.stdout or "").strip()
        if not raw:
            return []
        parsed = json.loads(raw)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"}]

    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cmd = str(row.get("CommandLine") or "")
        out.append({
            "pid": row.get("ProcessId"),
            "parent_pid": row.get("ParentProcessId"),
            "exe": row.get("ExecutablePath"),
            "command_line": cmd,
            "creation_date": row.get("CreationDate"),
            "port": _extract_port(cmd),
            "is_serve": bool(re.search(r"\bserve\b", cmd)),
        })
    return out


def list_bindings() -> list[dict[str, Any]]:
    try:
        from task_hounds_api.db.ops import runtime as db_rt

        return db_rt.list_bindings()
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"}]


def list_registered_servers() -> list[dict[str, Any]]:
    try:
        from task_hounds_api.db.ops import runtime as db_rt

        return db_rt.list_servers()
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"}]


def runtime_policy() -> dict[str, Any]:
    try:
        from task_hounds_api.db.ops import runtime as db_rt

        return db_rt.get_policy()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def settings_snapshot() -> dict[str, Any]:
    paths = [
        ROOT / "core" / "runtime" / "settings.json",
        ROOT / "core" / "runtime" / "settings-default.json",
        ROOT / "core" / "runtime" / "opencode_config" / "opencode.jsonc",
    ]
    out: dict[str, Any] = {
        "env": {
            key: os.environ.get(key)
            for key in [
                "TASK_HOUNDS_OPENCODE_PORT",
                "TASK_HOUNDS_OPENCODE_MODEL",
                "TASK_HOUNDS_OPENCODE_AGENT",
                "POWER_TEAMS_RUNTIME_DIR",
                "TASK_HOUNDS_PORT",
                "OPENCODE_CONFIG_DIR",
                "OPENCODE_CONFIG",
            ]
            if os.environ.get(key) is not None
        }
    }
    for path in paths:
        key = str(path)
        if not path.exists():
            out[key] = {"exists": False}
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
            if path.name == "opencode.jsonc":
                out[key] = _opencode_config_summary(text)
            else:
                out[key] = {"exists": True, "text": text}
        except Exception as exc:
            out[key] = {"exists": True, "error": f"{type(exc).__name__}: {exc}"}
    return out


def _opencode_config_summary(text: str) -> dict[str, Any]:
    try:
        from task_hounds_api.opencode.config import _strip_jsonc

        parsed = json.loads(_strip_jsonc(text))
        providers = parsed.get("provider") or {}
        return {
            "exists": True,
            "schema": parsed.get("$schema"),
            "plugins": parsed.get("plugin"),
            "providers": {
                provider_id: {
                    "name": provider.get("name"),
                    "npm": provider.get("npm"),
                    "options": _redact(provider.get("options") or {}),
                    "models": list((provider.get("models") or {}).keys()),
                }
                for provider_id, provider in providers.items()
                if isinstance(provider, dict)
            },
        }
    except Exception as exc:
        return {"exists": True, "error": f"{type(exc).__name__}: {exc}"}


def _extract_port(command_line: str) -> int | None:
    match = re.search(r"(?:--port\s+|--port=)(\d+)", command_line)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _redact(value: Any, key: str = "", depth: int = 0) -> Any:
    if depth > 8:
        return "[max-depth]"
    sensitive = any(
        marker in key.lower()
        for marker in ("key", "token", "secret", "password", "credential", "authorization")
    )
    if isinstance(value, str):
        if sensitive:
            if not value:
                return ""
            return {
                "redacted": True,
                "length": len(value),
                "prefix": value[:6],
                "suffix": value[-4:] if len(value) >= 4 else "",
            }
        return value
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k), depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, key, depth + 1) for item in value]
    return value
