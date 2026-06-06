"""Tests for directive failure lifecycle on OpenCode errors.

Issue 4 (P0-A): when the opencode subprocess exits non-zero, times
out, or raises, the directive must transition to `failed` quickly
(well under the 30s e2e budget). The pre-flight credential check
makes the missing-credentials path fast (<1s), but we also need
coverage for the post-spawn failure path (real opencode returns
non-zero or raises).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "directive_fail_test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    monkeypatch.setenv("TASK_HOUNDS_OPENCODE_PORT", "18991")
    from task_hounds_api.opencode import runtime_manager as rm_mod
    from task_hounds_api.opencode import config as oc_config
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    from task_hounds_api.db import init_db
    init_db()
    return db


@pytest.fixture()
def valid_credentials(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY_MINIMAX", "sk-test-minimax")
    monkeypatch.setenv("OPENCODE_API_KEY_BAILIAN", "sk-test-bailian")
    from task_hounds_api.opencode import config as oc_config
    oc_config.reset_cache()
    return monkeypatch


def test_directive_fails_fast_on_opencode_exit_1(
    fresh_db, monkeypatch, valid_credentials
):
    """When the Manager OpenCode subprocess exits with code 1, the
    BackgroundLoop must mark the directive as `failed` and record the
    error message — within seconds, not minutes.

    Reproduces the user-reported regression: "Manager OpenCode call exit 1
    時, directive 必須快速轉為 failed 並記錄 error, 不可超過 E2E 30 秒仍維持
    running/pending"."""
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.workflow import executor as exec_mod
    from task_hounds_api.db.ops import project as db_project
    from task_hounds_api.db.ops import chat as db_chat
    from task_hounds_api.db import connect

    db_project.create_session("ps_fail", workspace_path=".", name="fail-test")
    db_project.activate_session("ps_fail")
    did = db_chat.create_directive("ps_fail", "trigger failure")

    real_oc_run = exec_mod.oc_client.run

    def fake_run(**kwargs):
        return {
            "ok": False,
            "error": {
                "type": "RuntimeError",
                "message": "opencode run exited 1: stderr=auth failed",
            },
            "output": {"text": ""},
        }

    monkeypatch.setattr(exec_mod.oc_client, "run", fake_run)

    bg = loop_mod.BackgroundLoop(interval=60)
    bg.start()
    deadline = time.monotonic() + 15.0
    final_status = None
    error_text = None
    while time.monotonic() < deadline:
        with connect() as db:
            row = db.execute(
                "SELECT status, error FROM user_directives WHERE id=?",
                (did,),
            ).fetchone()
        if row and row["status"] in ("processed", "failed"):
            final_status = row["status"]
            error_text = row["error"]
            break
        time.sleep(0.2)
    bg.stop()
    bg._thread.join(timeout=2)

    assert final_status == "failed", (
        f"directive did not transition to 'failed' within 15s; final_status={final_status}"
    )
    assert error_text and ("exit" in error_text.lower() or "failed" in error_text.lower()), (
        f"directive error not recorded or missing failure detail: {error_text!r}"
    )
    monkeypatch.setattr(exec_mod.oc_client, "run", real_oc_run)


def test_directive_fails_fast_on_opencode_timeout(
    fresh_db, monkeypatch, valid_credentials
):
    """When the Manager OpenCode call times out, the directive must
    transition to `failed` with a timeout error."""
    from task_hounds_api.workflow import loop as loop_mod
    from task_hounds_api.workflow import executor as exec_mod
    from task_hounds_api.db.ops import project as db_project
    from task_hounds_api.db.ops import chat as db_chat
    from task_hounds_api.db import connect

    db_project.create_session("ps_to", workspace_path=".", name="to-test")
    db_project.activate_session("ps_to")
    did = db_chat.create_directive("ps_to", "trigger timeout")

    def fake_run(**kwargs):
        return {
            "ok": False,
            "error": {"type": "TimeoutError", "message": "opencode run timed out after 300s"},
            "output": {"text": ""},
        }

    monkeypatch.setattr(exec_mod.oc_client, "run", fake_run)

    bg = loop_mod.BackgroundLoop(interval=60)
    bg.start()
    deadline = time.monotonic() + 15.0
    final_status = None
    error_text = None
    while time.monotonic() < deadline:
        with connect() as db:
            row = db.execute(
                "SELECT status, error FROM user_directives WHERE id=?",
                (did,),
            ).fetchone()
        if row and row["status"] in ("processed", "failed"):
            final_status = row["status"]
            error_text = row["error"]
            break
        time.sleep(0.2)
    bg.stop()
    bg._thread.join(timeout=2)

    assert final_status == "failed", (
        f"directive did not transition to 'failed' within 15s on timeout; final_status={final_status}"
    )
    assert error_text, f"error empty: {error_text!r}"
