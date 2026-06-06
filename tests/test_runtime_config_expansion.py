"""Tests for runtime config expansion (Issue 8 from P0-A review).

The opencode CLI reads the config file DIRECTLY. Our Python-side
${ENV_VAR} expansion in opencode.config.load() is for the Python
layer only — the CLI itself does not expand placeholders. So the
managed opencode serve process must be spawned with a runtime-only
config that has the placeholders already expanded.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def template_cfg(tmp_path, monkeypatch):
    """Create a template opencode.jsonc with ${ENV_VAR} placeholders."""
    cfg_dir = tmp_path / "opencode_config"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "opencode.jsonc"
    cfg_path.write_text(json.dumps({
        "provider": {
            "test-provider": {
                "options": {
                    "baseURL": "https://example.invalid",
                    "apiKey": "${TASK_HOUNDS_TEST_API_KEY}",
                },
                "models": {"m1": {"name": "M1"}},
            }
        }
    }), encoding="utf-8")
    monkeypatch.setenv("TASK_HOUNDS_TEST_API_KEY", "expanded-secret-value")
    return cfg_path


def test_generate_runtime_config_writes_expanded_file(template_cfg, monkeypatch):
    """generate_runtime_config reads template, expands env vars, writes
    the result to a runtime-only file, and returns the runtime dir."""
    from task_hounds_api.opencode.config import generate_runtime_config
    from task_hounds_api.db import ROOT

    runtime_dir = generate_runtime_config(template_cfg)
    runtime_path = runtime_dir / "opencode.jsonc"
    assert runtime_path.exists(), "runtime config not written"

    parsed = json.loads(runtime_path.read_text(encoding="utf-8"))
    api_key = parsed["provider"]["test-provider"]["options"]["apiKey"]
    assert api_key == "expanded-secret-value", (
        f"runtime config not expanded; got apiKey={api_key!r}"
    )


def test_spawn_env_uses_runtime_config_dir(template_cfg, monkeypatch):
    """opencode.process._isolated_env must set OPENCODE_CONFIG_DIR to the
    runtime-generated directory, NOT the template directory."""
    from task_hounds_api.opencode.config import generate_runtime_config
    from task_hounds_api.opencode.process import _isolated_env

    runtime_dir = generate_runtime_config(template_cfg)
    env = _isolated_env()
    assert env.get("OPENCODE_CONFIG_DIR") == str(runtime_dir), (
        f"OPENCODE_CONFIG_DIR not pointing to runtime dir; got {env.get('OPENCODE_CONFIG_DIR')!r}"
    )
