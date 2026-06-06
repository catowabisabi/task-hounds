"""Phase 7 (Blocker 6): Chat debug logging tests.

Asserts that api/routes/chat.py routes persist their events to the
per-session debug log file (via write_backend_debug) instead of
going only to the Python logger. A silent chat failure used to leave
no operator-visible trace; these tests prove the trace is now in
ui/debug/logs/<session>.log and can be read with tools/debug_log_writer.
"""
from __future__ import annotations

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
    db = tmp_path / "phase7_chat_logging.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    return db


def _seed_project_session(db: Path, sid: str) -> None:
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO project_sessions (id, name, is_active) "
            "VALUES (?, ?, 1)",
            (sid, sid + "_name"),
        )
        c.commit()


def _log_dir(monkeypatch, tmp_path) -> Path:
    d = tmp_path / "ui_debug_logs"
    monkeypatch.setattr(
        "task_hounds_api.api.debug_logs.LOG_DIR", d
    )
    return d


# ── write_backend_debug helper unit tests ──────────────────────────────────


def test_write_backend_debug_writes_per_session_file(monkeypatch, tmp_path):
    from task_hounds_api.api import debug_logs

    d = tmp_path / "logs"
    monkeypatch.setattr(debug_logs, "LOG_DIR", d)

    result = debug_logs.write_backend_debug(
        session_id="chat_unit_1",
        level="info",
        category="chat",
        event="send.ok",
        data={"msgs": 2, "elapsed_ms": 120},
    )
    assert result["ok"] is True
    assert result["received"] == 1
    path = d / "chat_unit_1.log"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "session_id: chat_unit_1" in content
    assert "send.ok" in content
    assert "msgs: 2" in content
    assert "elapsed_ms: 120" in content


def test_write_backend_debug_redacts_secrets(monkeypatch, tmp_path):
    from task_hounds_api.api import debug_logs

    d = tmp_path / "logs"
    monkeypatch.setattr(debug_logs, "LOG_DIR", d)

    debug_logs.write_backend_debug(
        session_id="chat_unit_2",
        level="info",
        category="chat",
        event="send.ok",
        data={"apiKey": "supersecret-do-not-log", "msgs": 1},
    )
    content = (d / "chat_unit_2.log").read_text(encoding="utf-8")
    assert "supersecret-do-not-log" not in content
    assert "[redacted]" in content


def test_write_backend_debug_falls_back_to_backend_session(monkeypatch, tmp_path):
    from task_hounds_api.api import debug_logs

    d = tmp_path / "logs"
    monkeypatch.setattr(debug_logs, "LOG_DIR", d)

    result = debug_logs.write_backend_debug(
        session_id=None,
        level="warning",
        category="chat",
        event="status.binding_unresolved",
    )
    assert result["session_id"] == "backend"
    assert (d / "backend.log").exists()


# ── chat.py route integration: list_messages ───────────────────────────────


def test_list_messages_writes_log_when_no_active_session(monkeypatch, fresh_db, tmp_path):
    """The server auto-creates a default project at startup, so in
    practice list_messages always has an active session. The log
    file is written for that default session with the
    list_messages.ok event. This test asserts that the log file
    is created on every list_messages call (success path)."""
    from fastapi.testclient import TestClient
    from task_hounds_api.api.main import create_app

    d = _log_dir(monkeypatch, tmp_path)

    client = TestClient(create_app())
    response = client.get("/api/chat/messages")
    assert response.status_code == 200
    assert response.json() == []

    log_files = list(d.glob("*.log"))
    assert log_files, f"list_messages must create a log file in {d}"
    content = log_files[0].read_text(encoding="utf-8")
    assert "list_messages.ok" in content
    assert "row_count: 0" in content


def test_list_messages_writes_log_with_session_id(monkeypatch, fresh_db, tmp_path):
    from task_hounds_api.api.routes import chat as chat_route
    from fastapi.testclient import TestClient
    from task_hounds_api.api.main import create_app
    from task_hounds_api.api import debug_logs

    d = _log_dir(monkeypatch, tmp_path)
    _seed_project_session(fresh_db, "ps_chat_log")

    client = TestClient(create_app())
    response = client.get("/api/chat/messages?session_id=ps_chat_log")
    assert response.status_code == 200
    assert response.json() == []

    path = d / "ps_chat_log.log"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "list_messages.ok" in content
    assert "row_count: 0" in content


# ── chat.py route integration: send ───────────────────────────────────────


def test_send_writes_log_on_success(monkeypatch, fresh_db, tmp_path):
    from fastapi.testclient import TestClient
    from task_hounds_api.api.main import create_app
    from task_hounds_api.api import debug_logs
    from task_hounds_api.workflow import chat_agent

    d = _log_dir(monkeypatch, tmp_path)
    _seed_project_session(fresh_db, "ps_chat_send")

    def fake_send(sid, content, sender="human"):
        return {"ok": True, "messages": [{"role": "chat", "content": "hi"}]}

    monkeypatch.setattr(chat_agent, "send", fake_send)

    client = TestClient(create_app())
    response = client.post(
        "/api/chat/send",
        json={"session_id": "ps_chat_send", "content": "hello", "sender": "human"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True

    path = d / "ps_chat_send.log"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "send.ok" in content
    # Phase-8 (P2): the log must contain the FULL request and
    # FULL response, not just a summary. Operators replay any
    # chat interaction from the log.
    assert "hello" in content, "request.content must be in the log"
    assert "ps_chat_send" in content, "request.session_id must be in the log"
    assert "sender: human" in content or '"sender": "human"' in content
    assert '"role": "chat"' in content or "role: chat" in content, (
        "Full response messages must be in the log"
    )


def test_send_writes_log_on_failure(monkeypatch, fresh_db, tmp_path):
    from fastapi.testclient import TestClient
    from task_hounds_api.api.main import create_app
    from task_hounds_api.api import debug_logs
    from task_hounds_api.workflow import chat_agent

    d = _log_dir(monkeypatch, tmp_path)
    _seed_project_session(fresh_db, "ps_chat_fail")

    def fake_send(sid, content, sender="human"):
        return {"ok": False, "error": "chat agent unreachable"}

    monkeypatch.setattr(chat_agent, "send", fake_send)

    client = TestClient(create_app())
    response = client.post(
        "/api/chat/send",
        json={"session_id": "ps_chat_fail", "content": "hello"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False

    path = d / "ps_chat_fail.log"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "send.fail" in content
    assert "ERROR" in content
    assert "chat agent unreachable" in content


# ── chat.py route integration: status ──────────────────────────────────────


def test_status_writes_log_on_binding_failure(monkeypatch, fresh_db, tmp_path):
    from fastapi.testclient import TestClient
    from task_hounds_api.api.main import create_app
    from task_hounds_api.api import debug_logs
    from task_hounds_api.opencode import binding_resolver

    d = _log_dir(monkeypatch, tmp_path)

    def boom(role):
        raise RuntimeError("binding broken")

    monkeypatch.setattr(binding_resolver, "resolve_for_role", boom)
    monkeypatch.setattr(
        "task_hounds_api.opencode.runtime_manager.RuntimeManager.instance",
        lambda: type("RM", (), {"validate_credentials": staticmethod(lambda: [])})(),
    )

    client = TestClient(create_app())
    response = client.get("/api/chat/status")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["reason"] == "binding_unresolved"

    path = d / "backend.log"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "status.binding_unresolved" in content
    assert "binding broken" in content
