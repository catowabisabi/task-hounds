"""api.routes.settings — settings.json read/write + active session overlay.

Settings is a simple JSON file at core/runtime/settings.json for
non-active-session fields (loop policy, timeouts, etc.).

Phase-11 (P0): the active project session is the single source of
truth in `project_sessions` (the DB). /api/settings and
/api/projects/active MUST agree, so we overlay the active session
fields from db_project.get_active_session() on top of the JSON file
at read time. The JSON file no longer owns active_project_session.
"""
from __future__ import annotations

import json
from pathlib import Path
from fastapi import APIRouter

from task_hounds_api.db import ROOT, DB_PATH
from task_hounds_api.db.ops import project as db_project
from task_hounds_api.api import schemas

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTINGS_PATH = ROOT / "core" / "runtime" / "settings.json"


def _read() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))


def _write(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _with_active_session(data: dict) -> dict:
    """Overlay the DB's active project session on top of the JSON
    settings. The JSON file no longer owns active_project_session —
    the DB is the single source of truth. Returns a NEW dict so
    callers cannot mutate the cached file contents."""
    active = db_project.get_active_session() or {}
    out = dict(data)
    out["active_project_session"] = active.get("id")
    out["project_session_id"] = active.get("id")
    out["workspace_path"] = active.get("workspace_path") or ""
    return out


@router.get("", response_model=schemas.SettingsOut)
def get_settings() -> dict:
    """Migration audit symbol 157: GET /api/settings returns the
    typed SettingsOut shape (extras allowed)."""
    return _with_active_session(_read())


@router.put("")
def update_settings(body: dict) -> dict:
    """P7 ids 224 + 225: legacy settings merge contract.

    The 0c44ba2 settings save filtered out None values before
    merging so an explicit {key:null} would not overwrite a
    stored value with None. The new code used `current.update(body)`
    which propagates Nones. The fix: drop None values before
    merging, preserving the legacy contract.
    """
    current = _read()
    # P7 id 224 + 225: filter out None values so a body of
    # {key: null} does not erase a stored value.
    filtered = {k: v for k, v in (body or {}).items() if v is not None}
    current.update(filtered)
    _write(current)
    return _with_active_session(current)


@router.post("")
def update_settings_post(body: dict) -> dict:
    """P7 ids 224 + 225: UI uses POST; same as PUT, with the
    legacy None-filter contract."""
    return update_settings(body)


@router.get("/database-info", response_model=schemas.DatabaseInfo)
def get_database_info() -> dict:
    """Return database location and related paths."""
    import os
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return {
        "power_teams_db": str(DB_PATH),
        "opencode_config_dir": str(ROOT / "core" / "runtime"),
        "xdg_config_home": xdg if xdg else None,
    }
