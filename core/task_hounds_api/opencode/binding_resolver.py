"""opencode.binding_resolver — single source of truth for "which OpenCode
host/port/agent/model does role X use right now?".

Priority chain (first non-None wins):
  1. agent_runtime_bindings row for the role (DB)
  2. env vars TASK_HOUNDS_OPENCODE_PORT / *_AGENT / *_MODEL
  3. defaults: 127.0.0.1 / 18765 / "general" /
     "minimax-coding-plan/MiniMax-M2.7"

Replaces the hardcoded `host=127.0.0.1, port=18765` defaults that
were sprinkled across the worker / reviewer / loop / runtime
modules. The binding table is now actually consulted end-to-end
instead of being UI-only.
"""
from __future__ import annotations

import os


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18765
DEFAULT_AGENT = "general"
DEFAULT_MODEL = "minimax-coding-plan/MiniMax-M2.7"


def _agent_for_role(role: str) -> str:
    specific = os.environ.get(f"TASK_HOUNDS_{role.upper()}_OPENCODE_AGENT")
    if specific:
        return specific
    return os.environ.get("TASK_HOUNDS_OPENCODE_AGENT", DEFAULT_AGENT)


def _model_for_role(role: str) -> str:
    specific = os.environ.get(f"TASK_HOUNDS_{role.upper()}_OPENCODE_MODEL")
    if specific:
        return specific
    fallback = os.environ.get("TASK_HOUNDS_OPENCODE_MODEL")
    return fallback or DEFAULT_MODEL


def _port_default() -> int:
    raw = os.environ.get("TASK_HOUNDS_OPENCODE_PORT")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return DEFAULT_PORT


def resolve_for_role(role: str) -> tuple[str, int, str, str]:
    """Return (host, port, opencode_agent, model) for the given role.

    The DB binding row wins over everything. When no binding is set,
    env vars fill the gap; final fallback is the hardcoded defaults.

    Phase-9 (P0-5) staleness guard: if the DB binding's host/port
    is not reachable, this function asks RuntimeManager to repair
    the bindings (which may rewrite the row to a reachable
    server) and re-reads. If repair cannot find any reachable
    server, it raises RuntimeError with a clear message naming
    the role and the stale host/port — never silently returns
    a dead port.
    """
    try:
        from task_hounds_api.db.ops import runtime as db_rt

        binding = db_rt.get_binding(role)
    except Exception:
        binding = None

    if binding:
        host = binding.get("host") or DEFAULT_HOST
        port_raw = binding.get("port") or _port_default()
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            port = _port_default()
        agent = binding.get("opencode_agent") or _agent_for_role(role)
        model = binding.get("model") or _model_for_role(role)

        # Staleness guard: only check reachability for loopback
        # hosts. External hosts (e.g. 10.0.0.5) are operator-
        # configured and may be on a different network where we
        # can't reliably probe. Trust them.
        if _is_loopback(host) and not _is_reachable(host, port):
            try:
                from task_hounds_api.opencode.runtime_manager import RuntimeManager
                RuntimeManager.instance().repair_role_bindings()
                binding = db_rt.get_binding(role)
                if binding:
                    host = binding.get("host") or host
                    port = int(binding.get("port") or port)
                    if _is_loopback(host) and not _is_reachable(host, port):
                        raise RuntimeError(
                            f"binding for {role} points to unreachable "
                            f"{host}:{port}; no reachable server available. "
                            f"Start a managed OpenCode or attach a reachable server."
                        )
            except RuntimeError:
                raise
            except Exception:
                raise RuntimeError(
                    f"binding for {role} points to unreachable "
                    f"{host}:{port}; runtime repair failed. "
                    f"Start a managed OpenCode or attach a reachable server."
                )
        return host, port, agent, model

    host = DEFAULT_HOST
    port = _port_default()
    agent = _agent_for_role(role)
    model = _model_for_role(role)
    return host, port, agent, model


def _is_reachable(host: str, port: int) -> bool:
    try:
        from task_hounds_api.opencode.process import is_reachable
        return bool(is_reachable(host, int(port), timeout=0.6))
    except Exception:
        return False


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")
