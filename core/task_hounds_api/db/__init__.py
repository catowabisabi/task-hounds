"""SQLite connection + schema init for Task Hounds.

Single source of DB truth: core/db/power_teams.db. All layers read/write
through this DB. No alternate DBs.

Module shape:
  ROOT            = project root
  DATA_DIR        = core/db/
  DB_PATH         = core/db/power_teams.db  (overridable by POWER_TEAMS_DB env)
  SCHEMA_PATH     = core/db/schema.sql
  MIGRATIONS_DIR  = core/db/migrations/

Public API:
  connect()        -> sqlite3.Connection (row factory = sqlite3.Row, WAL on)
  init_db()        -> create tables from schema.sql, run migrations
  reset_db()       -> delete file and reinit (for tests)
  apply_migration() -> run one .sql file with statement-by-statement safety
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

ROOT = Path(
    os.environ.get(
        "TASK_HOUNDS_APP_ROOT",
        str(Path(__file__).resolve().parents[3]),
    )
).resolve()
DATA_DIR = ROOT / "core" / "db"
DB_PATH = Path(os.environ.get("POWER_TEAMS_DB", str(DATA_DIR / "power_teams.db")))
SCHEMA_PATH = DATA_DIR / "schema.sql"
MIGRATIONS_DIR = DATA_DIR / "migrations"


def _resolve_db_path(path: Path | None = None) -> Path:
    """Resolve the DB path. When `path` is None, re-read POWER_TEAMS_DB at
    call time so tests that monkeypatch the env var get true isolation.

    DB_PATH is still exported for backwards compatibility — it is the
    path captured at module-import time and is the right value in
    production where the env var does not change after startup.
    """
    import logging
    logger = logging.getLogger(__name__)

    if path is not None:
        result = Path(path)
    else:
        env_val = os.environ.get("POWER_TEAMS_DB")
        result = Path(env_val if env_val else str(DATA_DIR / "power_teams.db"))

    logger.warning(f"[DB-PATH] connect() resolved path: {result}")
    return result


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection. Creates parent dirs, sets WAL mode, returns row dicts."""
    p = _resolve_db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p)
    db.execute("PRAGMA busy_timeout = 5000")
    db.execute("PRAGMA journal_mode = WAL")
    db.row_factory = sqlite3.Row
    return db


def _split_into_executable_units(text: str) -> list[str]:
    units, current = [], ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        current += line + "\n"
        if sqlite3.complete_statement(current):
            units.append(current.strip())
            current = ""
    if current.strip():
        units.append(current.strip())
    return units


def _apply_script(db: sqlite3.Connection, sql_text: str) -> None:
    """Run a SQL script, ignoring harmless re-runs (duplicate column, exists)."""
    for part in _split_into_executable_units(sql_text):
        if not part:
            continue
        try:
            db.execute(part)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                continue
            raise
    db.commit()


# Phase-8 (P1 migration): expected column set for the 3 runtime
# tables. CREATE TABLE IF NOT EXISTS in schema.sql is a no-op for
# tables that already exist, so a v0.3 DB with a partial
# agent_runtime_bindings (e.g. only id and role) would not get
# the missing columns on init_db(). The fix is to extract the
# expected column set from schema.sql at module load and补 any
# missing columns via ALTER TABLE ADD COLUMN after _apply_script.
_RUNTIME_TABLES = (
    "agent_registry",
    "agent_runtime_bindings",
    "run_checkpoints",
    "runtime_policies",
)


def _extract_table_columns(sql_text: str, table_name: str) -> list[str]:
    """Extract the column-definition lines for a CREATE TABLE
    statement. Returns a list of (col_name, full_def) tuples.
    We don't fully parse the SQL — we just need each column
    name and its full type/constraint definition so we can
    replay it in an ALTER TABLE ADD COLUMN statement."""
    out: list[tuple[str, str]] = []
    pattern = re.compile(
        rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{re.escape(table_name)}\s*\(([^;]*)\)",
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(sql_text)
    if not m:
        return []
    body = m.group(1)
    for line in body.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped or stripped.startswith("--") or stripped.startswith("/*"):
            continue
        if stripped.startswith("PRIMARY KEY") or stripped.startswith("FOREIGN KEY") or stripped.startswith("UNIQUE") or stripped.startswith("CONSTRAINT") or stripped.startswith("CHECK"):
            continue
        first = stripped.split(None, 1)[0].strip('"[]`')
        if first.upper() in {"PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "CHECK"}:
            continue
        out.append((first, stripped))
    return out


def _complete_runtime_table_columns(db: sqlite3.Connection) -> None:
    """For each runtime table, compare the actual columns
    (PRAGMA table_info) against the expected columns parsed
    from schema.sql. ALTER TABLE ADD COLUMN for any missing
    ones. Idempotent: ALTER TABLE ADD COLUMN on an existing
    column raises 'duplicate column' which _apply_script
    already swallows, and we use ALTER TABLE ... ADD COLUMN
    with the same type/constraint definition so the new
    column matches the canonical schema.

    This is the fix for the audit's P1 migration bug:
    CREATE TABLE IF NOT EXISTS is a no-op for tables that
    already exist, so a v0.3 DB with a partial runtime
    table would not get its missing columns on init_db().
    """
    if not SCHEMA_PATH.exists():
        return
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    for table in _RUNTIME_TABLES:
        expected = _extract_table_columns(schema_text, table)
        if not expected:
            continue
        # PRAGMA table_info returns empty for missing tables (not
        # an error), so explicitly check sqlite_master first.
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            # Table doesn't exist yet — schema.sql will create
            # it on the next _apply_script run.
            continue
        actual = {
            r[1]
            for r in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for col_name, col_def in expected:
            if col_name in actual:
                continue
            # SQLite refuses to add a column to an existing table
            # when the column has: (a) a non-constant default like
            # CURRENT_TIMESTAMP, (b) NOT NULL without a constant
            # default (existing rows would violate the constraint),
            # or (c) UNIQUE (existing rows may violate the constraint).
            # Strip all three. The column is added as nullable,
            # non-unique; existing rows get NULL; the application
            # fills in new rows.
            col_def_clean = re.sub(
                r"\s+DEFAULT\s+(?:CURRENT_TIMESTAMP|CURRENT_DATE|CURRENT_TIME|\([^)]*\))",
                "",
                col_def,
                flags=re.IGNORECASE,
            )
            col_def_clean = re.sub(r"\s+NOT\s+NULL\b", "", col_def_clean, flags=re.IGNORECASE)
            col_def_clean = re.sub(r"\s+UNIQUE\b", "", col_def_clean, flags=re.IGNORECASE)
            sql = f"ALTER TABLE {table} ADD COLUMN {col_def_clean}"
            try:
                db.execute(sql)
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    continue
                raise
    db.commit()


def init_db(path: Path | None = None) -> None:
    """Create tables from schema.sql and run any pending migrations."""
    p = _resolve_db_path(path)
    with connect(p) as db:
        # Phase-8 (P1 migration): BEFORE running schema.sql,补
        # any missing columns on the 3 runtime tables. This is
        # critical because schema.sql's CREATE TABLE statements
        # for OTHER tables contain FOREIGN KEY references to
        # columns like server_instance_id; if agent_runtime_bindings
        # is missing that column, the FK validation in those
        # CREATE TABLE statements fails. We must ensure the
        # runtime tables have all expected columns BEFORE
        # schema.sql runs.
        _complete_runtime_table_columns(db)
        if SCHEMA_PATH.exists():
            _apply_script(db, SCHEMA_PATH.read_text(encoding="utf-8"))
        if MIGRATIONS_DIR.exists():
            for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                _apply_script(db, f.read_text(encoding="utf-8"))
        # Idempotent re-run: after schema.sql + migrations, check
        # again in case any column was added by a later migration
        # that wasn't in schema.sql at module load time.
        _complete_runtime_table_columns(db)


def reset_db(path: Path | None = None) -> None:
    """Delete the DB file and reinit. For tests only."""
    p = _resolve_db_path(path)
    if p.exists():
        p.unlink()
    init_db(p)
