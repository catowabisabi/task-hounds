"""api.deps — FastAPI dependencies.

Provides:
  get_db()                  — yields a sqlite3 connection
  get_active_session_id()   — returns the active project session id, or 400
  active_session_id_param   — Query() with default=None; if None, uses active session
"""
from __future__ import annotations

from fastapi import HTTPException, Query

from task_hounds_api.db import connect
from task_hounds_api.db.ops import project as db_project


def get_db():
    """Yield a sqlite3 connection. FastAPI dependency."""
    db = connect()
    try:
        yield db
    finally:
        db.close()


def get_active_session_id() -> str:
    """Return the active project session id. Raise 400 if none.

    Use this for WRITE routes where a session is required.
    """
    active = db_project.get_active_session()
    if not active:
        raise HTTPException(status_code=400, detail="no active project session")
    return active["id"]


def get_active_session_id_or_none() -> str | None:
    """Return the active project session id, or None if there isn't one.

    Use this for READ routes — the UI needs to render an empty state
    on first load, before the user has created any project.
    """
    active = db_project.get_active_session()
    return active["id"] if active else None


def active_session_id_param() -> str:
    """FastAPI Query dependency. session_id query param, defaulting to active.

    Use in route signatures:
        def my_route(session_id: str = Depends(active_session_id_param)):
    Or with explicit Query:
        def my_route(session_id: str | None = Query(default=None)):
            if not session_id:
                session_id = get_active_session_id()
    """
    return Query(default=None, description="Project session id. Defaults to active session.")


def resolve_session_id(session_id: str | None) -> str | None:
    """Resolve session_id: if None, fall back to active. May return None.

    For READ endpoints — returns None when no session is available,
    so the caller can return an empty response.

    The single rule:
      - session_id passed in  -> use it
      - session_id None        -> use active session (or None if no active)
      - no active session      -> returns None (read routes return empty)
    """
    if session_id:
        return session_id
    return get_active_session_id_or_none()


def require_session_id(session_id: str | None) -> str:
    """Resolve session_id or raise 400. For WRITE endpoints.

    The single rule:
      - session_id passed in  -> use it
      - session_id None        -> use active session, or raise 400
    """
    if session_id:
        return session_id
    return get_active_session_id()


def session_to_workspace(sess: dict | None) -> dict:
    """Map a project_sessions DB row to the public Workspace shape.

    The UI's Workspace interface reads label/path/active/path_missing;
    the DB stores name/workspace_path/is_active/path_missing. Without
    this mapping the WorkspacesPanel renders empty labels and "(no path)".
    """
    if not sess:
        return {}
    return {
        "id": sess.get("id"),
        "label": sess.get("name") or "",
        "path": sess.get("workspace_path") or "",
        "active": bool(sess.get("is_active")),
        "path_missing": bool(sess.get("path_missing")),
        "created_at": sess.get("created_at"),
        "updated_at": sess.get("updated_at"),
    }
