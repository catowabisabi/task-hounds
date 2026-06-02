from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "core" / "db"
DB_PATH = Path(os.environ.get("POWER_TEAMS_DB", str(DATA_DIR / "power_teams.db")))
SCHEMA_PATH = DATA_DIR / "schema.sql"


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA busy_timeout = 5000")
    db.execute("PRAGMA journal_mode = WAL")
    db.row_factory = sqlite3.Row
    return db


def _apply_migration_script(db: sqlite3.Connection, migration_text: str):
    """Apply a migration script statement-by-statement, handling multi-statement SQL blocks."""
    for part in _split_into_executable_units(migration_text):
        if not part.strip() or part.strip().startswith('--'):
            continue
        try:
            db.execute(part)
        except sqlite3.OperationalError as e:
            if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
                continue
            raise
    db.commit()


def _split_into_executable_units(text: str):
    units = []
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('--'):
            continue
        if not stripped:
            continue
        current += line + "\n"
        if sqlite3.complete_statement(current):
            units.append(current.strip())
            current = ""
    if current.strip():
        units.append(current.strip())
    return units


def init_db(path: Path = DB_PATH) -> None:
    """Initialize database with schema and migrations."""
    with connect(path) as db:
        db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _ensure_agent_progress_columns(db)

        migrations_dir = DATA_DIR / "migrations"
        if migrations_dir.exists():
            for migration_file in sorted(migrations_dir.glob("*.sql")):
                try:
                    _apply_migration_script(db, migration_file.read_text(encoding="utf-8"))
                except Exception as exc:
                    raise RuntimeError(f"failed to apply migration {migration_file.name}: {exc}") from exc

        db.commit()


def _ensure_agent_progress_columns(db: sqlite3.Connection) -> None:
    existing = {row["name"] for row in db.execute("PRAGMA table_info(agent_registry)").fetchall()}
    for name, ddl in {
        "current_step": "ALTER TABLE agent_registry ADD COLUMN current_step TEXT",
        "current_step_started_at": "ALTER TABLE agent_registry ADD COLUMN current_step_started_at TIMESTAMP",
        "last_stream_at": "ALTER TABLE agent_registry ADD COLUMN last_stream_at TIMESTAMP",
    }.items():
        if name not in existing:
            db.execute(ddl)


def seed_default_agents(path: Path = DB_PATH) -> None:
    shared_host = os.environ.get("POWER_TEAMS_OPENCODE_HOST", "127.0.0.1")
    shared_port = int(os.environ.get("POWER_TEAMS_OPENCODE_PORT", "18765"))

    def role_agent(role: str) -> str:
        return os.environ.get(f"POWER_TEAMS_{role.upper()}_OPENCODE_AGENT", "Sisyphus - ultraworker")

    def role_model(role: str) -> str | None:
        return os.environ.get(f"POWER_TEAMS_{role.upper()}_MODEL") or os.environ.get("POWER_TEAMS_DEFAULT_MODEL")

    with connect(path) as db:
        rows = [
            (
                "manager_0001", "manager", "manager", shared_host, shared_port,
                role_model("manager"), role_agent("manager"), "idle", 0,
                '{"worker":"worker_0001","reviewer":"reviewer_0001","chat":"chat_0001"}',
            ),
            (
                "worker_0001", "worker", "worker", shared_host, shared_port,
                role_model("worker"), role_agent("worker"), "idle", 0,
                '{"manager":"manager_0001"}',
            ),
            (
                "reviewer_0001", "reviewer", "reviewer", shared_host, shared_port,
                role_model("reviewer"), role_agent("reviewer"), "idle", 0,
                '{"manager":"manager_0001"}',
            ),
            (
                "chat_0001", "chat", "chat", shared_host, shared_port,
                role_model("chat"), role_agent("chat"), "idle", 0,
                '{"manager":"manager_0001","worker":"worker_0001","reviewer":"reviewer_0001"}',
            ),
        ]
        db.executemany(
            """
            INSERT OR IGNORE INTO agent_registry
                (id, name, role, host, port, model, opencode_agent, state, task_complete, relations_json)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        db.execute(
            """
            UPDATE agent_registry
               SET host = ?, port = ?, updated_at = CURRENT_TIMESTAMP
             WHERE name IN ('manager', 'worker', 'reviewer', 'chat')
            """,
            (shared_host, shared_port),
        )
        if "POWER_TEAMS_WORKER_OPENCODE_AGENT" not in os.environ:
            db.execute(
                """
                UPDATE agent_registry
                   SET opencode_agent = 'general', updated_at = CURRENT_TIMESTAMP
                 WHERE name = 'worker' AND opencode_agent = 'build'
                """
            )
        for role in ("manager", "worker", "reviewer", "chat"):
            if f"POWER_TEAMS_{role.upper()}_OPENCODE_AGENT" in os.environ:
                db.execute(
                    "UPDATE agent_registry SET opencode_agent=?, updated_at=CURRENT_TIMESTAMP WHERE name=?",
                    (role_agent(role), role),
                )
            if f"POWER_TEAMS_{role.upper()}_MODEL" in os.environ or "POWER_TEAMS_DEFAULT_MODEL" in os.environ:
                db.execute(
                    "UPDATE agent_registry SET model=?, updated_at=CURRENT_TIMESTAMP WHERE name=?",
                    (role_model(role), role),
                )
        db.commit()


def get_agent(name: str, path: Path = DB_PATH) -> sqlite3.Row:
    with connect(path) as db:
        row = db.execute("SELECT * FROM agent_registry WHERE name=?", (name,)).fetchone()
    if row is None:
        raise RuntimeError(f"agent not registered: {name}")
    return row


def update_agent(name: str, **fields) -> None:
    if not fields:
        return
    keys = list(fields)
    sets = ", ".join(f"{key}=?" for key in keys)
    values = [fields[key] for key in keys]
    values.append(name)
    with connect() as db:
        cur = db.execute(
            f"UPDATE agent_registry SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE name=?",
            values,
        )
        if cur.rowcount == 0:
            raise RuntimeError(f"agent not registered: {name}")
        db.commit()


def get_all_agents(path: Path = DB_PATH) -> list:
    with connect(path) as db:
        return db.execute(
            "SELECT * FROM agent_registry ORDER BY role, name"
        ).fetchall()


# ─────────────────────────────────────────────────────────────
#  PATH NORMALIZATION HELPERS
# ─────────────────────────────────────────────────────────────

def normalize_workspace_path(path_str: str) -> str:
    return os.path.realpath(Path(path_str).resolve())


def is_workspace_path_duplicate(normalized_path: str, exclude_ws_id: str = None, path: Path = DB_PATH) -> bool:
    with connect(path) as db:
        if exclude_ws_id:
            rows = db.execute(
                "SELECT id FROM project_sessions WHERE workspace_path=? AND id != ?",
                (normalized_path, exclude_ws_id),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id FROM project_sessions WHERE workspace_path=?",
                (normalized_path,),
            ).fetchall()
        return len(rows) > 0


def get_workspace_fingerprint(workspace_path: str) -> str | None:
    git_config = Path(workspace_path) / ".git" / "config"
    if git_config.exists():
        return f"git:{git_config.read_text(encoding='utf-8', errors='ignore')[:200]}"
    pkg_json = Path(workspace_path) / "package.json"
    if pkg_json.exists():
        return f"npm:{pkg_json.read_text(encoding='utf-8', errors='ignore')[:200]}"
    pyproject = Path(workspace_path) / "pyproject.toml"
    if pyproject.exists():
        return f"py:{pyproject.read_text(encoding='utf-8', errors='ignore')[:200]}"
    return None


def check_fingerprint_mismatch(ws_id: str, new_path: str, *, path: Path = DB_PATH) -> tuple[bool, str]:
    new_fp = get_workspace_fingerprint(new_path)
    with connect(path) as db:
        row = db.execute("SELECT workspace_fingerprint FROM project_sessions WHERE id=?", (ws_id,)).fetchone()
    if row is None:
        return False, ""
    old_fp = row["workspace_fingerprint"]
    # Skip check if old fingerprint is missing or looks like a placeholder (not a real git fingerprint)
    if not old_fp or not old_fp.startswith("git:"):
        return False, ""
    if new_fp and old_fp != new_fp:
        return True, f"Fingerprint mismatch: expected {old_fp[:30]}..., got {new_fp[:30]}..."
    return False, ""


# ─────────────────────────────────────────────────────────────
#  ACTIVE CONTEXT
# ─────────────────────────────────────────────────────────────

def get_active_context(path: Path = DB_PATH) -> dict:
    import json as _json
    settings_file = Path(os.environ.get("POWER_TEAMS_RUNTIME_DIR", str(ROOT / "core" / "runtime"))) / "settings.json"
    result = {
        "active_workspace_id": None,
        "active_project_session": None,
        "workspace_id": None,
        "workspace_path": None,
        "project_session_id": None,
        "path_missing": False,
        "is_consistent": True,
    }
    if settings_file.exists():
        try:
            settings = _json.loads(settings_file.read_text(encoding="utf-8-sig"))
            result["workspace_id"] = settings.get("active_workspace_id") or settings.get("workspace_id")
            result["workspace_path"] = settings.get("workspace_path")
            result["project_session_id"] = settings.get("active_project_session") or settings.get("project_session_id")
        except Exception:
            pass
    result["active_workspace_id"] = result["workspace_id"]
    result["active_project_session"] = result["project_session_id"]
    if result["workspace_id"] and result["project_session_id"]:
        with connect(path) as db:
            ps = db.execute(
                "SELECT workspace_id, workspace_path, path_missing FROM project_sessions WHERE id=?",
                (result["project_session_id"],),
            ).fetchone()
        if ps:
            if ps["workspace_id"] != result["workspace_id"]:
                result["is_consistent"] = False
            result["path_missing"] = bool(ps["path_missing"])
        else:
            result["is_consistent"] = False
    return result


# ─────────────────────────────────────────────────────────────
#  PROJECT HANDOFF
# ─────────────────────────────────────────────────────────────

_HANDOFF_FIELDS = [
    "human_requirements", "working_direction", "references_demos",
    "file_structure", "important_files", "available_scripts", "existing_solutions",
    "macro_flow", "current_task", "current_micro_flow", "human_concerns",
    "tested_files", "known_bugs", "completion_criteria",
    "project_folder_location",
]


def get_latest_handoff(path: Path = DB_PATH, session_id: str | None = None) -> sqlite3.Row | None:
    """Return the most recent handoff row for the given session, or None."""
    with connect(path) as db:
        if session_id:
            return db.execute(
                "SELECT * FROM project_handoff WHERE session_id=? ORDER BY version DESC LIMIT 1",
                (session_id,)
            ).fetchone()
        return db.execute(
            "SELECT * FROM project_handoff ORDER BY version DESC LIMIT 1"
        ).fetchone()


def list_handoff_versions(path: Path = DB_PATH, session_id: str | None = None) -> list:
    """Return summary of all handoff versions (newest first)."""
    with connect(path) as db:
        if session_id:
            return db.execute(
                "SELECT id, version, current_task, updated_at, updated_by "
                "FROM project_handoff WHERE session_id=? ORDER BY version DESC",
                (session_id,)
            ).fetchall()
        return db.execute(
            "SELECT id, version, current_task, updated_at, updated_by "
            "FROM project_handoff ORDER BY version DESC"
        ).fetchall()


def upsert_handoff(updated_by: str = "manager", path: Path = DB_PATH,
                   session_id: str | None = None, **fields) -> int:
    """
    Create a new handoff version by copying the latest row and applying overrides.
    JSON-serialises list/dict values automatically.
    Returns the new version number.
    """
    import json as _json

    with connect(path) as db:
        if session_id:
            latest = db.execute(
                "SELECT * FROM project_handoff WHERE session_id=? ORDER BY version DESC LIMIT 1",
                (session_id,)
            ).fetchone()
        else:
            latest = db.execute(
                "SELECT * FROM project_handoff ORDER BY version DESC LIMIT 1"
            ).fetchone()

        new_row = {}
        for f in _HANDOFF_FIELDS:
            if f in fields:
                v = fields[f]
                new_row[f] = _json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
            else:
                new_row[f] = latest[f] if latest else None

        new_version = (latest["version"] + 1) if latest else 1
        cols = ", ".join(["version", "updated_by", "session_id"] + _HANDOFF_FIELDS)
        placeholders = ", ".join(["?"] * (3 + len(_HANDOFF_FIELDS)))
        values = [new_version, updated_by, session_id] + [new_row[f] for f in _HANDOFF_FIELDS]
        db.execute(f"INSERT INTO project_handoff ({cols}) VALUES ({placeholders})", values)
        db.commit()
        return new_version


# ─────────────────────────────────────────────────────────────
#  SUGGESTION QUEUE
# ─────────────────────────────────────────────────────────────

def get_active_suggestion(path: Path = DB_PATH, session_id: str | None = None) -> sqlite3.Row | None:
    """Return the most recent non-done suggestion for the given session."""
    with connect(path) as db:
        if session_id:
            return db.execute(
                "SELECT * FROM suggestion_queue "
                "WHERE status != 'done' AND session_id=? ORDER BY id DESC LIMIT 1",
                (session_id,)
            ).fetchone()
        return db.execute(
            "SELECT * FROM suggestion_queue "
            "WHERE status != 'done' ORDER BY id DESC LIMIT 1"
        ).fetchone()


def create_suggestion(content: str, verification: str = None,
                      related_files: list = None, handoff_version: int = None,
                      session_id: str | None = None,
                      path: Path = DB_PATH) -> int:
    """Create a new suggestion with status=released. Returns new row id."""
    import json as _json
    with connect(path) as db:
        cur = db.execute(
            "INSERT INTO suggestion_queue "
            "(content, status, verification, related_files, handoff_version, session_id) "
            "VALUES (?, 'released', ?, ?, ?, ?)",
            (content, verification,
             _json.dumps(related_files, ensure_ascii=False) if related_files else None,
             handoff_version, session_id)
        )
        db.commit()
        return cur.lastrowid


def update_suggestion(suggestion_id: int, path: Path = DB_PATH, **fields) -> None:
    """Update suggestion fields. Allowed: content, status, human_comment, verification."""
    if not fields:
        return
    allowed = {"content", "status", "human_comment", "verification", "related_files"}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return
    set_parts = [f"{k}=?" for k in keys] + ["updated_at=CURRENT_TIMESTAMP"]
    if "status" in fields:
        if fields["status"] == "released":
            set_parts.append("released_at=CURRENT_TIMESTAMP")
        elif fields["status"] == "done":
            set_parts.append("done_at=CURRENT_TIMESTAMP")
    values = [fields[k] for k in keys] + [suggestion_id]
    with connect(path) as db:
        db.execute(
            f"UPDATE suggestion_queue SET {', '.join(set_parts)} WHERE id=?", values
        )
        db.commit()


def list_suggestions(limit: int = 20, path: Path = DB_PATH) -> list:
    """Return recent suggestions, newest first."""
    with connect(path) as db:
        return db.execute(
            "SELECT * FROM suggestion_queue ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


# ─────────────────────────────────────────────────────────────
#  MANAGER MESSAGES HISTORY
# ─────────────────────────────────────────────────────────────

def list_unscoped_active_suggestions(limit: int = 20, path: Path = DB_PATH) -> list:
    with connect(path) as db:
        return db.execute(
            "SELECT * FROM suggestion_queue "
            "WHERE status != 'done' AND session_id IS NULL "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()


def add_manager_message(content: str, path: Path = DB_PATH,
                        session_id: str | None = None) -> int:
    """Append a new manager-to-human message. Returns new row id."""
    with connect(path) as db:
        cur = db.execute(
            "INSERT INTO manager_messages (content, session_id) VALUES (?, ?)",
            (content, session_id)
        )
        db.commit()
        return cur.lastrowid


def list_manager_messages(path: Path = DB_PATH, session_id: str | None = None, limit: int = 50) -> list:
    """Return manager messages for the given session, newest first."""
    with connect(path) as db:
        if session_id:
            return db.execute(
                "SELECT * FROM manager_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, limit)
            ).fetchall()
        return db.execute(
            "SELECT * FROM manager_messages ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()


# ─────────────────────────────────────────────────────────────
#  SESSION MANAGEMENT (Archive)
# ─────────────────────────────────────────────────────────────

def add_user_directive(
    session_id: str,
    directive: str,
    *,
    status: str = "pending",
    path: Path = DB_PATH,
) -> int:
    if not session_id:
        raise ValueError("session_id is required")
    if not directive or not directive.strip():
        raise ValueError("directive is required")
    with connect(path) as db:
        cur = db.execute(
            "INSERT INTO user_directives (session_id, directive, status) VALUES (?, ?, ?)",
            (session_id, directive.strip(), status),
        )
        db.commit()
        return cur.lastrowid


def get_latest_user_directive(
    session_id: str,
    *,
    status: str | None = None,
    path: Path = DB_PATH,
) -> sqlite3.Row | None:
    if not session_id:
        raise ValueError("session_id is required")
    with connect(path) as db:
        if status:
            return db.execute(
                "SELECT * FROM user_directives WHERE session_id=? AND status=? ORDER BY id DESC LIMIT 1",
                (session_id, status),
            ).fetchone()
        return db.execute(
            "SELECT * FROM user_directives WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()


def update_user_directive_status(
    directive_id: int,
    status: str,
    *,
    path: Path = DB_PATH,
) -> None:
    with connect(path) as db:
        cur = db.execute(
            "UPDATE user_directives SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, directive_id),
        )
        if cur.rowcount != 1:
            raise LookupError(f"user_directive {directive_id} update affected {cur.rowcount} rows")
        db.commit()


def add_worker_report(
    session_id: str,
    report: str,
    *,
    path: Path = DB_PATH,
) -> int:
    if not session_id:
        raise ValueError("session_id is required")
    if report is None:
        raise ValueError("report is required")
    with connect(path) as db:
        cur = db.execute(
            "INSERT INTO worker_reports (session_id, report) VALUES (?, ?)",
            (session_id, report),
        )
        db.commit()
        return cur.lastrowid


def get_latest_worker_report(
    session_id: str,
    *,
    path: Path = DB_PATH,
) -> sqlite3.Row | None:
    if not session_id:
        raise ValueError("session_id is required")
    with connect(path) as db:
        return db.execute(
            "SELECT * FROM worker_reports WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()


def create_session_arch(session_key: str, session_name: str, agent_name: str = None,
                         folder_relation: str = None, worker_status: str = None,
                         token_usage: int = 0, path: Path = DB_PATH) -> int:
    """Archive a session (soft delete). Returns new arch id."""
    with connect(path) as db:
        cur = db.execute(
            "INSERT INTO sessions_arch (session_key, session_name, agent_name, "
            "folder_relation, worker_status, token_usage, last_active_at) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (session_key, session_name, agent_name, folder_relation, worker_status, token_usage)
        )
        db.commit()
        return cur.lastrowid


def restore_session_arch(arch_id: int, path: Path = DB_PATH) -> bool:
    """Restore a session from archive (unarchive). Returns True if found."""
    with connect(path) as db:
        row = db.execute("SELECT session_key FROM sessions_arch WHERE id=?", (arch_id,)).fetchone()
        if not row:
            return False
        db.execute("DELETE FROM sessions_arch WHERE id=?", (arch_id,))
        db.commit()
        return True


def list_sessions_arch(limit: int = 100, path: Path = DB_PATH) -> list:
    """Return archived sessions, newest first."""
    with connect(path) as db:
        return db.execute(
            "SELECT * FROM sessions_arch ORDER BY archived_at DESC LIMIT ?", (limit,)
        ).fetchall()


def get_sessions_arch_count(path: Path = DB_PATH) -> int:
    """Return count of archived sessions."""
    with connect(path) as db:
        row = db.execute("SELECT COUNT(*) as cnt FROM sessions_arch").fetchone()
        return row["cnt"] if row else 0


def list_live_sessions(path: Path = DB_PATH) -> list:
    """Return active (non-archived) sessions from agent_registry + runtime state."""
    with connect(path) as db:
        agents = db.execute(
            "SELECT id, name, role, host, port, session_id, state, task_complete, "
            "last_seen, created_at, relations_json FROM agent_registry ORDER BY role, name"
        ).fetchall()
        archived_keys = set(
            row["session_key"] for row in db.execute("SELECT session_key FROM sessions_arch")
        )
    sessions = []
    for a in agents:
        session_key = a["session_id"] or f"{a['name']}_{a['role']}"
        if session_key in archived_keys:
            continue
        sessions.append({
            "session_key": session_key,
            "session_name": f"{a['name']}_{a['role']}",
            "agent_name": a["name"],
            "worker_status": a["state"],
            "created_at": a["created_at"],
            "last_active_at": a["last_seen"] or "",
            "token_usage": 0,
            "folder_relation": a["relations_json"] or "",
        })
    return sessions


def get_live_sessions_count(path: Path = DB_PATH) -> int:
    """Return count of live (non-archived) sessions."""
    return len(list_live_sessions(path=path))


# ─────────────────────────────────────────────────────────────
#  REVIEWER SESSIONS
# ─────────────────────────────────────────────────────────────

def create_reviewer_session(suggestion_id: int, path: Path = DB_PATH) -> int:
    """Create a new reviewer session for a completed suggestion. Returns session id."""
    from datetime import datetime, timedelta, timezone
    timeout = datetime.now(timezone.utc) + timedelta(minutes=5)
    with connect(path) as db:
        cur = db.execute(
            "INSERT INTO reviewer_sessions (suggestion_id, status, started_at, timeout_at) "
            "VALUES (?, 'pending', CURRENT_TIMESTAMP, ?)",
            (suggestion_id, timeout.isoformat())
        )
        db.commit()
        return cur.lastrowid


def update_reviewer_session(session_id: int, path: Path = DB_PATH, **fields) -> None:
    """Update reviewer session fields."""
    if not fields:
        return
    allowed = {"status", "screenshot_paths", "review_notes", "usability_issues",
               "style_feedback", "scripts_documented", "completed_at"}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return
    sets = ", ".join(f"{key}=?" for key in keys)
    values = [fields[key] for key in keys] + [session_id]
    with connect(path) as db:
        db.execute(f"UPDATE reviewer_sessions SET {sets} WHERE id=?", values)
        db.commit()


def get_active_reviewer_session(suggestion_id: int, path: Path = DB_PATH) -> sqlite3.Row | None:
    """Get the most recent active reviewer session for a suggestion."""
    with connect(path) as db:
        return db.execute(
            "SELECT * FROM reviewer_sessions "
            "WHERE suggestion_id=? AND status IN ('pending', 'running') "
            "ORDER BY id DESC LIMIT 1",
            (suggestion_id,)
        ).fetchone()


def is_reviewer_timeout(session_id: int, path: Path = DB_PATH) -> bool:
    """Check if a reviewer session has timed out. Does NOT treat 'completed' as timeout."""
    from datetime import datetime, timezone
    with connect(path) as db:
        row = db.execute(
            "SELECT timeout_at, status FROM reviewer_sessions WHERE id=?",
            (session_id,)
        ).fetchone()
        if not row:
            return False
        if row["status"] == "completed":
            return False  # completed is NOT a timeout
        if row["status"] in ("failed", "timeout"):
            return True
        timeout_at = datetime.fromisoformat(row["timeout_at"])
        now = datetime.now(timezone.utc)
        return now > timeout_at


def mark_reviewer_timeout(session_id: int, path: Path = DB_PATH) -> None:
    """Mark a reviewer session as timed out."""
    update_reviewer_session(session_id, path=path, status="timeout")


def get_reviewer_feedback(suggestion_id: int, path: Path = DB_PATH) -> sqlite3.Row | None:
    """Get completed reviewer feedback for a suggestion."""
    with connect(path) as db:
        return db.execute(
            "SELECT * FROM reviewer_sessions "
            "WHERE suggestion_id=? AND status='completed' "
            "ORDER BY id DESC LIMIT 1",
            (suggestion_id,)
        ).fetchone()


def list_reviewer_sessions(limit: int = 20, path: Path = DB_PATH) -> list:
    """Return recent reviewer sessions, newest first."""
    with connect(path) as db:
        return db.execute(
            "SELECT * FROM reviewer_sessions ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()


def register_opencode_server(
    power_teams_session_id: str,
    agent_role: str,
    host: str,
    port: int,
    opencode_session_id: str | None,
    project_folder: str,
    pid: int | None,
    path: Path = DB_PATH,
) -> int:
    """Register an opencode serve instance for a session. Returns row id."""
    with connect(path) as db:
        cur = db.execute(
            """INSERT INTO opencode_server_instances
               (power_teams_session_id, agent_role, host, port, opencode_session_id, project_folder, pid)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (power_teams_session_id, agent_role, host, port, opencode_session_id, project_folder, pid),
        )
        db.commit()
        return cur.lastrowid


def get_opencode_servers_for_session(power_teams_session_id: str, path: Path = DB_PATH) -> list:
    """Get all opencode server instances for a session."""
    with connect(path) as db:
        return db.execute(
            "SELECT * FROM opencode_server_instances WHERE power_teams_session_id=?",
            (power_teams_session_id,),
        ).fetchall()


def unregister_opencode_servers_for_session(power_teams_session_id: str, path: Path = DB_PATH) -> None:
    """Remove server registrations for a session. Does NOT kill processes."""
    with connect(path) as db:
        db.execute(
            "DELETE FROM opencode_server_instances WHERE power_teams_session_id=?",
            (power_teams_session_id,),
        )
        db.commit()


# ─────────────────────────────────────────────────────────────
#  WORKSPACE / PROJECT SESSIONS
# ─────────────────────────────────────────────────────────────

def get_project_session(workspace_id: str, path: Path = DB_PATH) -> sqlite3.Row | None:
    """Get a project session by id (workspace_id)."""
    with connect(path) as db:
        return db.execute(
            "SELECT * FROM project_sessions WHERE id=?",
            (workspace_id,),
        ).fetchone()


def require_row_dict(row: sqlite3.Row | dict | None, context: str) -> dict:
    if row is None:
        raise LookupError(f"{context}: row not found")
    return dict(row)


def require_row_value(row: sqlite3.Row | dict | None, key: str, context: str):
    data = require_row_dict(row, context)
    if key not in data:
        raise KeyError(f"{context}: missing column {key}")
    value = data[key]
    if value is None or value == "":
        raise LookupError(f"{context}: {key} is empty")
    return value


_ROLE_SESSION_COLUMNS = {
    "manager": "manager_session_id",
    "worker": "worker_session_id",
    "reviewer": "reviewer_session_id",
    "chat": "chat_session_id",
}


def _role_session_column(role: str) -> str:
    try:
        return _ROLE_SESSION_COLUMNS[role]
    except KeyError as exc:
        raise ValueError(f"unknown role for opencode session lookup: {role}") from exc


def _other_role_session_columns(role: str) -> list[str]:
    current = _role_session_column(role)
    return [column for column in _ROLE_SESSION_COLUMNS.values() if column != current]


def resolve_role_opencode_session(
    project_session_id: str,
    role: str,
    *,
    path: Path = DB_PATH,
    require_existing: bool = False,
    allow_agent_registry_fallback: bool = False,
) -> str | None:
    if not project_session_id:
        raise ValueError("project_session_id is required for opencode session lookup")
    column = _role_session_column(role)
    with connect(path) as db:
        project_row = db.execute(
            "SELECT * FROM project_sessions WHERE id=?",
            (project_session_id,),
        ).fetchone()
        project = require_row_dict(project_row, f"project_session_id={project_session_id}")
        role_row = db.execute(
            "SELECT opencode_session_id FROM project_session_role_sessions "
            "WHERE project_session_id=? AND role=?",
            (project_session_id, role),
        ).fetchone()
        if role_row and role_row["opencode_session_id"]:
            return role_row["opencode_session_id"]
        value = project[column] if column in project.keys() else None
        if value:
            return value
        if allow_agent_registry_fallback:
            agent_row = db.execute(
                "SELECT session_id FROM agent_registry WHERE name=?",
                (role,),
            ).fetchone()
            if agent_row and agent_row["session_id"]:
                return agent_row["session_id"]
    if require_existing:
        raise LookupError(f"no opencode session for project_session_id={project_session_id} role={role}")
    return None


def save_role_opencode_session(
    project_session_id: str,
    role: str,
    opencode_session_id: str,
    *,
    path: Path = DB_PATH,
) -> None:
    if not project_session_id:
        raise ValueError("project_session_id is required when saving opencode session")
    if not opencode_session_id:
        raise ValueError("opencode_session_id is required when saving opencode session")
    column = _role_session_column(role)
    other_columns = _other_role_session_columns(role)
    with connect(path) as db:
        project_row = db.execute(
            "SELECT workspace_path FROM project_sessions WHERE id=?",
            (project_session_id,),
        ).fetchone()
        project = require_row_dict(project_row, f"project_session_id={project_session_id}")
        db.execute(
            "UPDATE project_session_role_sessions SET opencode_session_id=NULL, updated_at=CURRENT_TIMESTAMP "
            "WHERE project_session_id=? AND role!=? AND opencode_session_id=?",
            (project_session_id, role, opencode_session_id),
        )
        db.execute(
            """INSERT INTO project_session_role_sessions
                 (project_session_id, role, opencode_session_id, workspace_path)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(project_session_id, role) DO UPDATE SET
                 opencode_session_id=excluded.opencode_session_id,
                 workspace_path=excluded.workspace_path,
                 updated_at=CURRENT_TIMESTAMP""",
            (project_session_id, role, opencode_session_id, project.get("workspace_path")),
        )
        for other_column in other_columns:
            db.execute(
                f"UPDATE project_sessions SET {other_column}=NULL "
                f"WHERE id=? AND {other_column}=?",
                (project_session_id, opencode_session_id),
            )
        cur = db.execute(
            f"UPDATE project_sessions SET {column}=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (opencode_session_id, project_session_id),
        )
        if cur.rowcount != 1:
            raise LookupError(f"project_session_id={project_session_id}: update affected {cur.rowcount} rows")
        db.execute(
            "UPDATE agent_registry SET session_id=?, updated_at=CURRENT_TIMESTAMP WHERE name=?",
            (opencode_session_id, role),
        )
        db.commit()


def update_project_session(workspace_id: str, path: Path = DB_PATH, **fields) -> None:
    allowed = {
        "workspace_path", "path_missing", "is_active", "name", "workspace_fingerprint", "workspace_id",
        "manager_session_id", "worker_session_id", "reviewer_session_id", "chat_session_id",
    }
    keys = [k for k in fields if k in allowed]
    if not keys:
        return
    set_parts = [f"{k}=?" for k in keys] + ["updated_at=CURRENT_TIMESTAMP"]
    values = [fields[k] for k in keys] + [workspace_id]
    with connect(path) as db:
        db.execute(f"UPDATE project_sessions SET {', '.join(set_parts)} WHERE id=?", values)
        db.commit()


def check_workspace_path(path: Path = DB_PATH, workspace_id: str | None = None) -> list:
    with connect(path) as db:
        if workspace_id:
            rows = db.execute(
                "SELECT * FROM project_sessions WHERE id=? AND workspace_path IS NOT NULL AND workspace_path != ''",
                (workspace_id,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM project_sessions WHERE workspace_path IS NOT NULL AND workspace_path != ''"
            ).fetchall()
    result = []
    for row in rows:
        wp = Path(row["workspace_path"])
        missing = not wp.exists()
        if missing:
            result.append({
                "workspace_id": row["id"],
                "name": row["name"],
                "workspace_path": row["workspace_path"],
                "path_missing": 1,
            })
    return result


def list_project_sessions(path: Path = DB_PATH) -> list:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM project_sessions ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_project_session_sessions(workspace_id: str, path: Path = DB_PATH) -> list:
    row = get_project_session(workspace_id, path=path)
    if not row:
        return []
    data = dict(row)
    manager_sid = data.get("manager_session_id")
    worker_sid = data.get("worker_session_id")
    reviewer_sid = data.get("reviewer_session_id")
    chat_sid = data.get("chat_session_id")
    result = []
    if manager_sid:
        result.append({"role": "manager", "session_id": manager_sid})
    if worker_sid:
        result.append({"role": "worker", "session_id": worker_sid})
    if reviewer_sid:
        result.append({"role": "reviewer", "session_id": reviewer_sid})
    if chat_sid:
        result.append({"role": "chat", "session_id": chat_sid})
    return result


# ─────────────────────────────────────────────────────────────
#  TODOS (Autonomous Cron Job Executable)
# ─────────────────────────────────────────────────────────────

def get_pending_todos(path: Path = DB_PATH, priority: str | None = None) -> list:
    """Return pending todos, optionally filtered by priority."""
    with connect(path) as db:
        if priority:
            return db.execute(
                "SELECT * FROM todos WHERE status='pending' AND priority=? ORDER BY priority, id",
                (priority,),
            ).fetchall()
        return db.execute(
            "SELECT * FROM todos WHERE status='pending' ORDER BY priority, id"
        ).fetchall()


def update_todo(todo_id: int, path: Path = DB_PATH, **fields) -> None:
    """Update todo fields. Allowed: status, blocked_reason, metadata."""
    if not fields:
        return
    allowed = {"status", "blocked_reason", "metadata", "assigned_cron"}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return
    set_parts = [f"{k}=?" for k in keys] + ["updated_at=CURRENT_TIMESTAMP"]
    if "status" in fields:
        if fields["status"] == "completed":
            set_parts.append("completed_at=CURRENT_TIMESTAMP")
    values = [fields[k] for k in keys] + [todo_id]
    with connect(path) as db:
        db.execute(
            f"UPDATE todos SET {', '.join(set_parts)} WHERE id=?",
            values,
        )
        db.commit()


def get_todo(todo_id: int, path: Path = DB_PATH) -> sqlite3.Row | None:
    """Get a single todo by id."""
    with connect(path) as db:
        return db.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()


def list_todos(limit: int = 50, path: Path = DB_PATH, status: str | None = None) -> list:
    """Return todos, optionally filtered by status."""
    with connect(path) as db:
        if status:
            return db.execute(
                "SELECT * FROM todos WHERE status=? ORDER BY priority, id LIMIT ?",
                (status, limit),
            ).fetchall()
        return db.execute(
            "SELECT * FROM todos ORDER BY priority, id LIMIT ?",
            (limit,),
        ).fetchall()


# ─────────────────────────────────────────────────────────────
#  OPENCODE LIFECYCLE MANAGEMENT
# ─────────────────────────────────────────────────────────────

def register_opencode_server_instance(
    power_teams_session_id: str,
    agent_role: str,
    host: str,
    port: int,
    *,
    owner: str = "power_teams",
    managed: bool = True,
    status: str = "running",
    pid: int | None = None,
    cwd: str | None = None,
    command: str | None = None,
    topology: str = "shared",
    roles_json: str | None = None,
    agent_bindings_json: str | None = None,
    project_session_id: str | None = None,
    started_by: str | None = None,
    opencode_session_id: str | None = None,
    project_folder: str | None = None,
    path: Path = DB_PATH,
) -> int:
    project_folder = project_folder or cwd or ""
    with connect(path) as db:
        cur = db.execute(
            """INSERT INTO opencode_server_instances
               (owner, managed, status, power_teams_session_id, agent_role, host, port,
                pid, cwd, command, topology, roles_json, agent_bindings_json,
                project_session_id, started_by, opencode_session_id, project_folder, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (owner, 1 if managed else 0, status, power_teams_session_id, agent_role, host, port,
             pid, cwd, command, topology, roles_json, agent_bindings_json,
             project_session_id, started_by, opencode_session_id, project_folder),
        )
        db.commit()
        return cur.lastrowid


def update_opencode_server_status(
    instance_id: int,
    status: str,
    *,
    stopped_at: str | None = None,
    stop_reason: str | None = None,
    last_error: str | None = None,
    last_error_at: str | None = None,
    error_count: int | None = None,
    last_seen: str | None = None,
    pid: int | None = None,
    path: Path = DB_PATH,
) -> None:
    fields = ["status=?"]
    vals = [status]
    if stopped_at is not None:
        fields.append("stopped_at=?")
        vals.append(stopped_at)
    if stop_reason is not None:
        fields.append("stop_reason=?")
        vals.append(stop_reason)
    if last_error is not None:
        fields.append("last_error=?")
        vals.append(last_error)
    if last_error_at is not None:
        fields.append("last_error_at=?")
        vals.append(last_error_at)
    if error_count is not None:
        fields.append("error_count=?")
        vals.append(error_count)
    if last_seen is not None:
        fields.append("last_seen=?")
        vals.append(last_seen)
    if pid is not None:
        fields.append("pid=?")
        vals.append(pid)
    vals.append(instance_id)
    with connect(path) as db:
        db.execute(f"UPDATE opencode_server_instances SET {', '.join(fields)} WHERE id=?", vals)
        db.commit()


def list_opencode_server_instances(
    *,
    owner: str | None = None,
    status: str | None = None,
    project_session_id: str | None = None,
    path: Path = DB_PATH,
) -> list:
    conditions = []
    vals = []
    if owner is not None:
        conditions.append("owner=?")
        vals.append(owner)
    if status is not None:
        conditions.append("status=?")
        vals.append(status)
    if project_session_id is not None:
        conditions.append("project_session_id=?")
        vals.append(project_session_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with connect(path) as db:
        return db.execute(f"SELECT * FROM opencode_server_instances {where}", vals).fetchall()


def get_opencode_server_by_id(instance_id: int, path: Path = DB_PATH) -> sqlite3.Row | None:
    with connect(path) as db:
        return db.execute("SELECT * FROM opencode_server_instances WHERE id=?", (instance_id,)).fetchone()


def discover_external_opencode_servers(
    ports: list[int] | None = None,
    host: str = "127.0.0.1",
) -> list[dict]:
    import socket
    results = []
    ports = ports or [4096, *range(18750, 18801)]
    for port in ports:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                results.append({"host": host, "port": port, "reachable": True})
        except OSError:
            results.append({"host": host, "port": port, "reachable": False})
    return results


# ─────────────────────────────────────────────────────────────
#  AGENT RUNTIME BINDINGS
# ─────────────────────────────────────────────────────────────

def upsert_agent_binding(
    role: str,
    *,
    server_instance_id: int | None = None,
    host: str = "127.0.0.1",
    port: int = 18765,
    opencode_agent: str = "general",
    model: str | None = None,
    binding_source: str = "auto",
    path: Path = DB_PATH,
) -> None:
    with connect(path) as db:
        existing = db.execute("SELECT id FROM agent_runtime_bindings WHERE role=?", (role,)).fetchone()
        if existing:
            db.execute(
                """UPDATE agent_runtime_bindings
                   SET server_instance_id=?, host=?, port=?, opencode_agent=?, model=?,
                       binding_source=?, updated_at=CURRENT_TIMESTAMP
                   WHERE role=?""",
                (server_instance_id, host, port, opencode_agent, model, binding_source, role),
            )
        else:
            db.execute(
                """INSERT INTO agent_runtime_bindings
                   (role, server_instance_id, host, port, opencode_agent, model, binding_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (role, server_instance_id, host, port, opencode_agent, model, binding_source),
            )
        db.commit()


def get_agent_binding(role: str, path: Path = DB_PATH) -> sqlite3.Row | None:
    with connect(path) as db:
        return db.execute("SELECT * FROM agent_runtime_bindings WHERE role=?", (role,)).fetchone()


def list_agent_bindings(path: Path = DB_PATH) -> list:
    with connect(path) as db:
        return db.execute("SELECT * FROM agent_runtime_bindings ORDER BY role").fetchall()


def clear_agent_binding(role: str, path: Path = DB_PATH) -> None:
    with connect(path) as db:
        db.execute("DELETE FROM agent_runtime_bindings WHERE role=?", (role,))
        db.commit()


# ─────────────────────────────────────────────────────────────
#  RUNTIME POLICIES
# ─────────────────────────────────────────────────────────────

def get_runtime_policy(name: str = "default", path: Path = DB_PATH) -> dict:
    with connect(path) as db:
        row = db.execute("SELECT * FROM runtime_policies WHERE name=?", (name,)).fetchone()
    if row:
        return dict(row)
    return {
        "name": name,
        "close_behavior": "ask",
        "background_mode_enabled": 0,
        "on_backend_exit": "stop_managed_opencode",
        "on_backend_crash_recovery": "ask",
        "on_opencode_crash": "mark_error",
        "max_managed_opencode_servers": 1,
        "default_topology": "shared",
        "default_shared_port": 18765,
        "allow_external_attach": 1,
        "allow_unknown_attach": 0,
    }


def upsert_runtime_policy(
    name: str = "default",
    *,
    close_behavior: str = "ask",
    background_mode_enabled: bool = False,
    on_backend_exit: str = "stop_managed_opencode",
    on_backend_crash_recovery: str = "ask",
    on_opencode_crash: str = "mark_error",
    max_managed_opencode_servers: int = 1,
    default_topology: str = "shared",
    default_shared_port: int = 18765,
    allow_external_attach: bool = True,
    allow_unknown_attach: bool = False,
    path: Path = DB_PATH,
) -> None:
    with connect(path) as db:
        existing = db.execute("SELECT id FROM runtime_policies WHERE name=?", (name,)).fetchone()
        data = {
            "close_behavior": close_behavior,
            "background_mode_enabled": 1 if background_mode_enabled else 0,
            "on_backend_exit": on_backend_exit,
            "on_backend_crash_recovery": on_backend_crash_recovery,
            "on_opencode_crash": on_opencode_crash,
            "max_managed_opencode_servers": max_managed_opencode_servers,
            "default_topology": default_topology,
            "default_shared_port": default_shared_port,
            "allow_external_attach": 1 if allow_external_attach else 0,
            "allow_unknown_attach": 1 if allow_unknown_attach else 0,
        }
        if existing:
            set_parts = [f"{k}=?" for k in data]
            vals = list(data.values()) + [name]
            db.execute(f"UPDATE runtime_policies SET {', '.join(set_parts)}, updated_at=CURRENT_TIMESTAMP WHERE name=?", vals)
        else:
            cols = list(data.keys()) + ["name"]
            vals = list(data.values()) + [name]
            db.execute(f"INSERT INTO runtime_policies ({', '.join(cols)}) VALUES ({', '.join(['?']*len(cols))})", vals)
        db.commit()


# ─────────────────────────────────────────────────────────────
#  CHECKPOINTS
# ─────────────────────────────────────────────────────────────

def create_checkpoint(
    project_session_id: str | None,
    workspace_id: str | None,
    reason: str,
    *,
    status: str = "complete",
    manager_state_json: str | None = None,
    worker_state_json: str | None = None,
    reviewer_state_json: str | None = None,
    chat_state_json: str | None = None,
    agent_registry_snapshot_json: str | None = None,
    active_suggestion_id: int | None = None,
    handoff_version: int | None = None,
    plan_snapshot: str | None = None,
    todos_snapshot_json: str | None = None,
    opencode_servers_snapshot_json: str | None = None,
    runtime_bindings_snapshot_json: str | None = None,
    workspace_path: str | None = None,
    resume_prompt: str | None = None,
    notes: str | None = None,
    path: Path = DB_PATH,
) -> int:
    with connect(path) as db:
        cur = db.execute(
            """INSERT INTO run_checkpoints
               (project_session_id, workspace_id, reason, status,
                manager_state_json, worker_state_json, reviewer_state_json, chat_state_json,
                agent_registry_snapshot_json, active_suggestion_id, handoff_version,
                plan_snapshot, todos_snapshot_json, opencode_servers_snapshot_json,
                runtime_bindings_snapshot_json, workspace_path, resume_prompt, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_session_id, workspace_id, reason, status,
             manager_state_json, worker_state_json, reviewer_state_json, chat_state_json,
             agent_registry_snapshot_json, active_suggestion_id, handoff_version,
             plan_snapshot, todos_snapshot_json, opencode_servers_snapshot_json,
             runtime_bindings_snapshot_json, workspace_path, resume_prompt, notes),
        )
        db.commit()
        return cur.lastrowid


def get_latest_checkpoint(
    project_session_id: str | None = None,
    status: str | None = None,
    path: Path = DB_PATH,
) -> sqlite3.Row | None:
    conditions = []
    vals = []
    if project_session_id is not None:
        conditions.append("project_session_id=?")
        vals.append(project_session_id)
    if status is not None:
        conditions.append("status=?")
        vals.append(status)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with connect(path) as db:
        return db.execute(
            f"SELECT * FROM run_checkpoints {where} ORDER BY created_at DESC LIMIT 1"
        ).fetchone()


def get_checkpoint_by_id(checkpoint_id: int, path: Path = DB_PATH) -> Optional[dict]:
    """Fetch a single checkpoint by its integer ID."""
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM run_checkpoints WHERE id = ?", (checkpoint_id,)
        ).fetchone()
        return dict(row) if row else None


def list_checkpoints(
    project_session_id: str | None = None,
    limit: int = 20,
    path: Path = DB_PATH,
) -> list:
    if project_session_id:
        with connect(path) as db:
            return db.execute(
                "SELECT * FROM run_checkpoints WHERE project_session_id=? ORDER BY created_at DESC LIMIT ?",
                (project_session_id, limit),
            ).fetchall()
    with connect(path) as db:
        return db.execute(
            "SELECT * FROM run_checkpoints ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def update_checkpoint_status(checkpoint_id: int, status: str, notes: str | None = None, path: Path = DB_PATH) -> None:
    with connect(path) as db:
        if notes is not None:
            db.execute("UPDATE run_checkpoints SET status=?, notes=? WHERE id=?", (status, notes, checkpoint_id))
        else:
            db.execute("UPDATE run_checkpoints SET status=? WHERE id=?", (status, checkpoint_id))
        db.commit()


def archive_checkpoint(checkpoint_id: int, notes: str | None = None, path: Path = DB_PATH) -> None:
    with connect(path) as db:
        note_suffix = f" [archived at {datetime.now(timezone.utc).isoformat()}]"
        row = db.execute("SELECT notes FROM run_checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
        existing = row["notes"] if row else ""
        new_notes = (existing or "") + note_suffix + (f": {notes}" if notes else "")
        db.execute("UPDATE run_checkpoints SET status='archived', notes=? WHERE id=?", (new_notes, checkpoint_id))
        db.commit()


# ─────────────────────────────────────────────────────────────
#  ACTIVE WORK DETECTION
# ─────────────────────────────────────────────────────────────

def discover_external_opencode_servers(host: str = "127.0.0.1", port_range: tuple[int, int] = (18750, 18801), timeout: float = 0.1) -> list[dict]:
    import socket
    results = []
    ports = [4096, *range(port_range[0], port_range[1])]
    effective_timeout = min(timeout, 0.03)
    for port in dict.fromkeys(ports):
        try:
            with socket.create_connection((host, port), timeout=effective_timeout):
                results.append({"host": host, "port": port})
        except OSError:
            pass
    return results


def has_active_work(session_id: str | None = None, path: Path = DB_PATH) -> tuple[bool, str]:
    with connect(path) as db:
        busy_agents = db.execute(
            "SELECT COUNT(*) FROM agent_registry WHERE state IN ('busy', 'waiting')"
        ).fetchone()[0]
        if busy_agents > 0:
            return True, f"{busy_agents} agent(s) busy"
        if session_id and session_id != "legacy":
            active_suggestion = db.execute(
                "SELECT COUNT(*) FROM suggestion_queue "
                "WHERE status NOT IN ('done', 'rejected', 'archived') AND session_id=?",
                (session_id,),
            ).fetchone()[0]
        else:
            active_suggestion = db.execute(
                "SELECT COUNT(*) FROM suggestion_queue "
                "WHERE status NOT IN ('done', 'rejected', 'archived') AND session_id IS NULL"
            ).fetchone()[0]
        if active_suggestion > 0:
            return True, f"{active_suggestion} active suggestion(s)"
        running_checkpoints = db.execute(
            "SELECT COUNT(*) FROM run_checkpoints "
            "WHERE status IN ('complete', 'partial') "
            "AND (? IS NULL OR ? = 'legacy' OR project_session_id=?)",
            (session_id, session_id, session_id),
        ).fetchone()[0]
        if running_checkpoints > 0:
            return True, f"{running_checkpoints} checkpoint(s)"
    return False, ""


_DEFAULT_POLICY = {
    "name": "default",
    "close_behavior": "ask",
    "background_mode_enabled": False,
    "on_backend_exit": "stop_managed_opencode",
    "on_backend_crash_recovery": "ask",
    "on_opencode_crash": "mark_error",
    "max_managed_opencode_servers": 1,
    "default_topology": "shared",
    "default_shared_port": 18765,
    "allow_external_attach": True,
    "allow_unknown_attach": False,
}


def get_runtime_policy(path: Path = DB_PATH) -> dict:
    """Read the singleton runtime policy row, or return defaults if missing."""
    conn = connect(path)
    row = conn.execute("SELECT * FROM runtime_policy WHERE id = 1").fetchone()
    if row is None:
        return dict(_DEFAULT_POLICY)
    row = dict(row)
    # Normalize boolean/int fields
    for key in ("background_mode_enabled", "allow_external_attach", "allow_unknown_attach"):
        row[key] = bool(row.get(key, 0))
    for key in ("max_managed_opencode_servers", "default_shared_port"):
        row[key] = int(row.get(key, 0))
    return row


def upsert_runtime_policy(
    name: str = "default",
    close_behavior: str = "ask",
    background_mode_enabled: bool = False,
    on_backend_exit: str = "stop_managed_opencode",
    on_backend_crash_recovery: str = "ask",
    on_opencode_crash: str = "mark_error",
    max_managed_opencode_servers: int = 1,
    default_topology: str = "shared",
    default_shared_port: int = 18765,
    allow_external_attach: bool = True,
    allow_unknown_attach: bool = False,
    path: Path = DB_PATH,
) -> dict:
    """ INSERT-or-UPDATE the singleton runtime policy row."""
    conn = connect(path)
    conn.execute(
        """
        INSERT INTO runtime_policy
            (id, name, close_behavior, background_mode_enabled, on_backend_exit,
             on_backend_crash_recovery, on_opencode_crash, max_managed_opencode_servers,
             default_topology, default_shared_port, allow_external_attach, allow_unknown_attach,
             updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            name                       = excluded.name,
            close_behavior             = excluded.close_behavior,
            background_mode_enabled    = excluded.background_mode_enabled,
            on_backend_exit            = excluded.on_backend_exit,
            on_backend_crash_recovery  = excluded.on_backend_crash_recovery,
            on_opencode_crash          = excluded.on_opencode_crash,
            max_managed_opencode_servers = excluded.max_managed_opencode_servers,
            default_topology           = excluded.default_topology,
            default_shared_port        = excluded.default_shared_port,
            allow_external_attach      = excluded.allow_external_attach,
            allow_unknown_attach       = excluded.allow_unknown_attach,
            updated_at                 = CURRENT_TIMESTAMP
        """,
        (
            name, close_behavior, int(background_mode_enabled), on_backend_exit,
            on_backend_crash_recovery, on_opencode_crash, max_managed_opencode_servers,
            default_topology, default_shared_port, int(allow_external_attach), int(allow_unknown_attach),
        ),
    )
    conn.commit()
    return get_runtime_policy(path=path)


def get_runtime_status_summary(session_id: str | None = None, path: Path = DB_PATH) -> dict:
    policy = get_runtime_policy(path=path)
    managed = list_opencode_server_instances(owner="power_teams", status="running", path=path)
    external = list_opencode_server_instances(owner="external", status="running", path=path)
    unknown = list_opencode_server_instances(owner="unknown", status="running", path=path)
    active_work, active_work_reason = has_active_work(session_id=session_id, path=path)
    latest_cp = get_latest_checkpoint(path=path)
    bindings = list_agent_bindings(path=path)
    return {
        "policy": policy,
        "managed_opencode_count": len(managed),
        "external_opencode_count": len(external),
        "unknown_opencode_count": len(unknown),
        "active_work": active_work,
        "active_work_reason": active_work_reason,
        "last_checkpoint": dict(latest_cp) if latest_cp else None,
        "role_bindings": [dict(b) for b in bindings],
    }
