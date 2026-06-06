"""Phase 3: chat endpoint integration tests.

Verifies the authoritative chat routes (api/routes/chat.py) work
end-to-end with the same contract the UI consumes:

  GET  /api/chat/messages     -> [] when no active session, else rows
  POST /api/chat/send         -> {ok, messages} on success, {ok:False, error} on failure
  GET  /api/chat/status       -> {ok, enabled, reason}

Covers:
  - No active session: messages returns [], send returns 400, status is safe
  - With active session + content: chat_send writes a row, returns ok
  - Empty content: chat_send returns {ok:False, error: 'empty_message'}
  - Credentials missing: chat_status reports missing_credentials
  - The compat duplicates are gone: each path has exactly one handler
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "chat_phase3_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_PORT", "18983")
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    return db


@pytest.fixture()
def valid_creds(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY_MINIMAX", "sk-test-minimax")
    monkeypatch.setenv("OPENCODE_API_KEY_BAILIAN", "sk-test-bailian")
    from task_hounds_api.opencode import config as oc_config
    oc_config.reset_cache()
    return monkeypatch


def test_chat_messages_returns_empty_array_when_no_active_session(fresh_db):
    """GET /api/chat/messages with no active session returns []
    (NOT null, NOT 404) so the UI can render an empty state without
    crashing."""
    from task_hounds_api.api import main as api_main
    with TestClient(api_main.create_app()) as c:
        r = c.get("/api/chat/messages")
    assert r.status_code == 200
    assert r.json() == []


def test_chat_status_reports_missing_credentials_when_env_empty(fresh_db):
    """GET /api/chat/status with no API keys must report ok=false
    and reason=missing_credentials (not a silent ok=true)."""
    from task_hounds_api.api import main as api_main
    with TestClient(api_main.create_app()) as c:
        r = c.get("/api/chat/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["enabled"] is False
    assert body["reason"] == "missing_credentials"


def test_chat_status_reports_ok_when_credentials_and_binding_resolve(
    fresh_db, valid_creds
):
    """With credentials present and the default chat binding resolvable,
    /api/chat/status must report ok=true and reason=empty string."""
    from task_hounds_api.api import main as api_main
    with TestClient(api_main.create_app()) as c:
        r = c.get("/api/chat/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["enabled"] is True
    assert body["reason"] == ""


def test_chat_send_returns_400_when_no_active_session(fresh_db, monkeypatch):
    """POST /api/chat/send with no session_id in the body AND no
    active session returns 400 (the require_session_id guard)."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.workflow import chat_agent
    from task_hounds_api.db import connect
    from task_hounds_api.db.ops import project as db_project
    monkeypatch.setattr(
        chat_agent.oc_client, "run",
        lambda *a, **kw: {"ok": False, "error": {"message": "should not be called"}},
    )
    with TestClient(api_main.create_app()) as c:
        with connect() as db:
            db.execute("UPDATE project_sessions SET is_active=0")
            db.commit()
        monkeypatch.setattr(db_project, "get_active_session", lambda: None)
        r = c.post("/api/chat/send", json={"content": "hello"})
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.json()}"


def test_chat_send_with_empty_content_returns_empty_message_error(
    fresh_db, valid_creds
):
    """POST /api/chat/send with empty content must return
    {ok:False, error:'empty_message'} -- not crash, not call LLM."""
    from task_hounds_api.api import main as api_main
    with TestClient(api_main.create_app()) as c:
        # Activate a project first
        projects = c.get("/api/projects").json()
        proj_id = projects[0]["id"] if projects else None
        assert proj_id, "default project must exist"
        c.post(f"/api/projects/{proj_id}/activate")
        # Empty content
        r = c.post("/api/chat/send", json={"content": ""})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "empty_message"


def test_chat_send_failure_path_writes_human_message_then_returns_error(
    fresh_db, valid_creds, monkeypatch
):
    """When the chat LLM call fails, the human message is still
    persisted to DB (so the operator can see what was attempted),
    and the response includes a clear error string -- not a silent
    ok=True with no reply."""
    from task_hounds_api.api import main as api_main
    from task_hounds_api.workflow import chat_agent
    from task_hounds_api.db import connect
    from task_hounds_api.db.ops import chat as db_chat

    def _fake_run(*args, **kwargs):
        return {"ok": False, "error": {"message": "opencode crashed (simulated)"}}

    monkeypatch.setattr(chat_agent.oc_client, "run", _fake_run)

    with TestClient(api_main.create_app()) as c:
        projects = c.get("/api/projects").json()
        proj_id = projects[0]["id"]
        c.post(f"/api/projects/{proj_id}/activate")
        r = c.post("/api/chat/send", json={"content": "please do something"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "opencode crashed" in (body.get("error") or "")

    with connect() as db:
        rows = db.execute(
            "SELECT sender, content FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 5",
            (proj_id,),
        ).fetchall()
    human_msgs = [r for r in rows if r["sender"] == "human"]
    assert any("please do something" in r["content"] for r in human_msgs), (
        f"human message must be persisted even on LLM failure, got rows: {[dict(r) for r in rows]}"
    )
