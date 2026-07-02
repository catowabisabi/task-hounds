"""DB ops for agent_registry.

Pure CRUD. Manager/Worker/Reviewer/Chat roles.
"""
from __future__ import annotations

import os
from pathlib import Path
from task_hounds_api.db import connect


def list_agents(path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM agent_registry ORDER BY role, name"
        ).fetchall()
    return [dict(r) for r in rows]


def get_agent(name: str, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute("SELECT * FROM agent_registry WHERE name=?", (name,)).fetchone()
    return dict(row) if row else None


def get_agents_by_role(role: str, path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM agent_registry WHERE role=? ORDER BY name", (role,)
        ).fetchall()
    return [dict(r) for r in rows]


ALLOWED_UPDATE_FIELDS = frozenset({
    "host",
    "port",
    "model",
    "opencode_agent",
    "state",
    "last_error",
    "current_step",
    "current_step_started_at",
    "step_source",
    "last_stream_at",
    "last_seen",
    "project_session_id",
    "role_session_id",
    "task_complete",
})


def update_agent(name: str, path: Path | None = None, **fields) -> None:
    if not fields:
        return
    keys = [k for k in fields if k in ALLOWED_UPDATE_FIELDS]
    if not keys:
        return
    sets = ", ".join(f"{k}=?" for k in keys) + ", updated_at=CURRENT_TIMESTAMP"
    values = [fields[k] for k in keys] + [name]
    with connect(path) as db:
        db.execute(f"UPDATE agent_registry SET {sets} WHERE name=?", values)
        db.commit()


def clear_transient_ui_state(path: Path | None = None) -> None:
    """Clear process-global presentation state on project switches.

    Durable, session-scoped execution state lives in agent_execution_state.
    agent_registry is only a legacy/current UI projection and must not carry an
    error or timer from one project into another.
    """
    with connect(path) as db:
        db.execute(
            """UPDATE agent_registry
                  SET state='idle',
                      current_step=NULL,
                      current_step_started_at=NULL,
                      step_source=NULL,
                      last_stream_at=NULL,
                      last_error=NULL,
                      last_error_at=NULL,
                      project_session_id=NULL,
                      role_session_id=NULL,
                      updated_at=CURRENT_TIMESTAMP"""
        )
        db.commit()


def seed_default_agents(path: Path | None = None) -> None:
    """Insert the 4 default agents (manager, worker, reviewer, chat) if missing."""
    shared_host = os.environ.get("POWER_TEAMS_OPENCODE_HOST", "127.0.0.1")
    shared_port = int(os.environ.get("POWER_TEAMS_OPENCODE_PORT", "18765"))
    rows = [
        (
            "manager_0001", "manager", "manager",
            os.environ.get("POWER_TEAMS_MANAGER_OPENCODE_HOST", shared_host),
            int(os.environ.get("POWER_TEAMS_MANAGER_OPENCODE_PORT", shared_port)),
            os.environ.get("POWER_TEAMS_MANAGER_MODEL") or os.environ.get("POWER_TEAMS_DEFAULT_MODEL"),
            os.environ.get("POWER_TEAMS_MANAGER_OPENCODE_AGENT", "general"),
        ),
        (
            "worker_0001", "worker", "worker",
            os.environ.get("POWER_TEAMS_WORKER_OPENCODE_HOST", shared_host),
            int(os.environ.get("POWER_TEAMS_WORKER_OPENCODE_PORT", shared_port)),
            os.environ.get("POWER_TEAMS_WORKER_MODEL") or os.environ.get("POWER_TEAMS_DEFAULT_MODEL"),
            os.environ.get("POWER_TEAMS_WORKER_OPENCODE_AGENT", "general"),
        ),
        (
            "reviewer_0001", "reviewer", "reviewer",
            os.environ.get("POWER_TEAMS_REVIEWER_OPENCODE_HOST", shared_host),
            int(os.environ.get("POWER_TEAMS_REVIEWER_OPENCODE_PORT", shared_port)),
            os.environ.get("POWER_TEAMS_REVIEWER_MODEL") or os.environ.get("POWER_TEAMS_DEFAULT_MODEL"),
            os.environ.get("POWER_TEAMS_REVIEWER_OPENCODE_AGENT", "general"),
        ),
        (
            "chat_0001", "chat", "chat",
            os.environ.get("POWER_TEAMS_CHAT_OPENCODE_HOST", shared_host),
            int(os.environ.get("POWER_TEAMS_CHAT_OPENCODE_PORT", shared_port)),
            os.environ.get("POWER_TEAMS_CHAT_MODEL") or os.environ.get("POWER_TEAMS_DEFAULT_MODEL"),
            os.environ.get("POWER_TEAMS_CHAT_OPENCODE_AGENT", "general"),
        ),
    ]
    with connect(path) as db:
        db.executemany(
            """
            INSERT OR IGNORE INTO agent_registry
                (id, name, role, host, port, model, opencode_agent, state, task_complete)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'idle', 0)
            """,
            rows,
        )
        db.commit()
