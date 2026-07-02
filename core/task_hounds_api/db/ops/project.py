"""DB ops for project_sessions (workspaces).

Pure CRUD. No business logic. No import from api/ or workflow/.
"""
from __future__ import annotations

import os
from pathlib import Path
from task_hounds_api.db import connect, DB_PATH


ALLOWED_UPDATE_FIELDS = {
    "name",
    "workspace_name",
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


def _path_missing(workspace_path: str | None) -> int:
    if not workspace_path:
        return 1
    return 0 if Path(workspace_path).is_dir() else 1


def refresh_path_missing(path: Path | None = None) -> None:
    """Refresh cached missing-folder flags without touching updated_at."""
    with connect(path) as db:
        rows = db.execute("SELECT id, workspace_path, path_missing FROM project_sessions").fetchall()
        changed = False
        for row in rows:
            missing = _path_missing(row["workspace_path"])
            if int(row["path_missing"] or 0) == missing:
                continue
            db.execute(
                "UPDATE project_sessions SET path_missing=? WHERE id=?",
                (missing, row["id"]),
            )
            changed = True
        if changed:
            db.commit()


def list_sessions(path: Path | None = None) -> list[dict]:
    refresh_path_missing(path)
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM project_sessions ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str, path: Path | None = None) -> dict | None:
    refresh_path_missing(path)
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM project_sessions WHERE id=?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def create_session(
    session_id: str,
    workspace_path: str,
    name: str = "",
    workspace_name: str | None = None,
    path: Path | None = None,
) -> dict:
    normalized = _normalize(workspace_path)
    ws_name = workspace_name if workspace_name else name
    with connect(path) as db:
        db.execute("UPDATE project_sessions SET is_active=0, updated_at=CURRENT_TIMESTAMP")
        db.execute(
            """
            INSERT INTO project_sessions
                (id, name, workspace_name, workspace_path, is_active, name_generated, path_missing, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                workspace_name=excluded.workspace_name,
                workspace_path=excluded.workspace_path,
                is_active=1,
                name_generated=excluded.name_generated,
                path_missing=excluded.path_missing,
                updated_at=CURRENT_TIMESTAMP
            """,
            (session_id, name, ws_name, normalized),
        )
        db.commit()
    return get_session(session_id, path) or {}


def activate_session(session_id: str, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute("UPDATE project_sessions SET is_active=0, updated_at=CURRENT_TIMESTAMP")
        updated = db.execute(
            "UPDATE project_sessions SET is_active=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (session_id,),
        )
        if updated.rowcount != 1:
            db.rollback()
            raise ValueError(f"project session {session_id!r} not found")
        db.commit()


def get_active_session(path: Path | None = None) -> dict | None:
    refresh_path_missing(path)
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM project_sessions WHERE is_active=1 ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def delete_session(session_id: str, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute("DELETE FROM project_sessions WHERE id=?", (session_id,))
        db.commit()


def delete_workspace_sessions(session_id: str, path: Path | None = None) -> list[str]:
    """Delete every session belonging to the same workspace as session_id."""
    with connect(path) as db:
        anchor = db.execute(
            "SELECT workspace_path FROM project_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if not anchor:
            return []
        workspace_key = os.path.normcase(os.path.normpath(anchor["workspace_path"] or ""))
        rows = db.execute(
            "SELECT id, workspace_path FROM project_sessions"
        ).fetchall()
        session_ids = [
            str(row["id"])
            for row in rows
            if os.path.normcase(os.path.normpath(row["workspace_path"] or "")) == workspace_key
        ]
        if not session_ids:
            return []
        placeholders = ", ".join("?" for _ in session_ids)
        db.execute(
            f"DELETE FROM project_sessions WHERE id IN ({placeholders})",
            session_ids,
        )
        db.commit()
    return session_ids


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


def generate_session_name(directive: str, max_words: int = 6, max_chars: int = 80) -> str:
    """P7 id 122 (simple non-LLM fallback): derive a short
    readable session name from a directive.

    The 0c44ba2 _generate_session_name called the opencode CLI
    to ask an LLM for a 3-5 word title. That subprocess call is
    not portable to the new architecture (no LLM helper here).
    This fallback derives the name from the first `max_words`
    words of the directive, mirroring the LAST RESORT path the
    old code fell back to when the LLM produced no output
    (`name = " ".join(words[:6])`).

    Callers that want a richer name should set `name` explicitly
    on session creation. This helper is for the auto-naming
    UX feature only.
    """
    if not directive or not directive.strip():
        return ""
    words = directive.strip().split()
    name = " ".join(words[:max_words])
    name = name.strip().rstrip(".").strip()[:max_chars]
    return name


def apply_generated_name(
    session_id: str, directive: str, path: Path | None = None
) -> str:
    """P7 id 122: derive a name from `directive` and persist it
    on the project session with name_generated=1. Returns the
    generated name (or "" if the directive was empty / no name
    could be derived). Idempotent: re-calling with the same
    session re-sets the same name.
    """
    name = generate_session_name(directive)
    if not name:
        return ""
    update_session(session_id, name=name, name_generated=1, path=path)
    return name


def path_already_used(workspace_path: str, exclude_session_id: str | None = None) -> bool:
    import os as _os
    normalized = _normalize(workspace_path)
    with connect() as db:
        if exclude_session_id:
            row = db.execute(
                "SELECT workspace_path FROM project_sessions WHERE id != ?",
                (exclude_session_id,),
            ).fetchall()
        else:
            row = db.execute("SELECT workspace_path FROM project_sessions").fetchall()
    for existing in row:
        ep = existing["workspace_path"] or ""
        if _normalize(ep) == normalized:
            return True
        if _os.path.normcase(_os.path.normpath(ep)) == _os.path.normcase(_os.path.normpath(workspace_path)):
            return True
    return False


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
