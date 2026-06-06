"""DB ops for project_sessions (workspaces).

Pure CRUD. No business logic. No import from api/ or workflow/.
"""
from __future__ import annotations

import os
from pathlib import Path
from task_hounds_api.db import connect, DB_PATH


ALLOWED_UPDATE_FIELDS = {
    "name",
    "manager_session_id",
    "worker_session_id",
    "reviewer_session_id",
    "chat_session_id",
    "is_active",
    "name_generated",
    "workspace_path",
    "path_missing",
    "workspace_fingerprint",
}


def _normalize(p: str) -> str:
    return os.path.realpath(Path(p).resolve())


def list_sessions(path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM project_sessions ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM project_sessions WHERE id=?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def create_session(
    session_id: str,
    workspace_path: str,
    name: str = "",
    path: Path | None = None,
) -> dict:
    normalized = _normalize(workspace_path)
    with connect(path) as db:
        db.execute("UPDATE project_sessions SET is_active=0, updated_at=CURRENT_TIMESTAMP")
        db.execute(
            """
            INSERT INTO project_sessions
                (id, name, workspace_path, is_active, name_generated, path_missing, created_at, updated_at)
            VALUES (?, ?, ?, 1, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                workspace_path=excluded.workspace_path,
                is_active=1,
                name_generated=excluded.name_generated,
                path_missing=excluded.path_missing,
                updated_at=CURRENT_TIMESTAMP
            """,
            (session_id, name, normalized),
        )
        db.commit()
    return get_session(session_id, path) or {}


def activate_session(session_id: str, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute("UPDATE project_sessions SET is_active=0, updated_at=CURRENT_TIMESTAMP")
        db.execute(
            "UPDATE project_sessions SET is_active=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (session_id,),
        )
        db.commit()


def get_active_session(path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM project_sessions WHERE is_active=1 ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def delete_session(session_id: str, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute("DELETE FROM project_sessions WHERE id=?", (session_id,))
        db.commit()


def update_session(session_id: str, path: Path | None = None, **fields) -> None:
    safe_fields = {k: v for k, v in fields.items() if k in ALLOWED_UPDATE_FIELDS}
    if "workspace_path" in safe_fields and safe_fields["workspace_path"]:
        safe_fields["workspace_path"] = _normalize(str(safe_fields["workspace_path"]))
    if not safe_fields:
        return
    keys = list(safe_fields)
    sets = ", ".join(f"{k}=?" for k in keys) + ", updated_at=CURRENT_TIMESTAMP"
    values = [safe_fields[k] for k in keys] + [session_id]
    with connect(path) as db:
        db.execute(f"UPDATE project_sessions SET {sets} WHERE id=?", values)
        db.commit()


def path_already_used(workspace_path: str, exclude_session_id: str | None = None) -> bool:
    normalized = _normalize(workspace_path)
    with connect() as db:
        if exclude_session_id:
            row = db.execute(
                "SELECT 1 FROM project_sessions WHERE workspace_path=? AND id != ?",
                (normalized, exclude_session_id),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT 1 FROM project_sessions WHERE workspace_path=?",
                (normalized,),
            ).fetchone()
    return row is not None


def fingerprint_for(workspace_path: str) -> str | None:
    """Return a short fingerprint string for a workspace, or None."""
    p = Path(workspace_path)
    git = p / ".git" / "config"
    if git.exists():
        return "git:" + git.read_text(encoding="utf-8", errors="ignore")[:200]
    pkg = p / "package.json"
    if pkg.exists():
        return "npm:" + pkg.read_text(encoding="utf-8", errors="ignore")[:200]
    pyr = p / "pyproject.toml"
    if pyr.exists():
        return "py:" + pyr.read_text(encoding="utf-8", errors="ignore")[:200]
    return None


def check_fingerprint_mismatch(session_id: str, new_workspace_path: str) -> tuple[bool, str]:
    """Return (is_mismatch, message). Empty message if no mismatch."""
    new_fp = fingerprint_for(new_workspace_path)
    with connect() as db:
        row = db.execute(
            "SELECT workspace_fingerprint FROM project_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
    if row is None:
        return False, ""
    old_fp = row["workspace_fingerprint"]
    if not old_fp or not old_fp.startswith("git:"):
        return False, ""
    if new_fp and old_fp != new_fp:
        return True, f"Fingerprint mismatch: expected {old_fp[:30]}..., got {new_fp[:30]}..."
    return False, ""
