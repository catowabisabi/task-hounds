"""Regression tests for handoff updated_by metadata handling."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parents[2] / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "handoff_updated_by.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    for name in list(sys.modules):
        if name == "task_hounds_api" or name.startswith("task_hounds_api."):
            sys.modules.pop(name, None)
    from task_hounds_api.db import init_db
    from task_hounds_api.opencode import config as oc_config
    from task_hounds_api.opencode import runtime_manager as rm_mod

    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    init_db()
    return db


@pytest.fixture()
def seeded_project(fresh_db, tmp_path):
    from task_hounds_api.db.ops.project import activate_session, create_session

    sid = "ps_handoff_updated_by"
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    create_session(sid, str(ws), "Handoff Updated By")
    activate_session(sid)
    return sid


def test_upsert_handoff_treats_updated_by_as_metadata(seeded_project):
    from task_hounds_api.db.ops import workflow as db_wf

    db_wf.upsert_handoff(
        seeded_project,
        current_task="write tests",
        updated_by="reviewer",
    )

    handoff = db_wf.get_handoff(seeded_project)
    assert handoff is not None
    assert handoff["current_task"] == "write tests"
    assert handoff["updated_by"] == "reviewer"


def test_apply_handoff_update_allows_payload_updated_by(seeded_project):
    from task_hounds_api.db.ops import workflow as db_wf
    from task_hounds_api.workflow.repair import apply_handoff_update

    payload = {
        "current_task": "resume cleanly",
        "working_direction": "keep going",
        "updated_by": "llm_payload",
    }
    response = f"<HANDOFF_UPDATE>{json.dumps(payload)}</HANDOFF_UPDATE>"

    version = apply_handoff_update(
        response,
        updated_by="manager",
        project_session_id=seeded_project,
    )

    handoff = db_wf.get_handoff(seeded_project)
    assert version is not None
    assert handoff is not None
    assert handoff["current_task"] == "resume cleanly"
    assert handoff["working_direction"] == "keep going"
    assert handoff["updated_by"] == "manager"
