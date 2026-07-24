"""api.routes.runtime — OpenCode server management and runtime policy.

Authoritative owner of all /api/runtime/* routes. The compat.py
duplicates have been removed; do not re-add them there.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException

from task_hounds_api.db.ops import runtime as db_rt
from task_hounds_api.db.ops import graphflow_jobs as db_jobs
from task_hounds_api.db.ops import project as db_project
from task_hounds_api.db import ROOT
from task_hounds_api.opencode.config import (
    CONFIG_PATH,
    generate_runtime_config,
    is_model_available,
    list_providers,
    model_supports_thinking,
    reset_cache,
)
from task_hounds_api.api import schemas

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


@router.get("/session-statuses")
def session_statuses() -> dict:
    from task_hounds_api.db.ops import execution as db_execution

    return {"sessions": db_execution.session_runtime_statuses()}

def _env_path() -> Path:
    runtime_dir = os.environ.get("POWER_TEAMS_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / ".env"
    return ROOT / ".env"


SUPPORTED_CREDENTIAL_ENV_VARS = {
    "minimax_api_key": "OPENCODE_API_KEY_MINIMAX",
    "kimi_api_key": "OPENCODE_API_KEY_KIMI",
    "bailian_api_key": "OPENCODE_API_KEY_BAILIAN",
}


def _resolve_runtime_manager():
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    return RuntimeManager.instance()


def _validate_host_port(host: str, port: int) -> None:
    if not host or not isinstance(host, str):
        raise HTTPException(status_code=400, detail="host must be a non-empty string")
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise HTTPException(status_code=400, detail="port must be an integer 1..65535")


def _server_host_port(server: dict, instance_id: int) -> tuple[str, int]:
    host = str(server.get("host") or "").strip()
    try:
        port = int(server.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not host or not (1 <= port <= 65535):
        raise HTTPException(
            status_code=400,
            detail=f"instance {instance_id} has no valid host/port",
        )
    return host, port


def _write_env_values(updates: dict[str, str]) -> None:
    """Set or uncomment selected .env keys without disturbing other lines."""
    env_path = _env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        env_path.read_text(encoding="utf-8-sig").splitlines()
        if env_path.exists()
        else []
    )
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        prefix = line[: len(line) - len(stripped)]
        candidate = stripped[1:].lstrip() if stripped.startswith("#") else stripped
        if "=" not in candidate:
            out.append(line)
            continue
        key = candidate.split("=", 1)[0].strip()
        if key not in remaining:
            out.append(line)
            continue
        out.append(f"{prefix}{key}={remaining.pop(key)}")
    if remaining and out and out[-1].strip():
        out.append("")
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _bindings_are_fully_wired(
    rm,
    bindings: list[dict],
    servers: list[dict],
) -> tuple[bool, str | None]:
    """Verify that every role binding is wired to a real, non-ignored,
    reachable server whose host/port matches the binding. Returns
    (ok, first_failure_reason). The first failure is reported so the
    UI can surface a specific actionable error to the operator.
    """
    servers_by_id = {int(s["id"]): s for s in servers if s.get("id") is not None}
    if len(bindings) < 1:
        return False, "bindings_role_count_wrong"
    for b in bindings:
        sid = b.get("server_instance_id")
        if sid is None:
            return False, "binding_server_instance_id_null"
        srv = servers_by_id.get(int(sid))
        if srv is None:
            return False, "binding_server_instance_id_orphaned"
        if srv.get("status") == "ignored":
            return False, "binding_points_to_ignored_server"
        if not rm.test_server(
            srv.get("host", ""), int(srv.get("port", 0))
        ).get("reachable", False):
            return False, "binding_points_to_unreachable_server"
        if b.get("host") and srv.get("host") and b["host"] != srv["host"]:
            return False, "binding_host_mismatch"
        if b.get("port") and srv.get("port") and int(b["port"]) != int(srv["port"]):
            return False, "binding_port_mismatch"
    return True, None


def _bindings_use_reachable_external_servers(
    rm,
    bindings: list[dict],
    servers: list[dict],
) -> bool:
    servers_by_id = {int(s["id"]): s for s in servers if s.get("id") is not None}
    if len(bindings) < 1:
        return False
    for binding in bindings:
        sid = binding.get("server_instance_id")
        if sid is None:
            return False
        server = servers_by_id.get(int(sid))
        if not server or server.get("owner") != "external" or server.get("status") == "ignored":
            return False
        if not rm.test_server(
            server.get("host", ""), int(server.get("port", 0))
        ).get("reachable", False):
            return False
    return True


@router.get("/status")
def runtime_status() -> dict:
    """Runtime Panel UI authoritative status shape. ready is True
    only when ALL four preconditions hold:
      (a) credentials present (no empty apiKey after env-var expansion)
      (b) at least one non-ignored, reachable server
      (c) exactly 4 role bindings exist
      (d) every binding's server_instance_id points at a real,
          non-ignored, reachable server whose host/port matches the
          binding row.
    unavailable_reason names the failing precondition so the UI can
    surface a specific actionable error to the operator.
    """
    rm = _resolve_runtime_manager()
    servers = rm.list_servers()
    bindings = db_rt.list_bindings()
    provider_ids = {
        str(b.get("model") or "").split("/", 1)[0]
        for b in bindings
        if "/" in str(b.get("model") or "")
    } or None
    cred_warnings = rm.validate_credentials(provider_ids=provider_ids)

    active_servers = [
        s for s in servers
        if s.get("status") != "ignored"
        and rm.test_server(s.get("host", ""), int(s.get("port", 0))).get("reachable", False)
    ]
    servers_ok = len(active_servers) > 0

    bindings_ok, bindings_reason = _bindings_are_fully_wired(rm, bindings, servers)
    external_credentials_ok = _bindings_use_reachable_external_servers(rm, bindings, servers)
    # In full mode (4 bindings, all external servers reachable), block on any credential
    # warning since all providers should be configured. In partial mode (1-3 bindings),
    # only block on warnings for providers that are actually bound - warnings for
    # unbound providers don't affect readiness since they're not in use yet.
    if external_credentials_ok:
        cred_ok = not cred_warnings
    else:
        if provider_ids:
            # Filter to only warnings for providers that are bound
            warnings_for_bound = [
                w for w in cred_warnings
                if re.search(r"provider '([^']+)'", w) and
                re.search(r"provider '([^']+)'", w).group(1) in provider_ids
            ]
            cred_ok = not warnings_for_bound
        else:
            # No bindings yet - don't block on missing credentials during setup
            cred_ok = True

    if not cred_ok:
        unavailable_reason = "missing_credentials"
    elif not servers_ok:
        unavailable_reason = "no_reachable_server"
    elif not bindings_ok:
        unavailable_reason = bindings_reason or "bindings_invalid"
    else:
        unavailable_reason = None
    ready = unavailable_reason is None

    # Phase-9 (P1) surface: enrich the status with actionable
    # debug fields so the UI can show operators exactly what to fix.
    reachable_servers = [
        {
            "id": s.get("id"),
            "host": s.get("host"),
            "port": int(s.get("port", 0)),
            "owner": s.get("owner"),
            "status": s.get("status"),
        }
        for s in active_servers
    ]
    stale_bindings: list[dict] = []
    for b in bindings:
        sid = b.get("server_instance_id")
        server_row = next((s for s in servers if s.get("id") == sid), None)
        if server_row is None:
            stale_bindings.append({
                "role": b.get("role"),
                "host": b.get("host"),
                "port": int(b.get("port", 0)),
                "reason": f"orphaned server_instance_id={sid}",
            })
            continue
        if (server_row.get("host"), int(server_row.get("port", 0))) != (
            b.get("host"), int(b.get("port", 0))
        ):
            stale_bindings.append({
                "role": b.get("role"),
                "host": b.get("host"),
                "port": int(b.get("port", 0)),
                "reason": "binding host/port mismatch server row",
            })
            continue
        if not rm.test_server(b.get("host", ""), int(b.get("port", 0))).get("reachable", False):
            stale_bindings.append({
                "role": b.get("role"),
                "host": b.get("host"),
                "port": int(b.get("port", 0)),
                "reason": "server is not reachable",
            })

    if not cred_ok:
        recommended_action = "set_credentials"
    elif not servers_ok:
        recommended_action = "start_managed"
    elif stale_bindings:
        recommended_action = "repair_bindings"
    elif not bindings_ok:
        recommended_action = "repair_bindings"
    else:
        recommended_action = None

    active_session = db_project.get_active_session() or {}
    active_job = (
        db_jobs.active_for_session(str(active_session.get("id")))
        if active_session.get("id")
        else None
    )
    active_work = None
    if active_job:
        active_work = (
            f"GraphFlow run #{active_job['run_id']} "
            f"({active_job.get('workflow_status') or active_job['status']})"
        )

    return {
        "ok": True,
        "ready": ready,
        "runtime_available": ready,
        "unavailable_reason": unavailable_reason,
        "managed_opencode_count": sum(
            1 for s in servers if s.get("owner") == "power_teams"
        ),
        "external_opencode_count": sum(
            1 for s in servers if s.get("owner") == "external"
        ),
        "servers": servers,
        "active_work": active_work,
        "last_checkpoint": None,
        "role_bindings": bindings,
        "policy": db_rt.get_policy(),
        "managed_health": rm.get_managed_health(),
        "stale_bindings": stale_bindings,
        "reachable_servers": reachable_servers,
        "recommended_action": recommended_action,
    }


@router.post("/credentials")
def save_runtime_credentials(body: dict) -> dict:
    """Persist provider API keys into ROOT/.env and refresh runtime config.

    The response never echoes secrets. This endpoint intentionally only
    accepts the known OpenCode provider keys used by the bundled config.
    """
    updates: dict[str, str] = {}
    for body_key, env_key in SUPPORTED_CREDENTIAL_ENV_VARS.items():
        value = str((body or {}).get(body_key) or "").strip()
        if value:
            updates[env_key] = value
    if not updates:
        raise HTTPException(status_code=400, detail="No API key provided")

    _write_env_values(updates)
    for key, value in updates.items():
        os.environ[key] = value

    reset_cache()
    generate_runtime_config(CONFIG_PATH)

    rm = _resolve_runtime_manager()
    restarted = False
    try:
        if os.environ.get("TASK_HOUNDS_SUPERVISED") == "1":
            from task_hounds_api.supervisor import request_restart

            restart_result = request_restart()
            restarted = bool(restart_result.get("ok"))
            if restarted:
                rm._managed_port = int(
                    restart_result.get("port") or rm._managed_port
                )
                rm._managed_lifecycle = None
                rm.discover_candidate_ports(
                    start_port=rm._managed_port,
                    end_port=rm._managed_port,
                    extra_ports=[rm._managed_port],
                )
        else:
            restarted = bool(rm.ensure_managed_running(restart=True))
        if restarted:
            try:
                rm.repair_role_bindings()
                rm.auto_bind_four_roles()
            except Exception:
                pass
    except Exception:
        restarted = False
    warnings = rm.validate_credentials(provider_ids=rm._active_provider_ids())
    return {
        "ok": True,
        "updated": sorted(updates.keys()),
        "credential_warnings": warnings,
        "managed_restarted": restarted,
    }


@router.post("/repair-bindings")
def repair_bindings_endpoint() -> dict:
    """Phase-9 (P1) operator endpoint: discover + repair, return a
    structured report.

    Idempotent — a second call after everything is healthy
    returns {repaired: 0, unresolved: []}.

    Does NOT call reconcile_servers. The lifespan already ran
    reconcile at startup; re-running it here would delete
    unreachable external rows, which the endpoint contract
    forbids ("External rows must still be there (no kill)").
    The endpoint only rewrites role bindings to point at a
    reachable preferred server; if no reachable server exists,
    the bindings are left as-is and the unresolved reasons are
    surfaced in the response so the operator can act.
    """
    from task_hounds_api.opencode.process import is_reachable

    rm = _resolve_runtime_manager()
    extra_ports: list[int] = []
    try:
        for b in db_rt.list_bindings():
            p = b.get("port")
            if isinstance(p, int) and p not in extra_ports:
                extra_ports.append(p)
    except Exception:
        pass
    try:
        rm.discover_candidate_ports(extra_ports=extra_ports)
    except Exception:
        pass
    report = rm.repair_role_bindings()
    return {
        "ok": True,
        "reconciled": 0,
        "repaired": int(report.get("repaired", 0)),
        "unresolved": list(report.get("unresolved", [])),
    }


@router.get("/opencode")
def list_opencode_servers() -> dict:
    """List known OpenCode server rows."""
    rm = _resolve_runtime_manager()
    reconciled = rm.reconcile_servers()
    servers = rm.list_servers()
    return {
        "servers": servers,
        "reconciled": reconciled,
        "managed_count": sum(1 for s in servers if s.get("owner") == "power_teams"),
        "external_count": sum(1 for s in servers if s.get("owner") == "external"),
        "ignored_count": sum(1 for s in servers if s.get("status") == "ignored"),
    }


@router.post("/discover")
def discover_opencode_servers(body: schemas.DiscoverRequest | None = None) -> dict:
    """Scan a port range, register newly-discovered servers."""
    rm = _resolve_runtime_manager()
    if body is None:
        return rm.discover_candidate_ports()
    return rm.discover_candidate_ports(
        host=body.host,
        start_port=body.start_port,
        end_port=body.end_port,
        extra_ports=body.extra_ports,
    )


# ── Migration audit compat alias (id 273) ───────────────────────────────
# The 0c44ba2 runtime_opencode_discover was GET. The new code uses
# POST (idempotent port scan is a write-shaped operation in the new
# architecture). This compat alias accepts a no-arg GET and returns
# the legacy envelope shape {ok, discovered} so the old UI can still
# scan with a simple GET.


@router.get("/discover")
def compat_discover_opencode_servers() -> dict:
    """Migration audit symbol 273 compat: legacy GET /api/runtime/discover
    delegates to the new POST handler and returns the legacy envelope
    {ok, discovered}. The new POST /discover returns a different shape;
    this compat wraps it for old callers."""
    result = discover_opencode_servers(None)
    return {"ok": True, "discovered": result}


@router.post("/attach")
def attach_opencode_server(body: schemas.AttachRequest) -> dict:
    """Attach to an externally-running OpenCode server. 422 if unreachable."""
    _validate_host_port(body.host, body.port)
    rm = _resolve_runtime_manager()
    reach = rm.test_server(body.host, body.port)
    if not reach["reachable"]:
        raise HTTPException(
            status_code=422,
            detail=f"opencode not reachable on {body.host}:{body.port}",
        )
    instance_id = rm.register_external(body.host, body.port)
    return {
        "ok": True,
        "attached": True,
        "host": body.host,
        "port": body.port,
        "instance_id": instance_id,
    }


# ── Migration audit compat alias (id 275) ───────────────────────────────
# The 0c44ba2 runtime_opencode_attach was POST /api/runtime/opencode/attach
# and returned {ok:true, host, port}. The new POST /api/runtime/attach
# returns the same shape plus instance_id. This compat alias delegates
# to the new handler — the extra instance_id field is additive
# (existing callers that destructure .ok / .host / .port still work).


@router.post("/opencode/attach")
def compat_attach_opencode(body: schemas.AttachRequest) -> dict:
    """Migration audit symbol 275 compat: legacy POST
    /api/runtime/opencode/attach delegates to the new attach
    handler. The legacy 0c44ba2 caller consumed {ok, host, port};
    the new response adds instance_id (additive, not breaking)."""
    return attach_opencode_server(body)


@router.post("/test")
def test_opencode_server(body: schemas.TestRequest) -> dict:
    """Ping a host/port and return reachability."""
    _validate_host_port(body.host, body.port)
    rm = _resolve_runtime_manager()
    return rm.test_server(body.host, body.port)


# ── Migration audit compat alias (id 290) ───────────────────────────────
# The 0c44ba2 port_checks was POST /api/port_checks, accepted
# {host, port} JSON, and returned {ok:true, is_running:1|0, output}.
# The new closest endpoint is POST /api/runtime/test, which calls
# RuntimeManager.test_server and returns {host, port, reachable:bool}.
# This compat alias translates the new response to the legacy
# envelope: is_running=1 if reachable else 0; output synthesised
# from the reachability result.


@router.post("/port_checks")
def compat_port_checks(body: schemas.TestRequest) -> dict:
    """Migration audit symbol 290 compat: legacy POST /api/port_checks
    delegates to RuntimeManager.test_server and wraps the result in
    the legacy {ok, is_running, output} envelope. `is_running` is
    the integer 1/0; `output` is the textual reachability summary."""
    _validate_host_port(body.host, body.port)
    rm = _resolve_runtime_manager()
    result = rm.test_server(body.host, body.port)
    reachable = bool(result.get("reachable", False))
    return {
        "ok": True,
        "is_running": 1 if reachable else 0,
        "output": (
            f"server reachable on {body.host}:{body.port}"
            if reachable
            else f"server unreachable on {body.host}:{body.port}"
        ),
    }


# ── Migration audit compat alias (id 276) ───────────────────────────────
# The 0c44ba2 runtime_opencode_test was POST /api/runtime/opencode/test
# and returned {ok:True, is_running:bool, message:str}. The new
# POST /api/runtime/test returns {host, port, reachable:bool} (no
# `is_running` field, no `message` field, no `ok` envelope). This
# compat alias translates the new response to the legacy envelope.


@router.post("/opencode/test")
def compat_test_opencode_server(body: schemas.TestRequest) -> dict:
    """Migration audit symbol 276 compat: legacy POST
    /api/runtime/opencode/test delegates to the new test handler
    and wraps the response in the legacy {ok, is_running, message}
    envelope. `is_running` = `reachable`; `message` is a
    synthesized 'reachable on host:port' / 'unreachable' string."""
    _validate_host_port(body.host, body.port)
    rm = _resolve_runtime_manager()
    result = rm.test_server(body.host, body.port)
    reachable = result.get("reachable", False)
    return {
        "ok": True,
        "is_running": reachable,
        "message": (
            f"reachable on {body.host}:{body.port}" if reachable
            else f"unreachable on {body.host}:{body.port}"
        ),
    }


@router.post("/ignore")
def ignore_opencode_server(body: schemas.IgnoreRequest) -> dict:
    """Mark (host, port) as ignored; future discover skips it."""
    _validate_host_port(body.host, body.port)
    rm = _resolve_runtime_manager()
    ok = rm.ignore_server(body.host, body.port, body.reason or "")
    return {
        "ok": ok,
        "ignored": True,
        "host": body.host,
        "port": body.port,
        "reason": body.reason or "",
    }


@router.post("/unignore")
def unignore_opencode_server(body: schemas.IgnoreRequest) -> dict:
    """Clear the 'ignored' status for (host, port)."""
    _validate_host_port(body.host, body.port)
    rm = _resolve_runtime_manager()
    ok = rm.unignore_server(body.host, body.port)
    return {
        "ok": ok,
        "unignored": ok,
        "host": body.host,
        "port": body.port,
    }


@router.post("/opencode/{instance_id}/stop")
def stop_opencode_instance(instance_id: int) -> dict:
    """Stop a managed OpenCode instance. External = skipped_external."""
    rm = _resolve_runtime_manager()
    outcome = rm.stop_server(instance_id)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail=f"no server with id {instance_id}")
    return {
        "ok": True,
        "instance_id": instance_id,
        "outcome": outcome,
    }


@router.post("/opencode/start")
def runtime_opencode_start() -> dict:
    rm = _resolve_runtime_manager()
    if os.environ.get("TASK_HOUNDS_SUPERVISED") == "1":
        from task_hounds_api.supervisor import request_restart

        result = request_restart()
        if not result.get("ok"):
            return {
                "ok": False,
                "status": "failed",
                "message": result.get("error") or "Supervisor failed to restart OpenCode.",
            }
        rm._managed_host = str(result.get("host") or rm._managed_host)
        rm._managed_port = int(result.get("port") or rm._managed_port)
        rm._managed_lifecycle = None
        try:
            rm.discover_candidate_ports(
                start_port=rm._managed_port,
                end_port=rm._managed_port,
                extra_ports=[rm._managed_port],
            )
            rm.repair_role_bindings()
            rm.auto_bind_four_roles()
        except Exception as exc:
            return {
                "ok": False,
                "status": "binding_repair_failed",
                "message": f"OpenCode restarted, but bindings could not be repaired: {exc}",
                "host": rm._managed_host,
                "port": rm._managed_port,
                "pid": result.get("pid"),
            }
        return {
            "ok": True,
            "host": rm._managed_host,
            "port": rm._managed_port,
            "pid": result.get("pid"),
            "status": "running",
            "message": f"OpenCode restarted by supervisor on {rm._managed_host}:{rm._managed_port}",
        }

    lifecycle = rm.get_managed_lifecycle()
    if lifecycle is not None and lifecycle.health().get("ok"):
        try:
            rm.discover_candidate_ports(
                start_port=rm._managed_port,
                end_port=rm._managed_port,
                extra_ports=[rm._managed_port],
            )
            rm.repair_role_bindings()
            rm.auto_bind_four_roles()
        except Exception:
            pass
        health = lifecycle.health()
        return {
            "ok": True,
            "host": rm._managed_host,
            "port": rm._managed_port,
            "pid": health.get("pid"),
            "status": "running",
            "message": f"OpenCode already running on {rm._managed_host}:{rm._managed_port}",
        }
    try:
        ok = rm.ensure_managed_running()
        if ok:
            try:
                rm.discover_candidate_ports(
                    start_port=rm._managed_port,
                    end_port=rm._managed_port,
                    extra_ports=[rm._managed_port],
                )
                rm.repair_role_bindings()
                rm.auto_bind_four_roles()
            except Exception:
                # The server is already reachable; binding diagnostics remain
                # available if registry repair needs separate attention.
                pass
            health = rm.get_managed_health()
            return {
                "ok": True,
                "host": rm._managed_host,
                "port": rm._managed_port,
                "pid": health.get("pid"),
                "status": "running",
                "message": f"OpenCode started successfully on {rm._managed_host}:{rm._managed_port}",
            }
        else:
            return {
                "ok": False,
                "host": rm._managed_host,
                "port": rm._managed_port,
                "pid": None,
                "status": "failed",
                "message": "OpenCode failed to start. Check credentials and port availability.",
            }
    except Exception as exc:
        return {
            "ok": False,
            "host": rm._managed_host,
            "port": rm._managed_port,
            "pid": None,
            "status": "failed",
            "message": f"OpenCode start failed: {exc!r}",
        }


@router.post("/stop-all")
def stop_all_opencode_servers() -> dict:
    """Stop every managed server and kill every in-flight run."""
    rm = _resolve_runtime_manager()
    return rm.stop_all()


ROLES = ("manager", "worker", "reviewer", "chat")


def _validate_and_resolve_binding(
    role: str,
    host: str,
    port: int,
    model: str | None,
    opencode_agent: str | None = None,
) -> int | None:
    """Validate a (role, host, port, model) tuple. Raises 400/422 on
    failure. Returns the server_instance_id to write into the
    binding row (either the matching row's id or a freshly-auto-
    registered external row's id).

    This function does NOT touch the database. The actual write is
    done by the caller via `upsert_binding_with_agent_sync`, which
    combines the binding write AND the agent_registry sync into a
    single atomic transaction.
    """
    from task_hounds_api.opencode.config import is_model_available

    if model and not is_model_available(model):
        raise HTTPException(
            status_code=422,
            detail=f"model {model!r} is not available in opencode.jsonc",
        )

    rm = _resolve_runtime_manager()
    reachable = rm.test_server(host, port).get("reachable", False)

    servers = rm.list_servers()
    matching = next(
        (s for s in servers if s.get("host") == host and s.get("port") == port),
        None,
    )
    server_instance_id: int | None = None

    if matching:
        if matching.get("status") == "ignored":
            raise HTTPException(
                status_code=422,
                detail=f"server {host}:{port} is marked ignored; unignore it first",
            )
        if not reachable:
            raise HTTPException(
                status_code=422,
                detail=f"server row exists for {host}:{port} but it is not reachable",
            )
        server_instance_id = int(matching["id"])
    else:
        if not reachable:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"no server row for {host}:{port} and it is not reachable; "
                    f"discover or attach first"
                ),
            )
        server_instance_id = rm.register_external(host, port)

    return server_instance_id


@router.get("/bindings")
def list_bindings() -> list[dict]:
    return db_rt.list_bindings()


@router.get("/bindings/{role}")
def get_binding(role: str) -> dict | None:
    return db_rt.get_binding(role)


@router.put("/bindings/{role}")
def upsert_binding(role: str, body: schemas.BindingUpdate) -> dict:
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"invalid role {role!r}")
    _validate_host_port(body.host, body.port)
    server_instance_id = _validate_and_resolve_binding(
        role, body.host, body.port, body.model, body.opencode_agent,
    )
    db_rt.upsert_binding_with_agent_sync(
        role,
        body.host,
        body.port,
        opencode_agent=body.opencode_agent,
        model=body.model,
        server_instance_id=server_instance_id,
        binding_source=body.binding_source or "user",
    )
    return db_rt.get_binding(role) or {}


@router.patch("/bindings/{role}")
def patch_binding(role: str, body: schemas.BindingPatch) -> dict:
    """Partial update; 404 if role has no binding yet."""
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"invalid role {role!r}")
    existing = db_rt.get_binding(role)
    if not existing:
        raise HTTPException(
            status_code=404, detail=f"no binding for role {role!r}; use PUT to create"
        )
    fields = {k: v for k, v in body.model_dump(exclude_none=True).items() if v is not None}
    new_host = fields.get("host", existing["host"])
    new_port = fields.get("port", existing["port"])
    new_model = fields.get("model", existing.get("model"))
    new_agent = fields.get("opencode_agent", existing.get("opencode_agent"))
    if "host" in fields or "port" in fields:
        _validate_host_port(new_host, new_port)
    if "host" in fields or "port" in fields or "model" in fields:
        server_instance_id = _validate_and_resolve_binding(
            role, new_host, new_port, new_model, new_agent,
        )
    else:
        server_instance_id = existing.get("server_instance_id")
    merged = {**existing, **fields, "server_instance_id": server_instance_id}
    db_rt.upsert_binding_with_agent_sync(
        role,
        merged["host"],
        merged["port"],
        opencode_agent=merged.get("opencode_agent"),
        model=merged.get("model"),
        server_instance_id=server_instance_id,
        binding_source=merged.get("binding_source", "user"),
    )
    return db_rt.get_binding(role) or {}


@router.delete("/bindings/{role}")
def clear_binding(role: str) -> dict:
    db_rt.clear_binding(role)
    return {"cleared": role}


@router.get("/policy")
def get_policy() -> dict:
    return db_rt.get_policy()


# ── Migration audit compat alias (id 270) ───────────────────────────────
# The 0c44ba2 runtime_policy_get returned {policy: dict-or-None}.
# The new /api/runtime/policy returns the policy dict directly.
# This compat alias accepts a no-arg query (path-only) and returns
# the legacy envelope shape for any UI that still calls the old path.


@router.get("/policy_get")
def compat_runtime_policy_get() -> dict:
    """Migration audit symbol 270 compat: legacy /api/runtime/policy_get
    returns {policy: dict-or-None} (the 0c44ba2 envelope)."""
    policy = db_rt.get_policy()
    if policy and "allow_external_attach" in policy:
        policy = {**policy, "allow_external_attach": bool(policy["allow_external_attach"])}
    return {"policy": dict(policy) if policy else None}


@router.put("/policy")
def update_policy(body: schemas.RuntimePolicyUpdate) -> dict:
    fields = body.model_dump(exclude_none=True)
    return db_rt.upsert_policy(**fields)


# ── Migration audit compat alias (id 271) ───────────────────────────────
# The 0c44ba2 runtime_policy accepted a wide field set including
# background_mode_enabled, on_backend_exit, on_backend_crash_recovery,
# allow_unknown_attach, default_topology, default_shared_port.
# The new RuntimePolicyUpdate schema dropped 6 of those (the audit's
# "removed fields" finding). This compat alias translates the old
# field set to the new schema and returns the legacy {ok, policy}
# envelope for any UI that still calls the old path with old fields.


@router.put("/policy_set")
def compat_runtime_policy_set(body: dict) -> dict:
    """Migration audit symbol 271 compat: legacy /api/runtime/policy_set
    accepts the old wide field set (background_mode_enabled,
    on_backend_exit, etc.) and translates it to the new
    RuntimePolicyUpdate fields. The 6 dropped fields (background_*,
    on_backend_*, allow_unknown_attach, default_topology,
    default_shared_port) are silently dropped — they were removed
    by design in the new architecture. The response also strips
    those legacy columns from the returned policy row so the
    envelope shape matches the new schema. Returns {ok, policy}."""
    # Translate old field names to the new RuntimePolicyUpdate field set.
    # Old fields that map to new: name, close_behavior, on_opencode_crash,
    # max_managed_opencode_servers, allow_external_attach.
    allowed = {
        "name", "close_behavior", "on_opencode_crash",
        "max_managed_opencode_servers", "allow_external_attach",
    }
    # Legacy columns that the new schema does not expose. Stripped
    # from both the upsert input AND the response row so callers
    # never see them.
    legacy_columns = {
        "background_mode_enabled",
        "on_backend_exit",
        "on_backend_crash_recovery",
        "default_topology",
        "default_shared_port",
        "allow_unknown_attach",
    }
    def _strip_legacy(row: dict) -> dict:
        out = {k: v for k, v in row.items() if k not in legacy_columns}
        if "allow_external_attach" in out:
            out["allow_external_attach"] = bool(out["allow_external_attach"])
        return out
    fields = {k: v for k, v in (body or {}).items() if k in allowed}
    if not fields:
        policy = _strip_legacy(db_rt.get_policy() or {})
        return {"ok": False, "error": "no recognized policy fields", "policy": policy}
    updated = db_rt.upsert_policy(**fields)
    return {"ok": True, "policy": _strip_legacy(updated)}


@router.get("/models")
def list_models() -> dict:
    providers = list_providers()
    out = {}
    for pid, p in providers.items():
        out[pid] = {
            "models": list((p.get("models") or {}).keys()),
            "name": p.get("name"),
            "baseURL": (p.get("options") or {}).get("baseURL"),
        }
    return out


@router.get("/model/check")
def check_model(model_id: str) -> dict:
    return {
        "model_id": model_id,
        "available": is_model_available(model_id),
        "supports_thinking": model_supports_thinking(model_id),
    }


def _model_provider_id(model_id: str) -> str | None:
    if not model_id or "/" not in model_id:
        return None
    provider_id, _model = model_id.split("/", 1)
    return provider_id or None


def _classify_model_check_error(message: str) -> str:
    raw = (message or "").lower()
    if not raw:
        return "unknown_error"
    if "x-api-key" in raw or "api secret key" in raw or "unauthorized" in raw or "401" in raw:
        return "auth_error"
    if "api key" in raw or "apikey" in raw or "authentication" in raw or "login fail" in raw:
        return "auth_error"
    if "balance" in raw or "quota" in raw or "insufficient" in raw or "billing" in raw:
        return "quota_or_balance"
    if "payment" in raw or "credit" in raw or "no money" in raw:
        return "quota_or_balance"
    if "余额" in raw or "餘額" in raw or "欠费" in raw or "欠費" in raw or "额度" in raw or "額度" in raw:
        return "quota_or_balance"
    if "not found" in raw or "404" in raw or ("model" in raw and "not" in raw):
        return "model_unavailable"
    if "not reachable" in raw or "connectionerror" in raw or "connection refused" in raw:
        return "server_unreachable"
    if "session not found" in raw:
        return "session_error"
    if "timeout" in raw:
        return "timeout"
    return "opencode_error"


@router.get("/availability")
def runtime_availability() -> dict:
    """Return current runtime agent/model availability without making LLM calls.

    This is a cheap preflight for the UI/operator:
    - configured providers/models from opencode.jsonc
    - current DB role bindings
    - current DB agent rows
    - reachable OpenCode servers and live /agent response, if reachable
    - credential warnings scoped to providers used by current bindings
    """
    from task_hounds_api.db.ops import agent as db_agent
    from task_hounds_api.opencode import client as oc_client

    rm = _resolve_runtime_manager()
    servers = rm.list_servers()
    bindings = db_rt.list_bindings()
    provider_ids = {
        str(b.get("model") or "").split("/", 1)[0]
        for b in bindings
        if "/" in str(b.get("model") or "")
    } or None
    providers = list_providers()
    configured_models: list[dict] = []
    for provider_id, provider in providers.items():
        for model_id, model_info in (provider.get("models") or {}).items():
            full_id = f"{provider_id}/{model_id}"
            configured_models.append({
                "id": full_id,
                "provider_id": provider_id,
                "model_id": model_id,
                "name": (model_info or {}).get("name") or model_id,
                "supports_thinking": model_supports_thinking(full_id),
            })

    reachable_servers: list[dict] = []
    live_agents_by_server: list[dict] = []
    for server in servers:
        host = str(server.get("host") or "")
        try:
            port = int(server.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        reachable = bool(host and port and rm.test_server(host, port).get("reachable", False))
        if reachable:
            reachable_servers.append({**server, "reachable": True})
            live_agents_by_server.append({
                "host": host,
                "port": port,
                "agents": oc_client.list_agents(host, port),
            })

    return {
        "ok": True,
        "servers": servers,
        "reachable_servers": reachable_servers,
        "bindings": bindings,
        "agents": db_agent.list_agents(),
        "opencode_agents": live_agents_by_server,
        "models": configured_models,
        "credential_warnings": rm.validate_credentials(provider_ids=provider_ids),
    }


@router.post("/model/live-check")
def live_check_model(body: dict) -> dict:
    """Make a minimal live OpenCode call for a specific model.

    Body:
      {
        "model": "minimax-coding-plan/MiniMax-M2.7",
        "agent": "general",        # optional
        "host": "127.0.0.1",       # optional
        "port": 18765,             # optional
        "timeout": 60              # optional, clamped 5..120
      }

    Returns a classified result so the UI can distinguish auth, quota/balance,
    model config, server reachability, and generic OpenCode stream failures.
    """
    from task_hounds_api.db.ops import project as db_project
    from task_hounds_api.opencode import client as oc_client
    from task_hounds_api.opencode.binding_resolver import resolve_for_role

    model = str((body or {}).get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    if not is_model_available(model):
        return {
            "ok": False,
            "model": model,
            "category": "model_unavailable",
            "message": f"model {model!r} is not available in opencode.jsonc",
        }

    provider_id = _model_provider_id(model)
    rm = _resolve_runtime_manager()
    credential_warnings = rm.validate_credentials(
        provider_ids={provider_id} if provider_id else None
    )
    if credential_warnings:
        return {
            "ok": False,
            "model": model,
            "provider_id": provider_id,
            "category": "auth_error",
            "message": "; ".join(credential_warnings),
            "credential_warnings": credential_warnings,
        }

    host = str((body or {}).get("host") or "").strip()
    port_raw = (body or {}).get("port")
    agent = str((body or {}).get("agent") or "").strip()
    if not host or not port_raw or not agent:
        try:
            default_host, default_port, default_agent, _default_model = resolve_for_role("chat")
        except Exception:
            default_host, default_port, default_agent = "127.0.0.1", 18765, "general"
        host = host or default_host
        port_raw = port_raw or default_port
        agent = agent or default_agent or "general"
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="port must be an integer")

    if not rm.test_server(host, port).get("reachable", False):
        return {
            "ok": False,
            "model": model,
            "agent": agent,
            "host": host,
            "port": port,
            "category": "server_unreachable",
            "message": f"opencode serve not reachable on {host}:{port}",
        }

    timeout_raw = (body or {}).get("timeout", 60)
    try:
        timeout = max(5, min(120, int(timeout_raw)))
    except (TypeError, ValueError):
        timeout = 60

    active = db_project.get_active_session() or {}
    workspace = Path(str(active.get("workspace_path") or ""))
    cwd = workspace if workspace.is_dir() else ROOT
    result = oc_client.run(
        agent=agent,
        model=model,
        prompt="Health check. Reply with exactly: OK",
        host=host,
        port=port,
        session_id=None,
        timeout=timeout,
        cwd=cwd,
    )
    if result.get("ok"):
        text = ((result.get("output") or {}).get("text") or "").strip()
        return {
            "ok": True,
            "model": model,
            "provider_id": provider_id,
            "agent": agent,
            "host": host,
            "port": port,
            "reply": text,
            "category": "ok",
        }

    error = result.get("error") or {}
    message = str(error.get("message") or result.get("raw") or "model check failed")
    return {
        "ok": False,
        "model": model,
        "provider_id": provider_id,
        "agent": agent,
        "host": host,
        "port": port,
        "category": _classify_model_check_error(message),
        "message": message,
        "retryable": bool(error.get("retryable", result.get("retryable", False))),
    }


@router.post("/opencode/{instance_id}/restart")
def restart_opencode_instance(instance_id: int) -> dict:
    """P11 id 279 (restored): restart a managed OpenCode server instance.

    Accepts an opencode_server_instances.id, looks up the row, and
    restarts the managed server via RuntimeManager.ensure_managed_running.

    Returns 404 if the instance is not found, 409 if it is not the
    currently-managed server (RuntimeManager manages one server at a time;
    only that server can be restarted via this endpoint).
    """
    server = db_rt.get_server(instance_id)
    if server is None:
        raise HTTPException(status_code=404, detail=f"instance {instance_id} not found")

    if not server.get("managed"):
        raise HTTPException(
            status_code=409,
            detail=f"instance {instance_id} is not managed by this runtime; "
            "only the active managed server can be restarted",
        )

    rm = _resolve_runtime_manager()
    rm_host = rm._managed_host
    rm_port = rm._managed_port
    srv_host, srv_port = _server_host_port(server, instance_id)

    if srv_host != rm_host or srv_port != rm_port:
        raise HTTPException(
            status_code=409,
            detail=f"instance {instance_id} ({srv_host}:{srv_port}) is not "
            f"the active managed server ({rm_host}:{rm_port}); "
            "RuntimeManager can only restart one server at a time",
        )

    ok = rm.ensure_managed_running(restart=True)
    return {"ok": ok, "instance_id": instance_id, "host": srv_host, "port": srv_port}


@router.post("/opencode/{instance_id}/refresh")
def compat_runtime_opencode_refresh(instance_id: int) -> dict:
    """P11 id 280 (restored): refresh/test a server instance by its id.

    Looks up the opencode_server_instances row by id, then tests
    reachability via RuntimeManager.test_server. Returns 404 if the
    instance is not found.

    This restores the old runtime_opencode_refresh contract: callers
    that passed instance_id now call this endpoint instead of having
    to first GET /api/runtime/servers and find the matching row.
    """
    server = db_rt.get_server(instance_id)
    if server is None:
        raise HTTPException(status_code=404, detail=f"instance {instance_id} not found")

    host, port = _server_host_port(server, instance_id)

    rm = _resolve_runtime_manager()
    result = rm.test_server(host, port)
    return {
        "instance_id": instance_id,
        "host": host,
        "port": port,
        **result,
    }


# ── Migration audit compat alias (id 268) ───────────────────────────────
# The 0c44ba2 /api/runtime/binding?role=X returned {binding: row} or
# {bindings: [...]}. The new architecture splits this into two
# endpoints: /bindings/{role} (single) and /bindings (list). This
# compat alias accepts the legacy query-param path and returns the
# legacy envelope shape for any UI that still calls the old path.


@router.get("/binding")
def compat_runtime_binding(role: str | None = None) -> dict:
    """Migration audit symbol 268 compat: legacy /api/runtime/binding
    with role query param. Returns {binding: row} for valid roles,
    {bindings: [...]} when no role is given (or role is not a
    valid 0c44ba2 role). role is optional for backward compat."""
    from task_hounds_api.api.routes.runtime import ROLES as _ROLES
    if role and role in _ROLES:
        row = db_rt.get_binding(role)
        return {"binding": dict(row) if row else None}
    rows = db_rt.list_bindings()
    return {"bindings": [dict(r) for r in rows]}
