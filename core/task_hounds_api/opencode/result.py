"""opencode.result — unified JsonResult contract.

Every opencode.client method returns one of these dicts. Callers only
need to check result["ok"]. They never inspect backend-specific fields.

Success:
    {
        "ok": True,
        "backend": "opencode",
        "agent": "manager",
        "run_id": "run_abc123",
        "status": "completed",
        "output": {"text": "...", "files": [], "metrics": {}},
        "error": None,
    }

Failure:
    {
        "ok": False,
        "backend": "opencode",
        "agent": "manager",
        "run_id": "run_abc123",
        "status": "failed",
        "output": None,
        "error": {
            "type": "ConnectionError",
            "message": "Backend process not reachable",
            "retryable": True,
            "raw": "WinError 10061..."
        },
    }
"""
from __future__ import annotations

import uuid
from typing import Any

# ── Public status values ────────────────────────────────────────────────────
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_RUNNING = "running"
STATUS_HEALTHY = "healthy"
STATUS_UNHEALTHY = "unhealthy"
STATUS_STARTED = "started"
STATUS_STOPPED = "stopped"


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:8]


def ok(
    *,
    agent: str,
    status: str = STATUS_COMPLETED,
    run_id: str | None = None,
    text: str = "",
    files: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
    **extra: Any,
) -> dict:
    return {
        "ok": True,
        "backend": "opencode",
        "agent": agent,
        "run_id": run_id or new_run_id(),
        "status": status,
        "output": {
            "text": text,
            "files": files or [],
            "metrics": metrics or {},
            **extra,
        },
        "error": None,
    }


def err(
    *,
    agent: str,
    message: str,
    error_type: str = "Error",
    retryable: bool = False,
    raw: str = "",
    run_id: str | None = None,
    status: str = STATUS_FAILED,
) -> dict:
    return {
        "ok": False,
        "backend": "opencode",
        "agent": agent,
        "run_id": run_id or new_run_id(),
        "status": status,
        "output": None,
        "error": {
            "type": error_type,
            "message": message,
            "retryable": retryable,
            "raw": raw,
        },
    }


def is_retryable(result: dict) -> bool:
    if result.get("ok"):
        return False
    return bool((result.get("error") or {}).get("retryable"))


def get_text(result: dict) -> str:
    if not result.get("ok"):
        return ""
    output = result.get("output") or {}
    return output.get("text", "")


def get_error_message(result: dict) -> str:
    if result.get("ok"):
        return ""
    error = result.get("error") or {}
    return error.get("message", "unknown error")
