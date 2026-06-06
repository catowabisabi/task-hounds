"""DB ops for runtime state.

Tables covered:
  opencode_server_instances — tracked OpenCode processes
  runtime_policy            — runtime behavior settings
  agent_runtime_bindings    — OpenCode server bindings per role
  sessions_arch             — archived session keys
"""
from __future__ import annotations

from pathlib import Path
from task_hounds_api.db import connect


# ── opencode_server_instances ────────────────────────────────────────────────

def register_server(
    project_session_id: str,
    agent_role: str,
    host: str,
    port: int,
    opencode_session_id: str | None = None,
    project_folder: str | None = None,
    pid: int | None = None,
    path: Path | None = None,
) -> int:
    with connect(path) as db:
        cur = db.execute(
            """
            INSERT INTO opencode_server_instances
                (power_teams_session_id, agent_role, host, port, opencode_session_id, project_folder, pid, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (project_session_id, agent_role, host, port, opencode_session_id, project_folder, pid),
        )
        db.commit()
    return int(cur.lastrowid)


def list_servers(path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM opencode_server_instances ORDER BY started_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def unregister_servers_for_session(project_session_id: str, path: Path | None = None) -> int:
    with connect(path) as db:
        cur = db.execute(
            "DELETE FROM opencode_server_instances WHERE power_teams_session_id=?",
            (project_session_id,),
        )
        db.commit()
    return cur.rowcount


# ── runtime_policy ───────────────────────────────────────────────────────────

DEFAULT_POLICY = {
    "name": "default",
    "close_behavior": "ask",
    "background_mode_enabled": 0,
    "on_backend_exit": "stop_managed_opencode",
    "on_backend_crash_recovery": "ask",
    "on_opencode_crash": "mark_error",
    "max_managed_opencode_servers": 1,
    "default_topology": "shared",
    "default_shared_port": 18765,
    "allow_external_attach": 1,
    "allow_unknown_attach": 0,
}


def get_policy(path: Path | None = None) -> dict:
    with connect(path) as db:
        row = db.execute("SELECT * FROM runtime_policy WHERE id=1").fetchone()
    if row:
        return dict(row)
    return {"id": 1, **DEFAULT_POLICY}


def upsert_policy(path: Path | None = None, **fields) -> dict:
    fields = {**DEFAULT_POLICY, **fields}
    fields.pop("id", None)
    with connect(path) as db:
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{k}=excluded.{k}" for k in fields)
        values = list(fields.values())
        db.execute(
            f"INSERT INTO runtime_policy (id, {cols}, updated_at) "
            f"VALUES (1, {placeholders}, CURRENT_TIMESTAMP) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}, updated_at=CURRENT_TIMESTAMP",
            values,
        )
        db.commit()
    return get_policy(path)


# ── agent_runtime_bindings ──────────────────────────────────────────────────

def upsert_binding(
    role: str,
    host: str,
    port: int,
    *,
    opencode_agent: str | None = None,
    model: str | None = None,
    server_instance_id: int | None = None,
    binding_source: str = "auto",
    path: Path | None = None,
) -> None:
    with connect(path) as db:
        existing = db.execute(
            "SELECT id FROM agent_runtime_bindings WHERE role=?",
            (role,),
        ).fetchone()
        if existing is None:
            db.execute(
                """
                INSERT INTO agent_runtime_bindings
                    (role, host, port, opencode_agent, model,
                     server_instance_id, binding_source, updated_at)
                VALUES (?, ?, ?, COALESCE(?, 'general'), ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (role, host, port, opencode_agent, model,
                 server_instance_id, binding_source),
            )
        else:
            db.execute(
                """
                UPDATE agent_runtime_bindings
                SET host=?, port=?,
                    opencode_agent=COALESCE(?, opencode_agent),
                    model=COALESCE(?, model),
                    server_instance_id=COALESCE(?, server_instance_id),
                    binding_source=COALESCE(?, binding_source),
                    updated_at=CURRENT_TIMESTAMP
                WHERE role=?
                """,
                (host, port, opencode_agent, model,
                 server_instance_id, binding_source, role),
            )
        db.commit()


def upsert_binding_with_agent_sync(
    role: str,
    host: str,
    port: int,
    *,
    opencode_agent: str | None = None,
    model: str | None = None,
    server_instance_id: int | None = None,
    binding_source: str = "user",
    agent_name: str | None = None,
    path: Path | None = None,
) -> None:
    """Atomic binding write + agent_registry sync in ONE DB
    transaction. Either both writes commit or neither does. The
    previous two-step approach (upsert_binding then a separate
    update_agent) opened two connections and committed twice; if
    the second commit failed (DB lock, disk full, killed process)
    the binding would be saved with a stale agent_registry row.

    `agent_name` is the agent_registry.name to sync (e.g. 'manager').
    When None, defaults to `role` (the four default roles are
    1:1 with their registry names). The agent_registry update is
    a no-op if neither model nor opencode_agent is provided, or
    if the agent_registry row does not exist.
    """
    target_agent = agent_name if agent_name is not None else role
    with connect(path) as db:
        existing = db.execute(
            "SELECT id FROM agent_runtime_bindings WHERE role=?",
            (role,),
        ).fetchone()
        if existing is None:
            db.execute(
                """
                INSERT INTO agent_runtime_bindings
                    (role, host, port, opencode_agent, model,
                     server_instance_id, binding_source, updated_at)
                VALUES (?, ?, ?, COALESCE(?, 'general'), ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (role, host, port, opencode_agent, model,
                 server_instance_id, binding_source),
            )
        else:
            db.execute(
                """
                UPDATE agent_runtime_bindings
                SET host=?, port=?,
                    opencode_agent=COALESCE(?, opencode_agent),
                    model=COALESCE(?, model),
                    server_instance_id=COALESCE(?, server_instance_id),
                    binding_source=COALESCE(?, binding_source),
                    updated_at=CURRENT_TIMESTAMP
                WHERE role=?
                """,
                (host, port, opencode_agent, model,
                 server_instance_id, binding_source, role),
            )
        if model or opencode_agent:
            agent_row = db.execute(
                "SELECT id FROM agent_registry WHERE name=?",
                (target_agent,),
            ).fetchone()
            if agent_row is not None:
                sets: list[str] = []
                values: list = []
                if model:
                    sets.append("model=?")
                    values.append(model)
                if opencode_agent:
                    sets.append("opencode_agent=?")
                    values.append(opencode_agent)
                if sets:
                    sets.append("updated_at=CURRENT_TIMESTAMP")
                    values.append(target_agent)
                    db.execute(
                        f"UPDATE agent_registry SET {', '.join(sets)} WHERE name=?",
                        values,
                    )
        db.commit()


def get_binding(role: str, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM agent_runtime_bindings WHERE role=?", (role,)
        ).fetchone()
    return dict(row) if row else None


def list_bindings(path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute("SELECT * FROM agent_runtime_bindings ORDER BY role").fetchall()
    return [dict(r) for r in rows]


def clear_binding(role: str, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute("DELETE FROM agent_runtime_bindings WHERE role=?", (role,))
        db.commit()


# ── sessions_arch ───────────────────────────────────────────────────────────

def is_archived(session_key: str, path: Path | None = None) -> bool:
    with connect(path) as db:
        row = db.execute(
            "SELECT 1 FROM sessions_arch WHERE session_key=? LIMIT 1", (session_key,)
        ).fetchone()
    return row is not None


def archive_session(session_key: str, agent_name: str = "", path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute(
            """
            INSERT INTO sessions_arch
                (session_key, session_name, agent_name, worker_status, last_active_at, archived_at)
            VALUES (?, ?, ?, 'archived', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (session_key, session_key, agent_name),
        )
        db.commit()


def unarchive_session(session_key: str, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute("DELETE FROM sessions_arch WHERE session_key=?", (session_key,))
        db.commit()


def list_archived(path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = db.execute(
            "SELECT * FROM sessions_arch ORDER BY archived_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
