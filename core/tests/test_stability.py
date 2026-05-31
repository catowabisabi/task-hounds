"""test_stability.py - Task Hounds Stability Hardening Tests"""
from __future__ import annotations

import json
import importlib.util
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
_CORE_API_SERVER = None


def _core_api_server():
    """Load legacy helper module while FastAPI owns runtime serving."""
    global _CORE_API_SERVER
    if _CORE_API_SERVER is not None:
        return _CORE_API_SERVER
    server_path = ROOT / "core" / "api" / "server.py"
    spec = importlib.util.spec_from_file_location("task_hounds_core_api_server", server_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _CORE_API_SERVER = module
    return module


def _mk_db(tmp_path, schema_sql):
    db = tmp_path / "test.db"
    schema_sql.write_text(schema_sql.read_text(encoding="utf-8"), encoding="utf-8")
    return db


class _FakeRow(dict):
    def __init__(self, d: dict):
        super().__init__(d)
        for k, v in d.items():
            setattr__(k, v)


@pytest.fixture
def test_db(tmp_path):
    schema_file = ROOT / "core" / "db" / "schema.sql"
    db = tmp_path / "test.db"
    schema_text = schema_file.read_text(encoding="utf-8")
    with sqlite3.connect(db) as conn:
        conn.executescript(schema_text)
        conn.execute("INSERT INTO project_sessions (id, workspace_id, name) VALUES (?, ?, ?)",
                     ("ws1", "ws1", "Test Workspace"))
        conn.commit()
    return db


@pytest.fixture
def monkeypatch_db(monkeypatch, tmp_path):
    test_db_path = tmp_path / "test.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(test_db_path))
    yield test_db_path


class TestConnectBusyTimeout:
    def test_connect_sets_busy_timeout(self, tmp_path):
        from power_teams.db import connect, SCHEMA_PATH
        db_path = tmp_path / "busy.db"
        schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
        with sqlite3.connect(db_path) as conn:
            conn.executescript(schema_text)
        conn = connect(db_path)
        result = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        assert result > 0, f"busy_timeout should be > 0, got {result}"


class TestPathNormalization:
    def test_normalize_case_insensitive(self):
        from power_teams.db import normalize_workspace_path
        p1 = normalize_workspace_path("C:\\Test\\Path")
        p2 = normalize_workspace_path("c:\\test\\path")
        assert p1.lower() == p2.lower()

    def test_normalize_trailing_slash(self):
        from power_teams.db import normalize_workspace_path
        p1 = normalize_workspace_path("C:\\Test")
        p2 = normalize_workspace_path("C:\\Test\\")
        assert p1 == p2

    def test_normalize_resolves(self):
        from power_teams.db import normalize_workspace_path
        result = normalize_workspace_path("C:\\Windows")
        assert result == os.path.realpath("C:\\Windows")


class TestDuplicateDetection:
    def test_duplicate_path_detected(self, tmp_path, monkeypatch):
        from power_teams.db import connect, is_workspace_path_duplicate
        db = tmp_path / "dup.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path) VALUES (?, ?, ?, ?)",
                         ("ws1", "ws1", "Test", "C:\\Existing\\Path"))
            conn.commit()
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        result = is_workspace_path_duplicate("C:\\Existing\\Path", path=db)
        assert result is True

    def test_duplicate_exclude_self(self, tmp_path, monkeypatch):
        from power_teams.db import connect, is_workspace_path_duplicate
        db = tmp_path / "dup2.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path) VALUES (?, ?, ?, ?)",
                         ("ws1", "ws1", "Test", "C:\\Existing\\Path"))
            conn.commit()
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        result = is_workspace_path_duplicate("C:\\Existing\\Path", exclude_ws_id="ws1", path=db)
        assert result is False


class TestFingerprint:
    def test_fingerprint_git(self, tmp_path):
        from power_teams.db import get_workspace_fingerprint
        ws = tmp_path / "git_project"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()
        config = git_dir / "config"
        config.write_text("[remote \"origin\"]\n        url = https://github.com/example/repo.git", encoding="utf-8")
        fp = get_workspace_fingerprint(str(ws))
        assert fp is not None
        assert fp.startswith("git:")

    def test_fingerprint_npm(self, tmp_path):
        from power_teams.db import get_workspace_fingerprint
        ws = tmp_path / "npm_project"
        ws.mkdir()
        pkg = ws / "package.json"
        pkg.write_text(json.dumps({"name": "@test/pkg", "version": "1.0.0"}), encoding="utf-8")
        fp = get_workspace_fingerprint(str(ws))
        assert fp is not None
        assert fp.startswith("npm:")

    def test_fingerprint_pyproject(self, tmp_path):
        from power_teams.db import get_workspace_fingerprint
        ws = tmp_path / "py_project"
        ws.mkdir()
        pyproject = ws / "pyproject.toml"
        pyproject.write_text("[project]\nname = \"test-project\"\nversion = \"0.1.0\"", encoding="utf-8")
        fp = get_workspace_fingerprint(str(ws))
        assert fp is not None
        assert fp.startswith("py:")

    def test_fingerprint_none(self, tmp_path):
        from power_teams.db import get_workspace_fingerprint
        ws = tmp_path / "empty_project"
        ws.mkdir()
        fp = get_workspace_fingerprint(str(ws))
        assert fp is None


class TestActiveContext:
    def test_consistent_context(self, tmp_path, monkeypatch):
        from power_teams.db import connect
        db = tmp_path / "ctx.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path) VALUES (?, ?, ?, ?)",
                         ("ps1", "ws1", "Test", "C:\\Test\\Path"))
            conn.commit()
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        settings_file = runtime / "settings.json"
        settings_file.write_text(json.dumps({
            "active_workspace_id": "ws1",
            "active_project_session": "ps1",
            "workspace_path": "C:\\Test\\Path",
        }), encoding="utf-8")
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        monkeypatch.setenv("POWER_TEAMS_RUNTIME_DIR", str(runtime))
        from power_teams.db import get_active_context
        ctx = get_active_context(db)
        assert ctx["is_consistent"] is True
        assert ctx["active_workspace_id"] == "ws1"
        assert ctx["active_project_session"] == "ps1"
        assert ctx["workspace_id"] == "ws1"
        assert ctx["project_session_id"] == "ps1"

    def test_consistent_context_legacy_keys(self, tmp_path, monkeypatch):
        from power_teams.db import connect
        db = tmp_path / "ctx.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path) VALUES (?, ?, ?, ?)",
                         ("ps1", "ws1", "Test", "C:\\Test\\Path"))
            conn.commit()
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        settings_file = runtime / "settings.json"
        settings_file.write_text(json.dumps({
            "workspace_id": "ws1",
            "project_session_id": "ps1",
            "workspace_path": "C:\\Test\\Path",
        }), encoding="utf-8")
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        monkeypatch.setenv("POWER_TEAMS_RUNTIME_DIR", str(runtime))
        from power_teams.db import get_active_context
        ctx = get_active_context(db)
        assert ctx["is_consistent"] is True
        assert ctx["active_workspace_id"] == "ws1"
        assert ctx["active_project_session"] == "ps1"

    def test_mismatch_context(self, tmp_path, monkeypatch):
        from power_teams.db import connect
        db = tmp_path / "ctx2.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path) VALUES (?, ?, ?, ?)",
                         ("ps1", "ws1", "Test", "C:\\Test\\Path"))
            conn.commit()
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        settings_file = runtime / "settings.json"
        settings_file.write_text(json.dumps({
            "workspace_id": "ws_wrong",
            "workspace_path": "C:\\Wrong\\Path",
            "project_session_id": "ps1",
        }), encoding="utf-8")
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        monkeypatch.setenv("POWER_TEAMS_RUNTIME_DIR", str(runtime))
        from power_teams.db import get_active_context
        ctx = get_active_context(db)
        assert ctx["is_consistent"] is False


class TestWorkspacePathValidation:
    def test_missing_path_detected(self, tmp_path, monkeypatch):
        from power_teams.db import connect, check_workspace_path
        db = tmp_path / "missing.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path, path_missing) VALUES (?, ?, ?, ?, ?)",
                         ("ws1", "ws1", "Test", "C:\\NonExistent\\Path", 1))
            conn.commit()
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        result = check_workspace_path(db, "ws1")
        assert len(result) > 0
        assert result[0]["path_missing"] == 1

    def test_valid_path_not_missing(self, tmp_path, monkeypatch):
        from power_teams.db import connect, check_workspace_path
        db = tmp_path / "valid.db"
        ws_path = tmp_path / "real_ws"
        ws_path.mkdir()
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path) VALUES (?, ?, ?, ?)",
                         ("ws1", "ws1", "Test", str(ws_path)))
            conn.commit()
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        result = check_workspace_path(db, "ws1")
        assert len(result) == 0


class TestRelink:
    def test_relink_updates_path(self, tmp_path, monkeypatch):
        from power_teams.db import connect, update_project_session, get_project_session
        db = tmp_path / "relink.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path) VALUES (?, ?, ?, ?)",
                         ("ws1", "ws1", "Test", "C:\\Old\\Path"))
            conn.commit()
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        new_path = tmp_path / "new_workspace"
        new_path.mkdir()
        update_project_session("ws1", path=db, workspace_path=str(new_path), path_missing=0)
        row = get_project_session("ws1", path=db)
        assert row["workspace_path"] == str(new_path)
        assert row["path_missing"] == 0

    def test_relink_to_nonexistent_fails(self, tmp_path, monkeypatch):
        from power_teams.db import connect
        db = tmp_path / "relink_fail.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path) VALUES (?, ?, ?, ?)",
                         ("ws1", "ws1", "Test", "C:\\Old\\Path"))
            conn.commit()
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        result = Path("C:\\NonExistent\\Path").exists()
        assert result is False


class TestArchivedSessionRejection:
    def test_archived_session_blocks_write(self, tmp_path, monkeypatch):
        from power_teams.skills import db_skill
        db = tmp_path / "arch.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO sessions_arch (session_key, session_name) VALUES (?, ?)",
                         ("ps_arch", "Archived Session"))
            conn.commit()
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        db_skill._DB_PATH = db
        with pytest.raises(PermissionError, match="archived"):
            db_skill._execute_operation("manager", "ps_arch", "append_manager_message", {"content": "test"})


class TestReviewerOwnership:
    def test_update_reviewer_session_checks_ownership(self, tmp_path, monkeypatch):
        from power_teams.skills.db_skill import _execute_operation
        db = tmp_path / "owner.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name) VALUES (?, ?, ?)",
                         ("ps1", "ws1", "Test1"))
            conn.execute("INSERT INTO suggestion_queue (id, content, status, session_id) VALUES (?, ?, ?, ?)",
                         (1, "Test suggestion", "released", "ps_other"))
            conn.execute("INSERT INTO reviewer_sessions (id, suggestion_id, status) VALUES (?, ?, ?)",
                         (1, 1, "pending"))
            conn.commit()
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        with pytest.raises(PermissionError, match="not owned"):
            _execute_operation("reviewer", "ps1", "update_reviewer_session",
                               {"session_id": 1, "status": "completed", "review_notes": "test"})


class TestEmptyDBInit:
    def test_init_db_succeeds_on_empty_db(self, tmp_path):
        from power_teams.db import init_db
        db_path = tmp_path / "empty_init.db"
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        conn.close()
        table_names = [t[0] for t in tables]
        assert "project_sessions" in table_names
        assert "agent_registry" in table_names
        assert "suggestion_queue" in table_names


class TestHealthEndpoint:
    def test_health_returns_all_fields(self, tmp_path, monkeypatch):
        db = tmp_path / "health.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO agent_registry (id, name, role, host, port, state) VALUES (?, ?, ?, ?, ?, ?)",
                         ("manager_0001", "manager", "manager", "127.0.0.1", 18765, "idle"))
            conn.commit()
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        monkeypatch.setenv("POWER_TEAMS_RUNTIME_DIR", str(tmp_path / "runtime"))
        (tmp_path / "runtime").mkdir(exist_ok=True)
        Handler = _core_api_server().Handler
        import inspect
        src = inspect.getsource(Handler._health)
        assert "ok" in src
        assert "timestamp" in src
        assert "backend_version" in src
        assert "db_path" in src
        assert "active_workspace_id" in src
        assert "active_project_session" in src
        assert "shared_opencode_host" in src
        assert "shared_opencode_port" in src


class TestCheckWorkspaceReady:
    def test_check_workspace_ready_returns_error_for_missing(self, tmp_path, monkeypatch):
        from power_teams.skills import db_skill
        server = _core_api_server()
        db = tmp_path / "ready.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path, path_missing) VALUES (?, ?, ?, ?, ?)",
                         ("ws1", "ws1", "Test", "C:\\DoesNotExist", 1))
            conn.commit()
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        monkeypatch.setenv("POWER_TEAMS_RUNTIME_DIR", str(tmp_path / "runtime"))
        (tmp_path / "runtime").mkdir(exist_ok=True)
        db_skill._DB_PATH = db
        server.DB_PATH = db
        Handler = _core_api_server().Handler
        class FakeHandler(Handler):
            def __init__(self):
                pass
            def _json(self, data, status=200):
                self._health_data = data
        h = FakeHandler()
        err = h._check_workspace_ready("ws1")
        assert err is not None
        assert err["error"] == "workspace_path_missing"

    def test_check_workspace_ready_returns_none_for_valid(self, tmp_path, monkeypatch):
        from power_teams.skills import db_skill
        server = _core_api_server()
        db = tmp_path / "ready2.db"
        ws_path = tmp_path / "real_ws"
        ws_path.mkdir()
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path) VALUES (?, ?, ?, ?)",
                         ("ws1", "ws1", "Test", str(ws_path)))
            conn.commit()
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        monkeypatch.setenv("POWER_TEAMS_RUNTIME_DIR", str(tmp_path / "runtime"))
        (tmp_path / "runtime").mkdir(exist_ok=True)
        db_skill._DB_PATH = db
        server.DB_PATH = db
        Handler = _core_api_server().Handler
        class FakeHandler(Handler):
            def __init__(self):
                pass
            def _json(self, data, status=200):
                self._health_data = data
        h = FakeHandler()
        err = h._check_workspace_ready("ws1")
        assert err is None

    def test_new_session_creates_db_row_and_updates_settings(self, tmp_path, monkeypatch):
        from power_teams.skills import db_skill
        import power_teams.db as db_mod
        server = _core_api_server()
        from power_teams.agents import base
        db = tmp_path / "newsession.db"
        ws_path = tmp_path / "ws"
        ws_path.mkdir()
        git_dir = ws_path / ".git"
        git_dir.mkdir()
        config = git_dir / "config"
        config.write_text('[remote "origin"]\n        url = https://github.com/test/repo.git', encoding="utf-8")
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path) VALUES (?, ?, ?, ?)",
                         ("ws1", "ws1", "Test", str(ws_path)))
            conn.commit()


class TestProjectSessionSwitch:
    def test_switch_updates_db_and_settings(self, tmp_path, monkeypatch):
        from power_teams.skills import db_skill
        import power_teams.db as db_mod
        server = _core_api_server()
        from power_teams.agents import base
        db = tmp_path / "switch.db"
        ws_path = tmp_path / "ws"
        ws_path.mkdir()
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path, is_active) VALUES (?, ?, ?, ?, ?)",
                         ("ws1", "ws1", "Test", str(ws_path), 1))
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path, is_active) VALUES (?, ?, ?, ?, ?)",
                         ("ps_old", "ws1", "Old", str(ws_path), 0))
            conn.commit()
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        settings_file = runtime / "settings.json"
        settings_file.write_text(json.dumps({}), encoding="utf-8")
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        monkeypatch.setenv("POWER_TEAMS_RUNTIME_DIR", str(runtime))
        db_skill._DB_PATH = db
        db_mod._DB_PATH = db
        server.DB_PATH = db
        base.SETTINGS_FILE = settings_file
        Handler = _core_api_server().Handler
        class FakeHandler(Handler):
            def __init__(self):
                pass
            def _json(self, data, status=200):
                self._resp = data
            def _save_settings(self, settings):
                settings_file.write_text(json.dumps(settings), encoding="utf-8")
        h = FakeHandler()
        h._project_session_switch("ps_old")
        assert h._resp["ok"] is True
        assert "session_id" in h._resp
        with sqlite3.connect(db) as conn:
            ws1_active_row = conn.execute("SELECT is_active FROM project_sessions WHERE id=?", ("ws1",)).fetchone()
            ps_old_active_row = conn.execute("SELECT is_active FROM project_sessions WHERE id=?", ("ps_old",)).fetchone()
            assert ws1_active_row[0] == 0
            assert ps_old_active_row[0] == 1
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert settings.get("active_project_session") == "ps_old"
        assert settings.get("active_workspace_id") == "ws1"

    def test_switch_missing_path_returns_warning(self, tmp_path, monkeypatch):
        from power_teams.skills import db_skill
        import power_teams.db as db_mod
        server = _core_api_server()
        from power_teams.agents import base
        db = tmp_path / "switch2.db"
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path, path_missing) VALUES (?, ?, ?, ?, ?)",
                         ("ps1", "ws1", "Test", "C:\\DoesNotExist", 1))
            conn.commit()
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        settings_file = runtime / "settings.json"
        settings_file.write_text(json.dumps({}), encoding="utf-8")
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        monkeypatch.setenv("POWER_TEAMS_RUNTIME_DIR", str(runtime))
        db_skill._DB_PATH = db
        db_mod._DB_PATH = db
        server.DB_PATH = db
        base.SETTINGS_FILE = settings_file
        Handler = _core_api_server().Handler
        class FakeHandler(Handler):
            def __init__(self):
                pass
            def _json(self, data, status=200):
                self._resp = data
            def _save_settings(self, settings):
                settings_file.write_text(json.dumps(settings), encoding="utf-8")
        h = FakeHandler()
        h._project_session_switch("ps1")
        assert h._resp["ok"] is True
        assert "warning" in h._resp
        assert h._resp["warning"] == "workspace_path_missing"


class TestWorkspaceRelink:
    def test_relink_preserves_session_id_updates_path_and_fingerprint(self, tmp_path, monkeypatch):
        from power_teams.skills import db_skill
        import power_teams.db as db_mod
        server = _core_api_server()
        from power_teams.agents import base
        db = tmp_path / "relink2.db"
        old_path = tmp_path / "old_ws"
        old_path.mkdir()
        new_path = tmp_path / "new_ws"
        new_path.mkdir()
        git_dir = new_path / ".git"
        git_dir.mkdir()
        config = git_dir / "config"
        config.write_text('[remote "origin"]\n        url = https://github.com/test/repo.git', encoding="utf-8")
        schema_text = (ROOT / "core" / "db" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(db) as conn:
            conn.executescript(schema_text)
            conn.execute("INSERT INTO project_sessions (id, workspace_id, name, workspace_path, workspace_fingerprint) VALUES (?, ?, ?, ?, ?)",
                         ("ws1", "ws1", "Test", str(old_path), "old_fingerprint"))
            conn.execute("INSERT INTO project_handoff (session_id, project_folder_location, version) VALUES (?, ?, ?)",
                         ("ws1", str(old_path), 1))
            conn.commit()
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        settings_file = runtime / "settings.json"
        settings_file.write_text(json.dumps({}), encoding="utf-8")
        monkeypatch.setenv("POWER_TEAMS_DB", str(db))
        monkeypatch.setenv("POWER_TEAMS_RUNTIME_DIR", str(runtime))
        db_skill._DB_PATH = db
        db_mod._DB_PATH = db
        server.DB_PATH = db
        base.SETTINGS_FILE = settings_file
        Handler = _core_api_server().Handler
        class FakeHandler(Handler):
            def __init__(self):
                self.path = "/api/workspaces/ws1/relink"
            def _json(self, data, status=200):
                self._resp = data
            def _read_json_body(self):
                return {"path": str(new_path)}
        h = FakeHandler()
        h._workspace_relink("ws1")
        assert h._resp["ok"] is True
        assert "workspace_id" in h._resp
        assert h._resp["workspace_id"] == "ws1"
        assert h._resp["workspace_path"] == str(new_path)
        assert h._resp["workspace_fingerprint"] is not None
        with sqlite3.connect(db) as conn:
            row = conn.execute("SELECT workspace_path, workspace_fingerprint FROM project_sessions WHERE id=?", ("ws1",)).fetchone()
            assert row[0] == str(new_path)
            assert row[1] is not None
            handoff = conn.execute("SELECT project_folder_location FROM project_handoff WHERE session_id=?", ("ws1",)).fetchone()
            assert handoff[0] == str(new_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
