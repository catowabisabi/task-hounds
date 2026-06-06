"""
test_lifecycle_smoke.py — Smoke tests for Task Hounds lifecycle (runtime) tables
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))


@pytest.fixture
def fresh_db():
    """Create a temporary DB and set env so power_teams.db uses it."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test_lifecycle.db"
    old_db = os.environ.get("POWER_TEAMS_DB")
    os.environ["POWER_TEAMS_DB"] = str(db_path)
    from power_teams.db import init_db
    init_db(db_path)
    yield db_path
    os.chdir("C:\\")
    if old_db:
        os.environ["POWER_TEAMS_DB"] = old_db
    else:
        os.environ.pop("POWER_TEAMS_DB", None)
    try:
        db_path.unlink()
    except Exception:
        pass
    try:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass


class TestRuntimeTables:
    def test_runtime_tables_exist(self, fresh_db):
        from power_teams.db import connect
        with connect(fresh_db) as db:
            db.execute("SELECT 1 FROM runtime_policies").fetchone()
            db.execute("SELECT 1 FROM run_checkpoints").fetchone()
            db.execute("SELECT 1 FROM agent_runtime_bindings").fetchone()

    def test_opencode_instances_has_all_columns(self, fresh_db):
        from power_teams.db import connect
        with connect(fresh_db) as db:
            cols = {r[1] for r in db.execute("PRAGMA table_info(opencode_server_instances)").fetchall()}
            for col in ["owner", "managed", "status", "topology", "last_seen"]:
                assert col in cols, f"missing column: {col}"

    def test_get_runtime_policy_default(self, fresh_db):
        from power_teams.db import get_runtime_policy
        policy = get_runtime_policy(path=fresh_db)
        assert policy["name"] == "default"
        assert "max_managed_opencode_servers" in policy

    def test_discover_external_quick(self, fresh_db):
        from power_teams.db import discover_external_opencode_servers
        start = time.time()
        result = discover_external_opencode_servers(timeout=0.1)
        elapsed = time.time() - start
        assert elapsed < 3.0
        assert isinstance(result, list)

    def test_is_port_reachable_short_timeout(self, fresh_db):
        from power_teams.runtime.opencode_lifecycle import is_port_reachable
        start = time.time()
        result = is_port_reachable("127.0.0.1", 18765, timeout=0.1)
        elapsed = time.time() - start
        assert elapsed < 0.5

    def test_discover_external_prioritizes_default_port(self, fresh_db):
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
        manager = OpenCodeLifecycleManager(db_path=fresh_db)
        results = manager.discover_external()
        assert isinstance(results, list)

    def test_active_work_is_scoped_to_project_session(self, fresh_db):
        from power_teams.db import create_suggestion, has_active_work
        create_suggestion("other session work", session_id="ps_other", path=fresh_db)
        active, reason = has_active_work(session_id="ps_current", path=fresh_db)
        assert active is False
        assert reason == ""
        create_suggestion("current session work", session_id="ps_current", path=fresh_db)
        active, reason = has_active_work(session_id="ps_current", path=fresh_db)
        assert active is True
        assert "active suggestion" in reason
