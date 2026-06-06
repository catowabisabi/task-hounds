"""api.main - FastAPI app entry point.

Run with:
    uvicorn task_hounds_api.api.main:app --port 8765

Or programmatically:
    from task_hounds_api.api import create_app
    app = create_app()
"""
from __future__ import annotations

import logging
import os
import string
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from task_hounds_api.db import ROOT, init_db
from task_hounds_api.db.ops import project as db_project
from task_hounds_api.db.ops import agent as db_agent
from task_hounds_api.db import connect
from task_hounds_api.api.routes import projects, agents, todos, workflow, chat, runtime, streams, settings, compat
from task_hounds_api.api.debug_logs import write_debug_batch
from task_hounds_api.opencode.runtime_manager import RuntimeManager
from task_hounds_api.workflow.signals import clear_runtime_agent_states

logger = logging.getLogger(__name__)


def _default_project_path() -> Path:
    """Return the platform-appropriate default project folder.

    Windows: C:\\task-hounds-projects\\default-project (or first available drive)
    Linux/macOS: ~/task-hounds-projects/default-project

    Creates the full directory path if missing.
    """
    if os.name == "nt":
        candidate = Path("C:/task-hounds-projects/default-project")
        if not candidate.parent.parent.exists():
            for letter in string.ascii_uppercase:
                drive = Path(f"{letter}:/")
                if drive.exists():
                    candidate = drive / "task-hounds-projects" / "default-project"
                    break
    else:
        candidate = Path.home() / "task-hounds-projects" / "default-project"

    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _ensure_default_project() -> None:
    """Create and activate a default project if none exists.

    Ensures the dashboard renders something useful on first load.
    The user can rename, delete, or create more projects via the UI.
    """
    if db_project.list_sessions():
        return
    default_path = _default_project_path()
    sid = "ps_default"
    db_project.create_session(
        session_id=sid,
        workspace_path=str(default_path),
        name="default-project",
    )
    db_project.activate_session(sid)
    print(f"[startup] Created default project at: {default_path}")


def _ensure_default_agents() -> None:
    """Seed the 4 default agents (manager, worker, reviewer, chat) if missing."""
    if db_agent.list_agents():
        return
    db_agent.seed_default_agents()
    print("[startup] Seeded default agents: manager, worker, reviewer, chat")


def _clear_stale_agent_states_on_startup() -> None:
    """Clear old busy timers when no directive is actually running.

    A previous process can be killed after setting manager=busy/digest but
    before the cleanup path runs. The UI reads agent_registry directly, so
    stale busy rows otherwise look like work has been running for hours.
    """
    with connect() as db:
        running = db.execute(
            "SELECT 1 FROM user_directives WHERE status='running' LIMIT 1"
        ).fetchone()
    if not running:
        clear_runtime_agent_states()


def create_app() -> FastAPI:
    """Build and return the FastAPI app. DB is initialized on first call."""
    init_db()
    _ensure_default_project()
    _ensure_default_agents()
    _clear_stale_agent_states_on_startup()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            rm = RuntimeManager.instance()
            rm.reconcile_servers()
            managed_ok = bool(rm.ensure_managed_running())
            cred_warnings = rm.validate_credentials()
            if managed_ok and not cred_warnings:
                rm.auto_bind_four_roles()
            else:
                if not managed_ok:
                    logger.warning("managed opencode not reachable; skipping auto-bind")
                if cred_warnings:
                    logger.warning(
                        "credentials missing — auto-bind skipped (%d warning(s))",
                        len(cred_warnings),
                    )
            logger.info("runtime ready: %s", rm.get_managed_health())
        except Exception as exc:
            logger.warning("startup runtime init failed (continuing): %s", exc)
        try:
            yield
        finally:
            try:
                RuntimeManager.instance().stop_all()
            except Exception:
                pass

    app = FastAPI(title="Task Hounds API", version="2.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(projects.router)
    app.include_router(projects.sessions_router)
    app.include_router(projects.project_sessions_router)
    app.include_router(agents.router)
    app.include_router(todos.router)
    app.include_router(workflow.router)
    app.include_router(workflow.manager_messages_root)
    app.include_router(workflow.flow01_router)
    app.include_router(chat.router)
    app.include_router(runtime.router)
    app.include_router(streams.router)
    app.include_router(settings.router)
    # Compat shim - keep old UI endpoints working until UI is rebuilt
    app.include_router(compat.router)

    @app.get("/api/ping")
    def ping() -> dict:
        return {"ok": True}

    @app.get("/api/health")
    def health() -> dict:
        from task_hounds_api.opencode import lifecycle as oc_lifecycle
        from task_hounds_api.db.ops import project as db_project
        lc = oc_lifecycle.OpenCodeLifecycle()
        active = db_project.get_active_session()
        return {
            "ok": True,
            "active_project_session": active["id"] if active else None,
            "opencode": lc.health(),
        }

    @app.post("/api/debug-logs")
    async def debug_logs(request: Request) -> dict:
        """Persist frontend debug events to one file per UI session."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        return write_debug_batch(body if isinstance(body, dict) else {})

    dist_dir = ROOT / "ui" / "web" / "dist"
    if dist_dir.exists():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="ui")

    return app


app = create_app()
