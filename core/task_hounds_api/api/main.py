"""api.main - FastAPI app entry point.

Run with:
    uvicorn task_hounds_api.api.main:app --port 8765

Or programmatically:
    from task_hounds_api.api import create_app
    app = create_app()
"""
from __future__ import annotations

import logging
import json
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
from task_hounds_api.db.ops import runtime as db_rt
from task_hounds_api.db.ops import workflow as db_wf
from task_hounds_api.db.ops import todo as db_todo
from task_hounds_api.db import connect
from task_hounds_api.api.routes import projects, agents, todos, workflow, chat, manager_chat, runtime, streams, settings, compat, questions
from task_hounds_api.api import schemas
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
        workspace_name="default-project",
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


def _normalize_stale_workflow_statuses_on_startup() -> None:
    changes = db_wf.normalize_stale_statuses()
    if any(changes.values()):
        logger.warning("normalized stale workflow statuses: %s", changes)


def _upgrade_active_todos_from_latest_run() -> None:
    """Use the latest clean terminal snapshot to seed active plan revisions."""
    with connect() as db:
        session_ids = [str(row["id"]) for row in db.execute("SELECT id FROM project_sessions")]
    for session_id in session_ids:
        with connect() as db:
            rows = db.execute(
                """
                SELECT id, output_json FROM workflow_runs
                 WHERE project_session_id=?
                   AND status IN ('completed', 'completed_with_unresolved_evidence')
                 ORDER BY id DESC
                """,
                (session_id,),
            ).fetchall()
        snapshot = None
        snapshot_run_id = None
        for row in rows:
            try:
                output = json.loads(row["output_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(output.get("todo_list"), list) and output["todo_list"]:
                snapshot = output
                snapshot_run_id = int(row["id"])
                break
        if snapshot is None:
            continue
        later_reopens: set[str] = set()
        with connect() as db:
            later_outputs = db.execute(
                """SELECT output_json FROM workflow_runs
                    WHERE project_session_id=? AND id>?""",
                (session_id, snapshot_run_id),
            ).fetchall()
            later_checkpoints = db.execute(
                """SELECT cp.state_json
                     FROM flow_checkpoints cp
                     JOIN workflow_runs wr ON wr.id=cp.run_id
                    WHERE wr.project_session_id=? AND wr.id>?""",
                (session_id, snapshot_run_id),
            ).fetchall()
        for raw_json in [
            *(row["output_json"] for row in later_outputs),
            *(row["state_json"] for row in later_checkpoints),
        ]:
            try:
                later_state = json.loads(raw_json or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            for reopen in later_state.get("reopen_todos") or []:
                if (
                    isinstance(reopen, dict)
                    and reopen.get("todo_id")
                    and str(reopen.get("reason") or "").strip()
                    and reopen.get("evidence")
                ):
                    later_reopens.add(str(reopen["todo_id"]))
        restored = db_todo.restore_completed_from_snapshot(
            session_id,
            snapshot["todo_list"],
            later_reopens,
        )
        if restored:
            logger.warning(
                "restored %s completed todo status(es) for session %s from run %s",
                restored,
                session_id,
                snapshot_run_id,
            )
        incoming = {
            str(todo.get("id"))
            for todo in snapshot["todo_list"]
            if isinstance(todo, dict) and todo.get("id")
        }
        current = {
            str(todo["id"])
            for todo in db_todo.list_active_todos(session_id)
        }
        if incoming == current:
            continue
        archive_updates = [
            {
                "todo_id": todo_id,
                "reason": "other",
                "note": "Archived while upgrading historical todos to the latest completed plan snapshot.",
            }
            for todo_id in current - incoming
        ]
        db_todo.sync_manager_todos(
            session_id,
            snapshot["todo_list"],
            archive_updates,
        )


def _reconcile_round_locks_on_startup() -> None:
    from task_hounds_api.db.ops import rounds as db_rounds

    with connect() as db:
        sessions = [
            str(row["id"])
            for row in db.execute("SELECT id FROM project_sessions")
        ]
    for session_id in sessions:
        current = db_rounds.current_round(session_id)
        if not current or current.get("status") != "active":
            continue
        with connect() as db:
            run = db.execute(
                """SELECT id, output_json FROM workflow_runs
                    WHERE project_session_id=?
                      AND status IN ('completed', 'completed_with_unresolved_evidence')
                    ORDER BY id DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
        if not run:
            continue
        try:
            output = json.loads(run["output_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            output = {}
        db_rounds.try_lock_round(
            session_id,
            int(run["id"]),
            str(output.get("manager_message") or "Imported completed directive."),
        )


# ── Migration audit compat helpers (id 137, 138) ────────────────────────────
# The 0c44ba2 codebase had top-level ensure_backend_ready() and
# ensure_runtime_ready(restart_managed=False) callables. The new
# architecture moves that work into create_app + the FastAPI lifespan.
# These shims preserve the old call style so legacy tests/scripts that
# import and call them keep working. They are thin: do not add heavy
# behavior here. New code should call create_app() and rely on the
# lifespan instead.


def ensure_backend_ready() -> dict:
    """Migration audit symbol 137 compat: init DB + default project + default agents.

    The 0c44ba2 version also mkdir-ed RUNTIME_DIR + RUNTIME_FILES and
    scanned opencode.jsonc models. Those are GONE in the new architecture
    (DB-only state). This shim does what the new code can do safely:
    init_db + ensure_default_project + ensure_default_agents.

    Returns a small status dict so callers (and tests) can confirm
    what the shim actually did, mirroring the spirit of the old
    function's side-effect logging.
    """
    init_db()
    _normalize_stale_workflow_statuses_on_startup()
    _upgrade_active_todos_from_latest_run()
    _reconcile_round_locks_on_startup()
    _ensure_default_project()
    _ensure_default_agents()
    return {
        "ok": True,
        "db_initialized": True,
        "default_project_ensured": True,
        "default_agents_ensured": True,
    }


def ensure_runtime_ready(*, restart_managed: bool = False) -> dict:
    """Migration audit symbol 138 compat: reconcile RuntimeManager state.

    The 0c44ba2 version called OpenCodeLifecycleManager.reconcile_runtime
    and optionally restarted managed servers. The new architecture uses
    RuntimeManager.reconcile_servers / ensure_managed_running /
    validate_credentials. The restart_managed knob is honored: when
    True, the shim asks the manager to stop+start the managed instance;
    when False, it only reconciles (no forced restart).
    """
    rm = RuntimeManager.instance()
    rm.reconcile_servers()
    supervised = os.environ.get("TASK_HOUNDS_SUPERVISED") == "1"
    if supervised:
        managed_ok = bool(
            rm.test_server(rm._managed_host, rm._managed_port).get("reachable")
        )
        if restart_managed:
            from task_hounds_api.supervisor import request_restart

            result = request_restart()
            managed_ok = bool(result.get("ok"))
            if managed_ok:
                rm._managed_port = int(result.get("port") or rm._managed_port)
    else:
        managed_ok = bool(rm.ensure_managed_running())
        if restart_managed:
            try:
                rm.stop_managed()
            except Exception:
                logger.warning("ensure_runtime_ready: stop_managed failed during restart")
            managed_ok = bool(rm.ensure_managed_running())
    cred_warnings = rm.validate_credentials()
    return {
        "ok": True,
        "managed_running": managed_ok,
        "restarted": restart_managed,
        "credential_warnings": list(cred_warnings or []),
    }


# ── Migration audit compat callables (id 140, 141) ───────────────────────
# The 0c44ba2 codebase had top-level startup() and shutdown() async
# callables that legacy scripts imported and called. The new
# architecture moves that work into create_app + the FastAPI lifespan.
# These shims preserve the old call style so legacy scripts that
# import and call them keep working.


def startup() -> dict:
    """Migration audit symbol 140 compat: idempotent boot hook.

    The 0c44ba2 startup was a FastAPI @app.on_event("startup") handler
    that called ensure_backend_ready() then
    ensure_runtime_ready(restart_managed=True). The new architecture
    uses the FastAPI lifespan context manager in create_app; this
    shim is the legacy entry point for scripts that didn't go through
    FastAPI (e.g. CLI tools, test fixtures).
    """
    backend = ensure_backend_ready()
    runtime = ensure_runtime_ready(restart_managed=True)
    return {
        "ok": True,
        "backend": backend,
        "runtime": runtime,
    }


async def shutdown() -> dict:
    """Migration audit symbol 141 compat: idempotent shutdown hook.

    The 0c44ba2 shutdown was a FastAPI @app.on_event("shutdown") handler
    that called OpenCodeLifecycleManager.stop_all_managed(reason='backend_exit').
    The new architecture uses the FastAPI lifespan finalizer; this
    shim is the legacy entry point. async to match the old
    @app.on_event("shutdown") signature.
    """
    rm = RuntimeManager.instance()
    return rm.stop_all()


def create_app() -> FastAPI:
    """Build and return the FastAPI app. DB is initialized on first call."""
    # ID 136: Bootstrap env from repo .env files (matches old _load_env_files behavior).
    # load_dotenv with override=False preserves explicitly-set process env vars
    # (e.g. from shell exports, CI, container orchestration).
    try:
        from dotenv import load_dotenv
        from task_hounds_api.db import ROOT as _pt_root
        _env_root = _pt_root / ".env"
        _env_config = _pt_root / "config" / ".env"
        _env_runtime = Path(os.environ.get("POWER_TEAMS_RUNTIME_DIR", "")) / ".env"
        load_dotenv(_env_root, override=False, encoding="utf-8-sig")
        load_dotenv(_env_config, override=False, encoding="utf-8-sig")
        if os.environ.get("POWER_TEAMS_RUNTIME_DIR"):
            load_dotenv(_env_runtime, override=False, encoding="utf-8-sig")
    except ImportError:
        # python-dotenv not installed — rely entirely on shell/CI env vars.
        pass

    init_db()
    _normalize_stale_workflow_statuses_on_startup()
    _upgrade_active_todos_from_latest_run()
    _reconcile_round_locks_on_startup()
    _ensure_default_project()
    _ensure_default_agents()
    _clear_stale_agent_states_on_startup()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        supervised = os.environ.get("TASK_HOUNDS_SUPERVISED") == "1"
        try:
            rm = RuntimeManager.instance()
            rm.reconcile_servers()
            managed_ok = (
                bool(rm.test_server(rm._managed_host, rm._managed_port).get("reachable"))
                if supervised
                else bool(rm.ensure_managed_running())
            )

            # Gather provider_ids we care about warnings for:
            # - Already-bound providers (from existing role bindings)
            # - The managed server's provider (if managed is running)
            # This prevents missing credentials for unused providers (e.g. Bailian)
            # from blocking auto_bind when MiniMax is functional.
            active_provider_ids: set[str] | None = rm._active_provider_ids()
            if managed_ok:
                managed_model = os.environ.get(
                    "TASK_HOUNDS_OPENCODE_MODEL", "minimax-coding-plan/MiniMax-M2.7"
                )
                managed_provider = managed_model.split("/")[0] if "/" in managed_model else managed_model
                if active_provider_ids is None:
                    active_provider_ids = {managed_provider}
                else:
                    active_provider_ids.add(managed_provider)

            cred_warnings = rm.validate_credentials(active_provider_ids)
            # Phase-10 (P0-2): discover must run before repair so
            # repair has reachable candidates to rewrite bindings to.
            # extra_ports = ports from current bindings (even if dead)
            # so a previously-seen but currently-down port gets a
            # chance to be re-registered when it comes back.
            extra_ports: list[int] = []
            try:
                for b in db_rt.list_bindings():
                    p = b.get("port")
                    if isinstance(p, int) and p not in extra_ports:
                        extra_ports.append(p)
            except Exception:
                pass
            try:
                rm.discover_candidate_ports(
                    start_port=rm._managed_port,
                    end_port=rm._managed_port,
                    extra_ports=extra_ports,
                )
            except Exception as exc:
                logger.warning("startup discover_candidate_ports failed: %s", exc)
            # Phase-9 (P0-2): repair stale bindings before auto-bind.
            # auto_bind_four_roles() picks the first reachable server,
            # but if existing bindings already point at a dead port
            # AND a reachable preferred server exists, rewrite
            # them first so the four role bindings reflect reality.
            try:
                rm.repair_role_bindings()
            except Exception as exc:
                logger.warning("startup repair_role_bindings failed: %s", exc)
            if managed_ok and not cred_warnings:
                rm.auto_bind_four_roles()
            else:
                if not managed_ok:
                    logger.warning(
                        "managed opencode failed during normal startup; "
                        "the UI will offer a user-confirmed restart"
                    )
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
                if supervised:
                    from task_hounds_api.opencode import registry as oc_registry

                    oc_registry.kill_all_runs()
                else:
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

    @app.middleware("http")
    async def prevent_stale_ui_shell(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    app.include_router(projects.router)
    app.include_router(projects.sessions_router)
    app.include_router(projects.project_sessions_router)
    app.include_router(agents.router)
    app.include_router(todos.router)
    app.include_router(workflow.router)
    app.include_router(workflow.manager_messages_root)
    app.include_router(workflow.flow01_router)
    app.include_router(chat.router)
    app.include_router(manager_chat.router)
    app.include_router(runtime.router)
    app.include_router(streams.router)
    app.include_router(settings.router)
    app.include_router(questions.router)
    # Compat shim - keep old UI endpoints working until UI is rebuilt
    app.include_router(compat.router)

    @app.get("/api/ping")
    def ping() -> dict:
        return {"ok": True}

    @app.get("/api/health", response_model=schemas.HealthResponse)
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

    @app.post("/api/health", response_model=schemas.HealthResponse)
    def health_post() -> dict:
        return health()

    @app.get("/api/health/legacy")
    def health_legacy() -> dict:
        """P7 id 187: legacy 15-field health envelope.

        The old /api/health returned 15 fields including
        backend_version, db_path, active_workspace_id,
        shared_opencode_host/port, opencode_enabled,
        managed/external_opencode_count, active_work,
        last_checkpoint, role_bindings, runtime_policy.
        The new /api/health returns only 3 top-level fields
        + an opencode sub-dict (the rest moved into the
        sub-dict). This legacy wrapper restores the 15-field
        envelope so the old UI's direct top-level field reads
        continue to work.
        """
        from datetime import datetime, timezone
        from task_hounds_api.opencode import lifecycle as oc_lifecycle
        from task_hounds_api.db.ops import project as db_project
        from task_hounds_api.db.ops import workflow as db_wf
        from task_hounds_api.opencode.runtime_manager import RuntimeManager
        lc = oc_lifecycle.OpenCodeLifecycle()
        lc_health = lc.health()
        active = db_project.get_active_session() or {}
        rm = RuntimeManager.instance()
        # Managed vs external server count.
        try:
            servers = rm.list_servers()
        except Exception:
            servers = []
        managed = sum(1 for s in servers if s.get("managed"))
        external = sum(1 for s in servers if not s.get("managed"))
        # Role bindings + runtime policy.
        try:
            bindings = rm.list_bindings()
        except Exception:
            bindings = []
        try:
            policy = rm.get_policy()
        except Exception:
            policy = {}
        # Best-effort backend_version via `git describe`.
        backend_version = ""
        try:
            import subprocess
            r = subprocess.run(
                ["git", "describe", "--tags", "--always"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            backend_version = (r.stdout or "").strip()
        except Exception:
            pass
        return {
            "ok": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "backend_version": backend_version,
            "db_path": "",
            "active_workspace_id": active.get("id"),
            "active_project_session": active.get("id"),
            "shared_opencode_host": lc_health.get("host", "127.0.0.1"),
            "shared_opencode_port": lc_health.get("port", 18765),
            "opencode_enabled": bool(lc_health.get("ok")),
            "managed_opencode_count": managed,
            "external_opencode_count": external,
            "active_work": False,
            "last_checkpoint": None,
            "role_bindings": bindings,
            "runtime_policy": policy,
        }

    @app.post("/api/debug-logs")
    async def debug_logs(request: Request) -> dict:
        """Persist frontend debug events to one file per UI session."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        return write_debug_batch(body if isinstance(body, dict) else {})

    @app.post("/api/debug_log")
    async def debug_log_single(request: Request) -> dict:
        """P7 id 346: legacy single-entry debug_log endpoint.

        The old endpoint accepted ONE DebugLogEntry {msg, source}
        and wrote a text debug line. The new /api/debug-logs
        accepts a batch payload. This legacy alias normalizes
        a single-entry body into the batch shape so the old
        client call (msg+source) still works.
        """
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        # Normalize: a single {msg, source} becomes a 1-entry batch.
        if "entries" not in body and "msg" in body:
            body = {
                "entries": [
                    {
                        "msg": body.get("msg", ""),
                        "source": body.get("source", "unknown"),
                    }
                ]
            }
        return write_debug_batch(body)

    dist_dir = ROOT / "ui" / "web" / "dist"
    if dist_dir.exists():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="ui")

    return app


app = create_app()
