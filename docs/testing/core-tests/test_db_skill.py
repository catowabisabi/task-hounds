"""
test_db_skill.py — Tests for Task Hounds DB Skill v1
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest import mock
import sys as _sys
_ROOT = Path(__file__).resolve().parents[2]
_core_path = str(_ROOT / "core")
if _core_path not in _sys.path:
    _sys.path.insert(0, _core_path)
del _sys, _ROOT, _core_path

SKILL_MODULE = "core.power_teams.skills.db_skill"


def _sqlite_rows(rows: list[dict]) -> list[sqlite3.Row]:
    def dict_to_row(d: dict):
        return d
    return [d for d in rows]


class _FakeRow(dict):
    def __init__(self, d: dict):
        super().__init__(d)
        for k, v in d.items():
            setattr(self, k, v)


class TestValidateIdentity:
    def test_accepts_correct_format(self):
        from power_teams.skills.db_skill import validate_identity
        ok, err = validate_identity("abc123", "manager", "abc123:manager")
        assert ok is True
        assert err == ""

    def test_rejects_mismatched_role(self):
        from power_teams.skills.db_skill import validate_identity
        ok, err = validate_identity("abc123", "worker", "abc123:manager")
        assert ok is False
        assert "Identity mismatch" in err

    def test_rejects_wrong_project(self):
        from power_teams.skills.db_skill import validate_identity
        ok, err = validate_identity("abc123", "manager", "xyz789:manager")
        assert ok is False


class TestReadableTables:
    def test_allowed_tables(self):
        from power_teams.skills.db_skill import READABLE_TABLES
        assert "suggestion_queue" in READABLE_TABLES
        assert "project_handoff" in READABLE_TABLES
        assert "manager_messages" in READABLE_TABLES

    def test_denies_disallowed_table(self):
        from power_teams.skills.db_skill import validate_identity, READABLE_TABLES
        assert "passwords" not in READABLE_TABLES


class TestWriteOpsAllowlist:
    def test_manager_ops(self):
        from power_teams.skills.db_skill import WRITE_OPS
        assert "append_manager_message" in WRITE_OPS["manager"]
        assert "create_suggestion" in WRITE_OPS["manager"]

    def test_worker_cannot_append_manager_message(self):
        from power_teams.skills.db_skill import WRITE_OPS
        assert "append_manager_message" not in WRITE_OPS["worker"]

    def test_worker_can_append_worker_report(self):
        from power_teams.skills.db_skill import WRITE_OPS
        assert "append_worker_report" in WRITE_OPS["worker"]


class TestCliSuccess:
    def test_validate_correct_identity(self, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        from power_teams.skills import db_tool
        rc = db_tool.main([
            "validate",
            "--project-session-id", "abc123",
            "--role", "manager",
            "--role-session-id", "abc123:manager",
        ])
        assert rc == 0

    def test_validate_wrong_identity(self, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        from power_teams.skills import db_tool
        rc = db_tool.main([
            "validate",
            "--project-session-id", "abc123",
            "--role", "manager",
            "--role-session-id", "abc123:worker",
        ])
        assert rc == 0


class TestCliJsonOutput:
    def test_success_output_has_ok_true(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        from power_teams.skills import db_tool
        db_tool.main([
            "validate",
            "--project-session-id", "abc123",
            "--role", "manager",
            "--role-session-id", "abc123:manager",
        ])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["ok"] is True

    def test_failure_output_has_ok_false(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        from power_teams.skills import db_tool
        db_tool.main([
            "validate",
            "--project-session-id", "abc123",
            "--role", "manager",
            "--role-session-id", "wrong:worker",
        ])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["ok"] is False
        assert "error" in data

    def test_no_stack_trace_on_error(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        from power_teams.skills import db_tool
        db_tool.main([
            "validate",
            "--project-session-id", "abc123",
            "--role", "manager",
            "--role-session-id", "bad:format",
        ])
        out = capsys.readouterr().out
        assert "Traceback" not in out


class TestReadTableSessionScope:
    def test_read_table_auto_scopes_to_session(self, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        from power_teams.skills.db_skill import read_table
        with mock.patch("power_teams.skills.db_skill._connect") as mock_conn:
            mock_db = mock.MagicMock()
            mock_conn.return_value.__enter__ = mock.MagicMock(return_value=mock_db)
            mock_conn.return_value.__exit__ = mock.MagicMock(return_value=False)
            mock_db.execute.return_value.fetchall.return_value = []
            read_table("ps1", "manager", "ps1:manager", "suggestion_queue")
            call_args = mock_db.execute.call_args[0]
            assert "session_id=?" in call_args[0]
            assert "ps1" in call_args[1]


class TestWriteOperationRoleEnforcement:
    def test_worker_cannot_run_manager_op(self, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        from power_teams.skills.db_skill import write_operation
        result = write_operation(
            project_session_id="abc123",
            role="worker",
            role_session_id="abc123:worker",
            operation="append_manager_message",
            payload={"content": "test"},
        )
        assert result["ok"] is False
        assert "not allowed" in result["error"]["message"]

    def test_manager_can_append_message(self, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        from power_teams.skills.db_skill import write_operation
        with mock.patch("power_teams.skills.db_skill._connect") as mock_conn:
            mock_db = mock.MagicMock()
            mock_conn.return_value.__enter__ = mock.MagicMock(return_value=mock_db)
            mock_conn.return_value.__exit__ = mock.MagicMock(return_value=False)
            mock_cur = mock.MagicMock()
            mock_cur.lastrowid = 42
            mock_db.execute.return_value = mock_cur
            mock_db.execute.return_value.fetchone.return_value = None
            mock_db.execute.return_value.fetchall.return_value = []
            mock_db.commit.return_value = None
            result = write_operation(
                project_session_id="abc123",
                role="manager",
                role_session_id="abc123:manager",
                operation="append_manager_message",
                payload={"content": "hello"},
            )
            assert result["ok"] is True
            assert result["data"]["id"] == 42

    def test_reviewer_update_is_scoped_through_suggestion_session(self, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        from power_teams.skills.db_skill import write_operation
        with mock.patch("power_teams.skills.db_skill._connect") as mock_conn:
            mock_db = mock.MagicMock()
            mock_conn.return_value.__enter__ = mock.MagicMock(return_value=mock_db)
            mock_conn.return_value.__exit__ = mock.MagicMock(return_value=False)
            mock_cur = mock.MagicMock()
            mock_cur.rowcount = 1
            mock_db.execute.return_value = mock_cur
            mock_db.execute.return_value.fetchall.side_effect = [
                [],
            ]
            mock_db.execute.return_value.fetchone.side_effect = [
                None,
                (1,),
            ]
            result = write_operation(
                project_session_id="abc123",
                role="reviewer",
                role_session_id="abc123:reviewer",
                operation="update_reviewer_session",
                payload={"session_id": 7, "status": "completed", "review_notes": "ok"},
            )
            assert result["ok"] is True

    def test_reviewer_update_rejects_cross_project_session(self, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        from power_teams.skills.db_skill import write_operation
        with mock.patch("power_teams.skills.db_skill._connect") as mock_conn:
            mock_db = mock.MagicMock()
            mock_conn.return_value.__enter__ = mock.MagicMock(return_value=mock_db)
            mock_conn.return_value.__exit__ = mock.MagicMock(return_value=False)
            mock_cur = mock.MagicMock()
            mock_cur.rowcount = 0
            mock_db.execute.return_value = mock_cur
            mock_db.execute.return_value.fetchone.return_value = None
            mock_db.execute.return_value.fetchall.return_value = []
            result = write_operation(
                project_session_id="project_b",
                role="reviewer",
                role_session_id="project_b:reviewer",
                operation="update_reviewer_session",
                payload={"session_id": 7, "status": "completed", "review_notes": "bad"},
            )
            assert result["ok"] is False
            assert result["error"]["type"] == "PermissionError"
            assert "not owned by project_session_id project_b" in result["error"]["message"]


class TestSkillErrorSetsAgentState:
    def test_failure_updates_agent_state_to_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        from power_teams.skills.db_skill import write_operation, _set_agent_error
        with mock.patch("power_teams.skills.db_skill._connect") as mock_conn:
            mock_db = mock.MagicMock()
            mock_conn.return_value.__enter__ = mock.MagicMock(return_value=mock_db)
            mock_conn.return_value.__exit__ = mock.MagicMock(return_value=False)
            mock_db.execute.return_value = None
            mock_db.commit.return_value = None
            _set_agent_error("manager", "test error message")
            call_args = mock_db.execute.call_args[0]
            assert "UPDATE agent_registry SET state='error'" in call_args[0]
            assert "test error message" in call_args[1]


class TestReadTableDeniesNonAllowlist:
    def test_read_table_rejects_passwords_table(self, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        from power_teams.skills.db_skill import read_table
        result = read_table("abc123", "manager", "abc123:manager", "passwords")
        assert result["ok"] is False
        assert "not in READABLE_TABLES" in result["error"]["message"]


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
