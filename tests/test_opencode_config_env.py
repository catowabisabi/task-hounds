"""Tests for opencode config: ${ENV_VAR} placeholder expansion in apiKey.

Regression for the security issue where plaintext API keys were committed
to core/runtime/opencode_config/opencode.jsonc. After this commit, the
config file uses ${ENV_VAR} placeholders and the loader expands them
at read time. If the env var is missing, the apiKey becomes empty
string (the OpenCode CLI will then fail with an auth error, which is
the desired behavior — not a silent fake success).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


def _write_cfg(path: Path, api_key_value: str) -> None:
    payload = {
        "provider": {
            "test-provider": {
                "options": {"apiKey": api_key_value},
                "models": {"m1": {"name": "M1"}},
            }
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_api_key_env_var_is_expanded(tmp_path, monkeypatch):
    """When apiKey is ${ENV_VAR}, load() expands to the env value."""
    cfg_file = tmp_path / "opencode.jsonc"
    _write_cfg(cfg_file, "${TEST_API_KEY}")
    monkeypatch.setenv("TEST_API_KEY", "sk-test-real-key")

    from task_hounds_api.opencode import config as oc_config
    oc_config.reset_cache()
    cfg = oc_config.load(cfg_file)
    assert (
        cfg["provider"]["test-provider"]["options"]["apiKey"]
        == "sk-test-real-key"
    )


def test_api_key_env_var_missing_becomes_empty(tmp_path, monkeypatch):
    """When env var is missing, apiKey becomes empty string. No exception."""
    cfg_file = tmp_path / "opencode.jsonc"
    _write_cfg(cfg_file, "${TEST_API_KEY_MISSING}")
    monkeypatch.delenv("TEST_API_KEY_MISSING", raising=False)

    from task_hounds_api.opencode import config as oc_config
    oc_config.reset_cache()
    cfg = oc_config.load(cfg_file)
    assert (
        cfg["provider"]["test-provider"]["options"]["apiKey"] == ""
    )


def test_api_key_plaintext_passthrough(tmp_path):
    """When apiKey is plaintext (no ${...}), it passes through unchanged.

    This preserves backwards compatibility for setups that still have
    plaintext keys, while the placeholder syntax is the recommended
    path going forward.
    """
    cfg_file = tmp_path / "opencode.jsonc"
    _write_cfg(cfg_file, "sk-plaintext-keep")

    from task_hounds_api.opencode import config as oc_config
    oc_config.reset_cache()
    cfg = oc_config.load(cfg_file)
    assert (
        cfg["provider"]["test-provider"]["options"]["apiKey"]
        == "sk-plaintext-keep"
    )


def test_real_opencode_config_has_no_plaintext_keys():
    """core/runtime/opencode_config/opencode.jsonc must not contain any
    plaintext API key (anything starting with `sk-`).

    This is a hard guard against accidental regression. If a developer
    commits a new plaintext key, CI will fail here.
    """
    from task_hounds_api.opencode import config as oc_config
    from task_hounds_api.db import ROOT
    real_path = ROOT / "core" / "runtime" / "opencode_config" / "opencode.jsonc"
    if not real_path.exists():
        pytest.skip("opencode.jsonc not present in this environment")

    oc_config.reset_cache()
    cfg = oc_config.load(real_path)

    plaintext_leaks: list[tuple[str, str]] = []
    for provider_id, provider in (cfg.get("provider") or {}).items():
        opts = provider.get("options") or {}
        api_key = opts.get("apiKey") or ""
        if api_key.startswith("sk-"):
            plaintext_leaks.append((provider_id, api_key))

    assert not plaintext_leaks, (
        f"plaintext API keys found in opencode.jsonc: {plaintext_leaks}"
    )
