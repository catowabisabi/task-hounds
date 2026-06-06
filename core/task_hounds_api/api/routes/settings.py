"""api.routes.settings — settings.json read/write.

Settings is a simple JSON file at core/runtime/settings.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from fastapi import APIRouter

from task_hounds_api.db import ROOT

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTINGS_PATH = ROOT / "core" / "runtime" / "settings.json"


def _read() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))


def _write(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


@router.get("")
def get_settings() -> dict:
    return _read()


@router.put("")
def update_settings(body: dict) -> dict:
    current = _read()
    current.update(body)
    _write(current)
    return current


@router.post("")
def update_settings_post(body: dict) -> dict:
    """UI uses POST; same as PUT."""
    return update_settings(body)
