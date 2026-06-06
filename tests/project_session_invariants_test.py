"""Regression tests for project session invariants."""
from __future__ import annotations

import os
import sys
import tempfile
import gc
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def temp_db(monkeypatch):
    fd, db_path = tempfile.mkstemp(prefix="task_hounds_project_", suffix=".db")
    os.close(fd)
    monkeypatch.setenv("POWER_TEAMS_DB", db_path)

    # Modules cache DB_PATH at import time, so reload the small DB layer after
    # changing POWER_TEAMS_DB.
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
                time.sleep(0.1)


def test_create_session_keeps_single_active_session(temp_db):
    from task_hounds_api.db import connect, init_db
    from task_hounds_api.db.ops import project

    init_db()
    project.create_session("ps_one", tempfile.gettempdir(), "one")
    project.create_session("ps_two", str(Path(tempfile.gettempdir()).resolve()), "two")

    with connect() as db:
        active = [dict(r) for r in db.execute("SELECT id FROM project_sessions WHERE is_active=1")]

    assert active == [{"id": "ps_two"}]


def test_create_session_reactivates_existing_session(temp_db):
    from task_hounds_api.db import connect, init_db
    from task_hounds_api.db.ops import project

    init_db()
    project.create_session("ps_one", tempfile.gettempdir(), "one")
    project.create_session("ps_two", tempfile.gettempdir(), "two")
    project.create_session("ps_one", tempfile.gettempdir(), "one again")

    with connect() as db:
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT id, name, is_active FROM project_sessions ORDER BY id"
            )
        ]

    assert rows == [
        {"id": "ps_one", "name": "one again", "is_active": 1},
        {"id": "ps_two", "name": "two", "is_active": 0},
    ]


def test_db_update_session_ignores_unknown_fields(temp_db):
    from task_hounds_api.db import connect, init_db
    from task_hounds_api.db.ops import project

    init_db()
    project.create_session("ps_one", tempfile.gettempdir(), "one")

    project.update_session("ps_one", **{"name=?, is_active=0 --": "boom"})

    with connect() as db:
        row = dict(db.execute("SELECT name, is_active FROM project_sessions WHERE id='ps_one'").fetchone())

    assert row == {"name": "one", "is_active": 1}


def test_project_patch_schema_rejects_unknown_fields(temp_db):
    from task_hounds_api.api import create_app

    app = create_app()
    with TestClient(app) as client:
        active = client.get("/api/projects/active").json()
        session_id = active["id"]

        bad = client.patch(
            f"/api/projects/{session_id}",
            json={"name=?, is_active=0 --": "boom"},
        )
        assert bad.status_code == 422

        ok = client.patch(f"/api/projects/{session_id}", json={"name": "Renamed"})
        assert ok.status_code == 200
        assert ok.json()["label"] == "Renamed"
