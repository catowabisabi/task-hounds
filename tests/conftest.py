"""Shared pytest fixtures for the test suite."""
from __future__ import annotations

import pytest


@pytest.fixture()
def valid_credentials(monkeypatch):
    """Set the OPENCODE_API_KEY_* env vars so validate_credentials()
    returns an empty list. Tests that need to exercise the
    missing-credentials path should not use this fixture (or should
    explicitly unset the env vars after)."""
    monkeypatch.setenv("OPENCODE_API_KEY_MINIMAX", "sk-test-minimax")
    monkeypatch.setenv("OPENCODE_API_KEY_BAILIAN", "sk-test-bailian")
    from task_hounds_api.opencode import config as oc_config
    oc_config.reset_cache()
    return monkeypatch
