"""Persistent, human-readable frontend debug session logs."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from task_hounds_api.db import ROOT


LOG_DIR = ROOT / "core" / "runtime" / "logs" / "debug"
_WRITE_LOCK = threading.Lock()
_SENSITIVE_KEY = re.compile(
    r"authorization|api[-_]?key|password|secret|token|credential", re.IGNORECASE
)
_SAFE_SESSION_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[max-depth]"
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if _SENSITIVE_KEY.search(str(key))
            else _sanitize(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth + 1) for item in value]
    if isinstance(value, str) and len(value) > 16_000:
        return f"{value[:16_000]}...[truncated {len(value) - 16_000} chars]"
    return value


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _format_value(value: Any, indent: int = 0, key: str | None = None) -> list[str]:
    pad = " " * indent
    prefix = f"{pad}{key}:" if key is not None else pad.rstrip()

    if isinstance(value, dict):
        lines = [prefix] if key is not None else []
        for child_key, child_value in value.items():
            lines.extend(_format_value(child_value, indent + (2 if key is not None else 0), str(child_key)))
        return lines or [f"{prefix} {{}}"]

    if isinstance(value, list):
        lines = [prefix] if key is not None else []
        item_indent = indent + (2 if key is not None else 0)
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{' ' * item_indent}-")
                lines.extend(_format_value(item, item_indent + 2))
            else:
                lines.append(f"{' ' * item_indent}- {_scalar(item)}")
        return lines or [f"{prefix} []"]

    text = _scalar(value)
    if "\n" in text:
        lines = [f"{prefix} |"]
        lines.extend(f"{' ' * (indent + 2)}{line}" for line in text.splitlines())
        return lines
    return [f"{prefix} {text}" if key is not None else f"{pad}{text}"]


def _safe_session_id(raw: Any) -> str:
    value = _SAFE_SESSION_ID.sub("_", str(raw or "unknown-session")).strip("._")
    return (value or "unknown-session")[:120]


def _normalise_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    entries = payload.get("entries")
    if isinstance(entries, list):
        return [entry for entry in entries if isinstance(entry, dict)]
    return [{
        "sequence": "?",
        "timestamp": "",
        "level": "debug",
        "category": "LEGACY",
        "event": payload.get("source", "frontend"),
        "format": "text",
        "data": payload.get("msg", payload),
        "page": "",
    }]


def _format_entry(entry: dict[str, Any]) -> str:
    clean = _sanitize(entry)
    heading = (
        f"[{clean.get('timestamp', '')}] "
        f"#{clean.get('sequence', '?')} "
        f"{str(clean.get('level', 'debug')).upper()} "
        f"{clean.get('category', 'UNKNOWN')} "
        f"{clean.get('event', '')}"
    ).rstrip()
    lines = ["", "=" * 96, heading]
    if clean.get("page"):
        lines.append(f"page: {clean['page']}")
    lines.append(f"format: {clean.get('format', 'unknown')}")
    lines.extend(_format_value(clean.get("data"), key="data"))
    return "\n".join(lines)


def write_debug_batch(
    payload: dict[str, Any],
    *,
    log_dir: Path | None = None,
) -> dict[str, Any]:
    """Append a frontend batch to one human-readable file per UI session."""
    destination = log_dir or LOG_DIR
    session_id = _safe_session_id(payload.get("session_id"))
    entries = _normalise_entries(payload)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"{session_id}.log"

    with _WRITE_LOCK:
        is_new = not path.exists()
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            if is_new:
                handle.write("TASK HOUNDS UI DEBUG SESSION\n")
                handle.write(f"session_id: {session_id}\n")
                handle.write(f"user_agent: {_scalar(payload.get('user_agent', 'unknown'))}\n")
            for entry in entries:
                handle.write(_format_entry(entry))
            handle.write("\n")

    return {
        "ok": True,
        "received": len(entries),
        "session_id": session_id,
        "file": f"core/runtime/logs/debug/{path.name}",
    }


def write_backend_debug(
    session_id: str | None,
    level: str,
    category: str,
    event: str,
    data: Any | None = None,
    *,
    log_dir: Path | None = None,
) -> dict[str, Any]:
    """Write a single backend event to the per-session debug log file.

    This is the helper that api/routes/*.py should use INSTEAD OF (or
    in addition to) the standard Python logger. The Python logger
    goes to stdout/stderr where it can be missed when the server is
    run in the background; this helper persists the event to a
    per-session file that docs/tools/debug_log_writer and the operator
    can read after the fact. Without this, a silent chat failure
    has no operator-visible trace.

    Phase-8 (P2): data=None is passed through as JSON null (not
    coerced to {}). Operators looking at the log can distinguish
    "no data" from "empty dict".
    """
    payload = {
        "session_id": session_id or "backend",
        "user_agent": "task-hounds-backend",
        "entries": [
            {
                "level": level,
                "category": category,
                "event": event,
                "data": data,
            }
        ],
    }
    return write_debug_batch(payload, log_dir=log_dir)
