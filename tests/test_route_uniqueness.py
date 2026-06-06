"""Targeted route uniqueness test for the runtime + chat endpoints.

Phase 1 only requires that /api/runtime/status has exactly one
authoritative handler (the runtime.py one, not a compat stub). The
broader compat-vs-new-route duplication is a Phase 2/3 cleanup item
(per the user's directive to preserve existing functionality and
avoid unrelated refactors).

Phase 3 added the same guarantee for /api/chat/*: every chat
endpoint (messages, send, status) must have exactly one handler in
api.routes.chat -- the compat duplicates were removed.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


def _handlers_for(app, method: str, path: str) -> list:
    return [
        r for r in app.routes
        if hasattr(r, "methods") and method in (r.methods or set()) and r.path == path
    ]


def test_api_runtime_status_has_exactly_one_handler():
    """GET /api/runtime/status must be served by exactly one handler.
    Previously compat.py shadowed runtime.py's authoritative route."""
    from task_hounds_api.api import create_app

    app = create_app()
    handlers = _handlers_for(app, "GET", "/api/runtime/status")
    assert len(handlers) == 1, (
        f"GET /api/runtime/status has {len(handlers)} handlers (expected 1): "
        f"{[getattr(h.endpoint, '__module__', '?') for h in handlers]}"
    )
    module = getattr(handlers[0].endpoint, "__module__", "")
    assert module.endswith("routes.runtime"), (
        f"GET /api/runtime/status should be served by api.routes.runtime, got module={module!r}"
    )


def test_chat_routes_have_exactly_one_handler_each():
    """Phase 3: every /api/chat/* endpoint must have exactly one
    handler in api.routes.chat. The compat duplicates were removed."""
    from task_hounds_api.api import create_app

    app = create_app()
    expected = [
        ("GET", "/api/chat/messages"),
        ("POST", "/api/chat/send"),
        ("GET", "/api/chat/status"),
    ]
    for method, path in expected:
        handlers = _handlers_for(app, method, path)
        assert len(handlers) == 1, (
            f"{method} {path} has {len(handlers)} handlers (expected 1): "
            f"{[getattr(h.endpoint, '__module__', '?') for h in handlers]}"
        )
        module = getattr(handlers[0].endpoint, "__module__", "")
        assert module.endswith("routes.chat"), (
            f"{method} {path} should be served by api.routes.chat, got module={module!r}"
        )


def test_phase6_compat_routes_have_exactly_one_handler_each():
    """Phase 6: the remaining compat duplicates (agents, todos,
    sessions, manager_messages, workflow_runs) were deleted. Each
    of these paths must now have exactly one handler, served by
    the authoritative route module."""
    from task_hounds_api.api import create_app

    app = create_app()
    expected = [
        # Group 1: Agents
        ("GET", "/api/agents", "routes.agents"),
        ("POST", "/api/agents/seed", "routes.agents"),
        # Group 2: Todos
        ("GET", "/api/todos", "routes.todos"),
        ("POST", "/api/todos", "routes.todos"),
        ("PATCH", "/api/todos/{todo_id}", "routes.todos"),
        ("DELETE", "/api/todos/{todo_id}", "routes.todos"),
        # Group 3: Sessions
        ("GET", "/api/sessions", "routes.projects"),
        ("POST", "/api/project-sessions/{session_id}/switch", "routes.projects"),
        # Group 4: Manager messages
        ("GET", "/api/manager-messages", "routes.workflow"),
        ("POST", "/api/manager-messages", "routes.workflow"),
        ("GET", "/api/workflows/flow_01/manager-messages", "routes.workflow"),
        ("GET", "/api/workflows/flow_01/runs", "routes.workflow"),
    ]
    for method, path, expected_module in expected:
        handlers = _handlers_for(app, method, path)
        assert len(handlers) == 1, (
            f"{method} {path} has {len(handlers)} handlers (expected 1): "
            f"{[getattr(h.endpoint, '__module__', '?') for h in handlers]}"
        )
        module = getattr(handlers[0].endpoint, "__module__", "")
        assert module.endswith(expected_module), (
            f"{method} {path} should be served by api.{expected_module}, got module={module!r}"
        )
