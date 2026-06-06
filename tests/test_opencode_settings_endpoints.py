"""Tests for settings modal OpenCode listing endpoints."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


def test_opencode_settings_endpoints_match_frontend_shape(monkeypatch):
    from task_hounds_api.api import create_app

    monkeypatch.setattr(
        "task_hounds_api.opencode.client.list_agents",
        lambda host, port: [
            {"name": "Sisyphus - ultraworker", "mode": "primary"},
            {"name": "general", "mode": "subagent"},
        ],
    )

    with TestClient(create_app()) as client:
        agents = client.get("/api/opencode/agents").json()
        models = client.get("/api/opencode/models").json()
        available = client.get("/api/opencode/available-models").json()

    assert [a["id"] for a in agents][:2] == ["Sisyphus - ultraworker", "general"]
    assert any(m["id"] == "minimax-coding-plan/MiniMax-M2.7" for m in models["models"])
    assert any(m["id"] == "minimax-coding-plan/MiniMax-M2.7" for m in available["models"])
    assert "apiKey" not in str(models)
    assert "apiKey" not in str(available)
