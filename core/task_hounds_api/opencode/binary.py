"""opencode.binary — find the managed opencode.exe path.

Reads from core/runtime/settings.json["opencode_bin"]. If missing,
falls back to the known install location. Raises if still not found.

No fallback to system PATH. Task Hounds owns its own OpenCode.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from task_hounds_api.db import ROOT

SETTINGS_PATH = ROOT / "core" / "runtime" / "settings.json"
DEFAULT_BINARY = ROOT / "core" / "runtime" / "opencode_runtime" / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"


def find(required: bool = False) -> Path | None:
    """Return the path to opencode binary, or None if not yet installed."""
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
            bin_str = data.get("opencode_bin")
            if bin_str:
                p = Path(bin_str)
                if p.exists():
                    return p
        except Exception:
            pass
    if DEFAULT_BINARY.exists():
        return DEFAULT_BINARY
    if required:
        raise FileNotFoundError(
            f"opencode binary not found. Run installation.cmd first. "
            f"Searched: {SETTINGS_PATH} and {DEFAULT_BINARY}"
        )
    return None
