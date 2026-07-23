"""Tests for the API security request guard in api.main.create_app:

  1. localhost/testserver requests pass with no configuration
  2. a non-allowlisted browser Origin is rejected (CSRF defense)
  3. a non-allowlisted Host is rejected (DNS-rebinding defense)
  4. allowlisted dev origins (vite on :5173) pass
  5. when API_SECRET_KEY is set, /api requests require X-API-Key,
     except /api/health (used by startup scripts before auth is known)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parents[2] / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def guard_client(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "guard_test.db"))
    monkeypatch.delenv("API_SECRET_KEY", raising=False)
    from starlette.testclient import TestClient
    from task_hounds_api.api.main import create_app

    return TestClient(create_app())


@pytest.fixture()
def keyed_client(tmp_path, monkeypatch):
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "guard_key_test.db"))
    monkeypatch.setenv("API_SECRET_KEY", "test-secret-key")
    from starlette.testclient import TestClient
    from task_hounds_api.api.main import create_app

    return TestClient(create_app())


def test_localhost_request_passes(guard_client):
    assert guard_client.get("/api/ping").status_code == 200


def test_foreign_origin_rejected(guard_client):
    r = guard_client.get("/api/ping", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_foreign_host_rejected(guard_client):
    r = guard_client.get("/api/ping", headers={"Host": "attacker.example"})
    assert r.status_code == 403


def test_vite_dev_origin_passes(guard_client):
    r = guard_client.get("/api/ping", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200


def test_api_key_required_when_configured(keyed_client):
    assert keyed_client.get("/api/ping").status_code == 401
    r = keyed_client.get("/api/ping", headers={"X-API-Key": "test-secret-key"})
    assert r.status_code == 200


def test_health_exempt_from_api_key(keyed_client):
    assert keyed_client.get("/api/health").status_code == 200


def test_placeholder_api_key_not_enforced(tmp_path, monkeypatch):
    """The historical .env.example placeholder must not lock anyone out."""
    monkeypatch.setenv("POWER_TEAMS_DB", str(tmp_path / "guard_ph_test.db"))
    monkeypatch.setenv("API_SECRET_KEY", "your_api_secret_key_here")
    from starlette.testclient import TestClient
    from task_hounds_api.api.main import create_app

    client = TestClient(create_app())
    assert client.get("/api/ping").status_code == 200
