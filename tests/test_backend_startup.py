"""Tests for backend startup lifecycle (FastAPI lifespan).

Regression: previously, `create_app()` initialized DB and seeded defaults
but did NOT start, reconcile, or auto-bind the OpenCode server. After
this commit, the FastAPI `lifespan` context manager invokes the
RuntimeManager on startup (reconcile → ensure_managed_running →
auto_bind_four_roles) and stop_all on shutdown.
"""
from __future__ import annotations

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
def rm_mock(monkeypatch):
    """Replace RuntimeManager in api.main with a MagicMock so we can verify
    lifespan invokes reconcile, ensure, and auto_bind — without spawning
    a real `opencode serve` process during the test."""
    from task_hounds_api.api import main as api_main

    rm = MagicMock()
    rm.reconcile_servers.return_value = 0
    rm.ensure_managed_running.return_value = True
    rm.validate_credentials.return_value = []
    rm.get_managed_health.return_value = {
        "ok": True,
        "host": "127.0.0.1",
        "port": 18956,
        "pid": 77000,
    }
    rm.auto_bind_four_roles.return_value = 4
    rm.stop_all.return_value = {
        "ok": True,
        "stopped": True,
        "killed": {"opencode_runs": 0, "managed_servers": 0},
    }
    rm.instance.return_value = rm
    monkeypatch.setattr(api_main, "RuntimeManager", rm)
    return rm


def test_startup_lifespan_calls_reconcile_then_ensure_then_bind(
    rm_mock, monkeypatch, tmp_path
):
    """When the FastAPI app starts, lifespan calls reconcile, ensure, bind."""
    db = tmp_path / "lifespan_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))

    from task_hounds_api.api import create_app

    with TestClient(create_app()) as c:
        ping = c.get("/api/ping")
        assert ping.status_code == 200

    rm_mock.reconcile_servers.assert_called_once()
    rm_mock.ensure_managed_running.assert_called_once()
    rm_mock.validate_credentials.assert_called()
    rm_mock.auto_bind_four_roles.assert_called_once()


def test_shutdown_lifespan_calls_stop_all(rm_mock, monkeypatch, tmp_path):
    """When the FastAPI app shuts down, lifespan calls stop_all on the manager."""
    db = tmp_path / "lifespan_test_shutdown.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))

    from task_hounds_api.api import create_app

    with TestClient(create_app()):
        pass

    rm_mock.stop_all.assert_called_once()


def test_startup_failure_does_not_crash_app(rm_mock, monkeypatch, tmp_path):
    """If ensure_managed_running raises, the app still serves /api/ping."""
    rm_mock.ensure_managed_running.side_effect = RuntimeError("opencode not found")

    db = tmp_path / "lifespan_test_fail.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))

    from task_hounds_api.api import create_app

    with TestClient(create_app()) as c:
        resp = c.get("/api/ping")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
