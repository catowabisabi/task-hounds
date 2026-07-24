"""api.routes.projects — CRUD for project sessions (workspaces)."""
from __future__ import annotations

import uuid
from fastapi import APIRouter, HTTPException

from task_hounds_api.db.ops import project as db_project
from task_hounds_api.db.ops import graphflow_jobs as db_jobs
from task_hounds_api.api import schemas
from task_hounds_api.api.deps import session_to_workspace

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _active_graphflow_job() -> dict | None:
    jobs = db_jobs.active()
    return jobs[0] if jobs else None


def _guard_no_running_graphflow_for_session_switch(target_session_id: str) -> None:
    """Changing the active project while GraphFlow is running can invalidate
    the OpenCode session/port currently used by Manager, Worker, or Reviewer.
    Keep the UI on the running project until the job finishes or is stopped.
    """
    job = _active_graphflow_job()
    if not job:
        return
    running_session = str(job.get("project_session_id") or "")
    if running_session == target_session_id:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "graphflow_running",
            "message": (
                "Cannot switch project session while GraphFlow is running. "
                "Stop or finish the current run before switching sessions."
            ),
            "run_id": job.get("run_id"),
            "project_session_id": running_session,
        },
    )


@router.get("")
def list_sessions() -> list[dict]:
    return [session_to_workspace(s) for s in db_project.list_sessions()]


@router.post("")
def create_session(body: schemas.ProjectSessionCreate) -> dict:
    from pathlib import Path as _Path
    path = (body.workspace_path or "").strip()
    name = (body.name or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="workspace_path is required")
    if db_project.path_already_used(path):
        raise HTTPException(status_code=409, detail="workspace_path already in use")
    sid = "ps_" + uuid.uuid4().hex[:8]
    workspace_name = name if name else _Path(path).name.replace(" ", "-")
    session_name = name if name else "New Session"
    sess = db_project.create_session(sid, path, session_name, workspace_name)
    return session_to_workspace(sess)


@router.get("/active")
def get_active() -> dict | None:
    return session_to_workspace(db_project.get_active_session())


@router.post("/{session_id}/activate")
def activate_session(session_id: str) -> dict:
    if not db_project.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    _guard_no_running_graphflow_for_session_switch(session_id)
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
def list_sessions_legacy() -> dict:
    """Legacy /api/sessions envelope.

    The UI and 0c44ba2 API contract expect an object with live sessions
    and counts, not the bare project-session list used internally.
    """
    from task_hounds_api.db.ops import runtime as db_rt

    live = [session_to_workspace(s) for s in db_project.list_sessions()]
    archived = db_rt.list_archived()
    return {
        "live": live,
        "live_count": len(live),
        "archived_count": len(archived),
    }


project_sessions_router = APIRouter(
    prefix="/api/project-sessions", tags=["project-sessions"]
)


@project_sessions_router.post("/{session_id}/switch")
def switch_session(session_id: str) -> dict:
    if not db_project.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    _guard_no_running_graphflow_for_session_switch(session_id)
    db_project.activate_session(session_id)
    return {"switched": session_id}


@project_sessions_router.patch("/{session_id}")
def update_project_session(session_id: str, body: schemas.ProjectSessionUpdate) -> dict:
    if not db_project.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    db_project.update_session(session_id, **body.model_dump(exclude_none=True))
    return session_to_workspace(db_project.get_session(session_id))
