from __future__ import annotations

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = Path(os.environ.get("POWER_TEAMS_RUNTIME_DIR", str(ROOT / "core" / "runtime")))
MANAGED_OPENCODE_BIN = (
    RUNTIME_DIR
    / "opencode_runtime"
    / "node_modules"
    / "opencode-ai"
    / "bin"
    / ("opencode.exe" if os.name == "nt" else "opencode")
)


def _settings_value() -> str | None:
    path = RUNTIME_DIR / "settings.json"
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8-sig"))
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
    raise RuntimeError(f"configured OpenCode binary not found: {value}")


def find_opencode_bin(*, required: bool = False) -> str | None:
    explicit = _settings_value()
    if explicit:
        return _resolve_explicit(explicit)

    if MANAGED_OPENCODE_BIN.exists():
        return str(MANAGED_OPENCODE_BIN)

    if required:
        raise RuntimeError(
            "managed OpenCode binary not found. Run installation.cmd from the Task Hounds root."
        )
    return None
