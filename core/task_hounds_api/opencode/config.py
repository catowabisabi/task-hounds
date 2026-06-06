"""opencode.config — read ONE opencode.jsonc, no fallback.

Reads from core/runtime/opencode_config/opencode.jsonc (the one
installation.cmd wrote). Raises if missing. No fallbacks.

Security: apiKey fields that use the syntax `${ENV_VAR_NAME}` are
expanded from the process environment at load time. If the env var
is missing the field becomes an empty string. Plaintext values
pass through unchanged for backwards compatibility, but the
recommended path is to use env var placeholders so credentials
never get committed to source control.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from task_hounds_api.db import ROOT

CONFIG_DIR = ROOT / "core" / "runtime" / "opencode_config"
CONFIG_PATH = CONFIG_DIR / "opencode.jsonc"

_cache: dict | None = None

_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")


def _strip_jsonc(raw: str) -> str:
    """Remove // and /* */ comments from a JSONC string."""
    out, i, n = [], 0, len(raw)
    in_str = False
    quote = ""
    while i < n:
        c = raw[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(raw[i + 1])
                i += 2
                continue
            if c == quote:
                in_str = False
            i += 1
            continue
        if c in ('"', "'"):
            in_str = True
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and raw[i + 1] == "/":
            while i < n and raw[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and raw[i + 1] == "*":
            i += 2
            while i + 1 < n and not (raw[i] == "*" and raw[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _expand_env(value):
    """Recursively expand ${ENV_VAR} placeholders in parsed config values.

    Only exact-match placeholders (entire string is `${VAR}`) are expanded,
    so URLs and other strings containing `${...}` are left untouched.
    Missing env vars become empty string; the OpenCode CLI will then fail
    with an auth error which is the desired behavior — no silent fake
    success.
    """
    if isinstance(value, str):
        m = _PLACEHOLDER_RE.match(value)
        if m:
            return os.environ.get(m.group(1), "")
        return value
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load(path: Path | None = None) -> dict:
    """Load and parse opencode.jsonc. Returns the dict. Raises if missing or invalid."""
    p = Path(path) if path is not None else CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"opencode config not found at {p}. Run installation.cmd to set up Task Hounds."
        )
    raw = p.read_text(encoding="utf-8-sig")
    cleaned = _strip_jsonc(raw)
    parsed = json.loads(cleaned)
    return _expand_env(parsed)


def get(path: Path | None = None) -> dict:
    """Cached load."""
    global _cache
    if _cache is None:
        _cache = load(path)
    return _cache


def reset_cache() -> None:
    """Drop the cache so the next get() re-reads from disk."""
    global _cache
    _cache = None


def generate_runtime_config(
    template_path: Path | None = None,
    runtime_dir: Path | None = None,
) -> Path:
    """Read the template opencode.jsonc, expand ${ENV_VAR} placeholders,
    and write the result to a runtime-only directory. Returns the
    directory containing the expanded file.

    Why: the opencode CLI parses the config file directly and does not
    expand ${ENV_VAR} placeholders itself. Our Python code reads the
    expanded form for listing models, but the subprocess we spawn
    must be given a file with concrete values, not placeholders.
    """
    src = Path(template_path) if template_path is not None else CONFIG_PATH
    out_dir = (
        Path(runtime_dir)
        if runtime_dir is not None
        else (ROOT / "core" / "runtime" / "opencode_config_runtime")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "opencode.jsonc"

    parsed = load(src)
    out_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    return out_dir


def list_providers(path: Path | None = None) -> dict[str, dict]:
    """Return {provider_id: {models: {...}, ...}, ...}."""
    cfg = get(path)
    return cfg.get("provider", {})


def model_supports_thinking(model_id: str, path: Path | None = None) -> bool:
    """True if the model entry has options.thinking.type == 'enabled'."""
    if not model_id:
        return False
    cfg = get(path)
    for provider_id, provider in cfg.get("provider", {}).items():
        models = provider.get("models", {})
        if model_id in models:
            opts = models[model_id].get("options", {}) or {}
            thinking = opts.get("thinking", {}) or {}
            return thinking.get("type") == "enabled"
        # Also check provider_id/model_id form
        if "/" in model_id:
            pid, mid = model_id.split("/", 1)
            if pid == provider_id and mid in models:
                opts = models[mid].get("options", {}) or {}
                thinking = opts.get("thinking", {}) or {}
                return thinking.get("type") == "enabled"
    return False


def is_model_available(model_id: str, path: Path | None = None) -> bool:
    """True if model_id (with or without provider prefix) is in the config."""
    if not model_id:
        return False
    cfg = get(path)
    providers = cfg.get("provider", {})
    if model_id in providers:
        return True
    if "/" in model_id:
        pid, mid = model_id.split("/", 1)
        if pid in providers and mid in providers[pid].get("models", {}):
            return True
    for provider in providers.values():
        if model_id in provider.get("models", {}):
            return True
    return False
