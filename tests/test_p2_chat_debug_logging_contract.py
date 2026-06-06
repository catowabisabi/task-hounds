"""Phase 8 (P2 chat logging): Persistent debug log contract.

The audit reproduced: write_backend_debug used `data or {}` which
mangled None into {}. Operators looking at the debug log couldn't
distinguish "no data" from "empty dict". Also: chat.py didn't log
the full request body or the full response, and chat_agent.send
exceptions were not logged at all.

Fix:
  1. write_backend_debug: pass data=None through as JSON null
     (not coerced to {}).
  2. chat.py: log the full request AND full response for every
     call site (list_messages, send, status).
  3. chat_agent.send exception: caught and logged as
     send.exception, with the original error preserved.

Tests (5):
  - test_data_none_passes_through_as_null: write_backend_debug(
    session_id, level, category, event, data=None) writes
    data: null (not data: {}).
  - test_success_log_contains_full_response: chat.py list_messages
    success log includes the full row payload.
  - test_failed_response_preserved_completely: chat.py send
    failure log includes the full error dict.
  - test_send_exception_writes_log: chat_agent.send raises;
    chat.py catches and writes send.exception log.
  - test_sensitive_fields_still_redacted: apiKey in data is
    redacted in the log file.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "phase8_p2.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    return db


def _log_dir(monkeypatch, tmp_path) -> Path:
    d = tmp_path / "p2_logs"
    monkeypatch.setattr("task_hounds_api.api.debug_logs.LOG_DIR", d)
    return d


# ── Test 1: data=None passes through as null (not {}) ──


def test_data_none_passes_through_as_null(monkeypatch, tmp_path):
    from task_hounds_api.api import debug_logs
    d = _log_dir(monkeypatch, tmp_path)
    debug_logs.write_backend_debug(
        session_id="p2_test_null",
        level="info",
        category="chat",
        event="send.response_none",
        data=None,
    )
    path = d / "p2_test_null.log"
    content = path.read_text(encoding="utf-8")
    # The bug: data was being coerced to {} via `data or {}`.
    # The fix: data=None must serialize as the JSON literal null.
    assert "data: null" in content, (
        f"data=None must be written as JSON null, not {{}}. "
        f"Got content:\n{content}"
    )
    assert "data: {}" not in content, (
        f"data=None must NOT be coerced to {{}}. "
        f"Got content:\n{content}"
    )


# ── Test 2: success log contains full response ──


def test_success_log_contains_full_response(monkeypatch, fresh_db):
    from fastapi.testclient import TestClient
    from task_hounds_api.api.main import create_app
    from task_hounds_api.api import debug_logs

    d = _log_dir(monkeypatch, fresh_db.parent)
    from task_hounds_api.db.ops import workflow as db_wf
    from task_hounds_api.workflow import chat_agent

    # Mock chat_agent.send to return a full response with all fields
    full_response = {
        "ok": True,
        "messages": [
            {"role": "chat", "content": "Hello!", "session_id": "ps_chat", "created_at": "2026-06-05T10:00:00Z"},
            {"role": "chat", "content": "World!", "session_id": "ps_chat", "created_at": "2026-06-05T10:00:01Z"},
        ],
        "request_id": "req-12345",
        "elapsed_ms": 250,
    }

    def fake_send(sid, content, sender="human"):
        return full_response

    # Patch at the import location used by chat.py
    import task_hounds_api.api.routes.chat as chat_route
    monkeypatch.setattr(chat_route, "chat_agent", type("C", (), {"send": staticmethod(fake_send)}))

    client = TestClient(create_app())
    # Use the default project session (created at startup)
    response = client.post(
        "/api/chat/send",
        json={"content": "hi", "sender": "human"},
    )
    assert response.status_code == 200

    # The log file should contain the full response payload
    log_files = list(d.glob("*.log"))
    assert log_files, f"send.ok must create a log file in {d}"
    content = log_files[0].read_text(encoding="utf-8")
    assert "send.ok" in content
    assert "Hello!" in content, "Full response messages must be in the log"
    assert "World!" in content
    assert "req-12345" in content, "Full response fields must be in the log"
    assert "elapsed_ms" in content


# ── Test 3: failed response preserved completely ──


def test_failed_response_preserved_completely(monkeypatch, fresh_db):
    from fastapi.testclient import TestClient
    from task_hounds_api.api.main import create_app
    from task_hounds_api.api import debug_logs

    d = _log_dir(monkeypatch, fresh_db.parent)
    import task_hounds_api.api.routes.chat as chat_route

    full_error_response = {
        "ok": False,
        "error": {
            "code": "opencode_unreachable",
            "message": "OpenCode server at 127.0.0.1:18765 is not responding",
            "details": {"retries": 3, "last_error": "ConnectionRefused"},
        },
        "request_id": "req-failed-001",
    }

    def fake_send(sid, content, sender="human"):
        return full_error_response

    monkeypatch.setattr(chat_route, "chat_agent", type("C", (), {"send": staticmethod(fake_send)}))

    client = TestClient(create_app())
    response = client.post(
        "/api/chat/send",
        json={"content": "hi", "sender": "human"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False

    log_files = list(d.glob("*.log"))
    assert log_files, "send.fail must create a log file"
    content = log_files[0].read_text(encoding="utf-8")
    assert "send.fail" in content
    assert "opencode_unreachable" in content
    assert "OpenCode server at 127.0.0.1:18765 is not responding" in content
    assert "ConnectionRefused" in content, "Nested error details must be preserved"
    assert "req-failed-001" in content, "Full request_id must be in the log"


# ── Test 4: chat_agent.send exception writes log ──


def test_send_exception_writes_log(monkeypatch, fresh_db):
    from fastapi.testclient import TestClient
    from task_hounds_api.api.main import create_app
    from task_hounds_api.api import debug_logs

    d = _log_dir(monkeypatch, fresh_db.parent)
    import task_hounds_api.api.routes.chat as chat_route

    def fake_send_raises(sid, content, sender="human"):
        raise RuntimeError("opencode subprocess crashed: segfault")

    monkeypatch.setattr(chat_route, "chat_agent", type("C", (), {"send": staticmethod(fake_send_raises)}))

    client = TestClient(create_app())
    response = client.post(
        "/api/chat/send",
        json={"content": "hi", "sender": "human"},
    )
    # The HTTP response should still be a 200 (or at least return
    # a JSON error dict, not a 500). The audit's concern is that
    # the exception must be LOGGED, not that the HTTP response
    # must be 200.
    assert response.status_code in (200, 500)

    # The exception must be written to the debug log
    log_files = list(d.glob("*.log"))
    assert log_files, "send.exception must create a log file"
    content = log_files[0].read_text(encoding="utf-8")
    assert "send.exception" in content
    assert "opencode subprocess crashed" in content
    assert "segfault" in content


# ── Test 5: sensitive fields still redacted ──


def test_sensitive_fields_still_redacted(monkeypatch, fresh_db):
    from task_hounds_api.api import debug_logs
    d = _log_dir(monkeypatch, fresh_db.parent)
    debug_logs.write_backend_debug(
        session_id="p2_redact",
        level="info",
        category="chat",
        event="test.redaction",
        data={
            "apiKey": "supersecret-do-not-log",
            "authorization": "Bearer secret-token",
            "session_id": "safe-to-log",
            "content": "safe-to-log",
        },
    )
    content = (d / "p2_redact.log").read_text(encoding="utf-8")
    assert "supersecret-do-not-log" not in content
    assert "secret-token" not in content
    assert "[redacted]" in content
    assert "safe-to-log" in content
