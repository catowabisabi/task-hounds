from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = Path(os.environ.get("POWER_TEAMS_RUNTIME_DIR", str(ROOT / "core" / "runtime")))


def _settings_value() -> str | None:
    path = RUNTIME_DIR / "settings.json"
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("opencode_bin", "opencode_path", "opencode_binary_path", "opencode_cli_path"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    except Exception:
        return None
    return None


def _resolve_explicit(value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.exists():
        return str(candidate)
    found = shutil.which(value)
    if found:
        return found
    raise RuntimeError(f"configured OpenCode binary not found: {value}")


def find_opencode_bin(*, required: bool = False) -> str | None:
    explicit = (
        os.environ.get("POWER_TEAMS_OPENCODE_BIN")
        or os.environ.get("OPENCODE_BIN")
        or _settings_value()
    )
    if explicit:
        return _resolve_explicit(explicit)

    names = ("opencode.cmd", "opencode") if os.name == "nt" else ("opencode",)
    for name in names:
        found = shutil.which(name)
        if found:
            return found

    if required:
        raise RuntimeError(
            "opencode command not found. Install the npm global OpenCode CLI and ensure it is on PATH."
        )
    return None
