"""api.routes.runtime — OpenCode server management and runtime policy.

Authoritative owner of all /api/runtime/* routes. The compat.py
duplicates have been removed; do not re-add them there.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from task_hounds_api.db.ops import runtime as db_rt
from task_hounds_api.opencode.config import list_providers, is_model_available, model_supports_thinking
from task_hounds_api.api import schemas

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


def _resolve_runtime_manager():
    from task_hounds_api.opencode.runtime_manager import RuntimeManager
    return RuntimeManager.instance()


def _validate_host_port(host: str, port: int) -> None:
    if not host or not isinstance(host, str):
        raise HTTPException(status_code=400, detail="host must be a non-empty string")
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise HTTPException(status_code=400, detail="port must be an integer 1..65535")


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
    if len(bindings) != 4:
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
    cred_warnings = rm.validate_credentials()
    servers = rm.list_servers()
    bindings = db_rt.list_bindings()
    cred_ok = not cred_warnings

    active_servers = [
        s for s in servers
        if s.get("status") != "ignored"
        and rm.test_server(s.get("host", ""), int(s.get("port", 0))).get("reachable", False)
    ]
    servers_ok = len(active_servers) > 0

    bindings_ok, bindings_reason = _bindings_are_fully_wired(rm, bindings, servers)

    if not cred_ok:
        unavailable_reason = "missing_credentials"
    elif not servers_ok:
        unavailable_reason = "no_reachable_server"
    elif not bindings_ok:
        unavailable_reason = bindings_reason or "bindings_invalid"
    else:
        unavailable_reason = None
    ready = unavailable_reason is None

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
        "active_work": None,
        "last_checkpoint": None,
        "role_bindings": bindings,
        "policy": db_rt.get_policy(),
        "managed_health": rm.get_managed_health(),
    }


@router.get("/opencode")
def list_opencode_servers() -> dict:
    """List known OpenCode server rows."""
    rm = _resolve_runtime_manager()
    servers = rm.list_servers()
    return {
        "servers": servers,
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


@router.post("/test")
def test_opencode_server(body: schemas.TestRequest) -> dict:
    """Ping a host/port and return reachability."""
    _validate_host_port(body.host, body.port)
    rm = _resolve_runtime_manager()
    return rm.test_server(body.host, body.port)


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


@router.put("/policy")
def update_policy(body: schemas.RuntimePolicyUpdate) -> dict:
    fields = body.model_dump(exclude_none=True)
    return db_rt.upsert_policy(**fields)


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
