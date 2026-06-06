"""Tests for interactive Chat agent replies."""
from __future__ import annotations

import gc
import os
import sys
import tempfile
import time as time_mod
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def temp_db(monkeypatch):
    fd, db_path = tempfile.mkstemp(prefix="task_hounds_chat_", suffix=".db")
    os.close(fd)
    monkeypatch.setenv("POWER_TEAMS_DB", db_path)

    for name in list(sys.modules):
        if name == "task_hounds_api" or name.startswith("task_hounds_api."):
            sys.modules.pop(name, None)

    yield Path(db_path)

    for name in list(sys.modules):
        if name == "task_hounds_api" or name.startswith("task_hounds_api."):
            sys.modules.pop(name, None)
    gc.collect()

    for suffix in ("", "-wal", "-shm"):
        target = Path(db_path + suffix)
        for _ in range(5):
            try:
                target.unlink()
                break
            except FileNotFoundError:
                break
            except PermissionError:
                time_mod.sleep(0.1)


def test_chat_send_calls_llm_and_appends_reply(temp_db, monkeypatch, tmp_path):
    from task_hounds_api.api import create_app
    from task_hounds_api.db import init_db
    from task_hounds_api.db.ops import agent as db_agent
    from task_hounds_api.db.ops import project as db_project

    init_db()
    db_agent.seed_default_agents()
    db_project.create_session("ps_chat", str(tmp_path), "Chat test")

    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "output": {"text": "Hello from Chat."}}

    monkeypatch.setattr("task_hounds_api.workflow.chat_agent.oc_client.run", fake_run)

    with TestClient(create_app()) as client:
        response = client.post("/api/chat/send", json={"content": "hi"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert [m["sender"] for m in body["messages"]] == ["human", "chat"]
    assert body["messages"][-1]["content"] == "Hello from Chat."
    assert calls[0]["agent"] == "Sisyphus - ultraworker"
    assert calls[0]["model"] == "minimax-coding-plan/MiniMax-M2.7"
    assert "Human message:\nhi" in calls[0]["prompt"]

    chat = db_agent.get_agent("chat")
    assert chat["state"] == "idle"
    assert chat["current_step"] is None
