"""api.routes.projects — CRUD for project sessions (workspaces)."""
from __future__ import annotations

import uuid
from fastapi import APIRouter, HTTPException

from task_hounds_api.db.ops import project as db_project
from task_hounds_api.api import schemas
from task_hounds_api.api.deps import session_to_workspace

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def list_sessions() -> list[dict]:
    return [session_to_workspace(s) for s in db_project.list_sessions()]


@router.post("")
def create_session(body: schemas.ProjectSessionCreate) -> dict:
    path = (body.workspace_path or "").strip()
    name = (body.name or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="workspace_path is required")
    if db_project.path_already_used(path):
        raise HTTPException(status_code=409, detail="workspace_path already in use")
    sid = "ps_" + uuid.uuid4().hex[:8]
    sess = db_project.create_session(sid, path, name)
    return session_to_workspace(sess)


@router.get("/active")
def get_active() -> dict | None:
    return session_to_workspace(db_project.get_active_session())


@router.post("/{session_id}/activate")
def activate_session(session_id: str) -> dict:
    if not db_project.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    db_project.activate_session(session_id)
    return {"activated": session_id}


@router.get("/{session_id}")
def get_session(session_id: str) -> dict:
    sess = db_project.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")
    return session_to_workspace(sess)


@router.patch("/{session_id}")
def update_session(session_id: str, body: schemas.ProjectSessionUpdate) -> dict:
    if not db_project.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    db_project.update_session(session_id, **body.model_dump(exclude_none=True))
    return session_to_workspace(db_project.get_session(session_id))


@router.delete("/{session_id}")
def delete_session(session_id: str) -> dict:
    db_project.delete_session(session_id)
    return {"deleted": session_id}


@router.post("/{session_id}/check-fingerprint")
def check_fingerprint(session_id: str, workspace_path: str) -> dict:
    mismatch, msg = db_project.check_fingerprint_mismatch(session_id, workspace_path)
    return {"mismatch": mismatch, "message": msg}


# ── /api/sessions + /api/project-sessions aliases (Phase 6) ──────────────
# The UI uses the older /api/sessions and /api/project-sessions/{id}/switch
# paths. The compat duplicates were deleted; the authoritative
# versions live here as proper APIRouter modules so the UI keeps
# working without a client-side rewrite.

sessions_router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@sessions_router.get("")
def list_sessions_legacy() -> list[dict]:
    return [session_to_workspace(s) for s in db_project.list_sessions()]


project_sessions_router = APIRouter(
    prefix="/api/project-sessions", tags=["project-sessions"]
)


@project_sessions_router.post("/{session_id}/switch")
def switch_session(session_id: str) -> dict:
    if not db_project.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    db_project.activate_session(session_id)
    return {"switched": session_id}
