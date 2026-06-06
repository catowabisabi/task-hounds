"""opencode.runtime_manager — process-wide owner of the OpenCode serve
process and the registry of external servers.

This is the SINGLE SOURCE OF TRUTH for the managed OpenCode subprocess
handle, the opencode_server_instances DB table, and the four default
role bindings. Every endpoint, the workflow loop, and the agent
executors MUST go through `RuntimeManager.instance()` — never
construct OpenCodeLifecycle() ad-hoc, or stop() will fail to find
the original process handle.

Public API:
  instance()                            — process-wide singleton
  reset_instance()                      — tests only
  ensure_managed_running(restart=False) — idempotent
  get_managed_health() -> dict          — {ok, host, port, pid}
  stop_managed() -> bool
  register_external(host, port) -> int  — adds row, returns id
  list_servers() -> list[dict]
  test_server(host, port) -> dict       — {host, port, reachable}
  stop_server(instance_id) -> bool
  stop_all() -> dict                    — {ok, killed: {...}}
  reconcile_servers() -> int            — removes dead-pid rows
  auto_bind_four_roles() -> int         — upserts 4 default bindings
"""
from __future__ import annotations

import os
import threading
from typing import Any

from task_hounds_api.opencode.lifecycle import OpenCodeLifecycle


class RuntimeManager:
    """Process-wide singleton. Use `RuntimeManager.instance()` to obtain it."""

    _instance: "RuntimeManager | None" = None
    _creation_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._managed_host: str = "127.0.0.1"
        self._managed_port: int = int(
            os.environ.get("TASK_HOUNDS_OPENCODE_PORT", "18765")
        )
        self._managed_lifecycle: OpenCodeLifecycle | None = None

    # ── Singleton ──────────────────────────────────────────────────────────

    @classmethod
    def instance(cls) -> "RuntimeManager":
        if cls._instance is None:
            with cls._creation_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Drop the cached singleton. Tests only."""
        cls._instance = None

    # ── Managed server lifecycle ───────────────────────────────────────────

    def _ensure_lifecycle(self) -> OpenCodeLifecycle:
        """Return the managed lifecycle, constructing it once per singleton."""
        if self._managed_lifecycle is None:
            with self._lock:
                if self._managed_lifecycle is None:
                    self._managed_lifecycle = OpenCodeLifecycle(
                        self._managed_host, self._managed_port
                    )
        return self._managed_lifecycle

    def get_managed_lifecycle(self) -> OpenCodeLifecycle | None:
        """Public accessor for the managed OpenCodeLifecycle, or None if
        ensure_managed_running has not been called yet (or has been
        stopped). Callers should NOT reach into _managed_lifecycle
        directly — that field is private."""
        return self._managed_lifecycle

    def ensure_managed_running(self, *, restart: bool = False) -> bool:
        """Start the managed OpenCode if not already up. Idempotent.

        When the port is reachable but we have no proc handle (we did
        not start the server), the server is auto-registered as
        `owner='external'` so list_servers() reports it and bindings
        can point at its server_instance_id.
        """
        lc = self._ensure_lifecycle()
        if restart:
            try:
                lc.stop()
            except Exception:
                pass
            self._managed_lifecycle = None
            lc = self._ensure_lifecycle()
        ok = bool(lc.ensure_running())
        if not ok:
            return False
        proc = getattr(lc, "_proc", None) if lc is not None else None
        if proc is None:
            self.register_external(self._managed_host, self._managed_port)
        else:
            self._sync_managed_server_row()
        return True

    def _sync_managed_server_row(self) -> None:
        """Write a row into opencode_server_instances for the managed server
        so list_servers() reflects the running process. Idempotent.

        Skips the write when the lifecycle is reachable but we did not
        start it (no proc handle). Claiming a pre-existing 18765 server
        as 'managed power_teams' would be misleading — the operator
        started it, not us, and we cannot stop it.
        """
        from task_hounds_api.db import connect

        proc = getattr(self._managed_lifecycle, "_proc", None) if self._managed_lifecycle else None
        pid = getattr(proc, "pid", None) if proc is not None else None
        if pid is None:
            return
        with connect() as db:
            db.execute(
                "DELETE FROM opencode_server_instances "
                "WHERE power_teams_session_id='managed' AND host=? AND port=?",
                (self._managed_host, self._managed_port),
            )
            db.execute(
                """
                INSERT INTO opencode_server_instances
                    (power_teams_session_id, agent_role, host, port, pid, started_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                ("managed", "managed", self._managed_host, self._managed_port, pid),
            )
            db.commit()

    def get_managed_health(self) -> dict:
        """Return {ok, host, port, pid, credential_warnings} for the
        managed OpenCode process. credential_warnings is a list of human
        readable strings identifying providers whose apiKey is empty
        after ${ENV_VAR} expansion; the UI uses this to show a clear
        'runtime unavailable' banner instead of letting the opencode
        subprocess crash with exit code 1."""
        warnings = self.validate_credentials()
        if self._managed_lifecycle is None:
            return {
                "ok": False,
                "host": self._managed_host,
                "port": self._managed_port,
                "pid": None,
                "credential_warnings": warnings,
            }
        health = self._managed_lifecycle.health()
        health["credential_warnings"] = warnings
        return health

    def validate_credentials(self) -> list[str]:
        """Walk the opencode config and return a list of human-readable
        issues for any provider whose apiKey is empty after env-var
        expansion. Empty apiKeys cause the opencode CLI to fail with
        exit code 1 when it tries to call the LLM, which the dashboard
        surfaces as a generic 'Manager OpenCode call failed'. By
        surfacing the issue here, the UI can show a clear banner
        before the subprocess is ever spawned."""
        from task_hounds_api.opencode.config import list_providers

        warnings: list[str] = []
        try:
            providers = list_providers()
        except FileNotFoundError:
            return ["opencode config not found"]
        for provider_id, provider in providers.items():
            opts = provider.get("options") or {}
            api_key = opts.get("apiKey") or ""
            if not api_key:
                warnings.append(
                    f"provider {provider_id!r} has empty apiKey — set the "
                    f"env var referenced in opencode.jsonc (look for "
                    f"${{...}} next to apiKey) or paste a real key."
                )
        return warnings

    def stop_managed(self) -> tuple[bool, str]:
        """Stop the managed OpenCode subprocess and verify the
        process actually died.

        Critical: the real `OpenCodeLifecycle.stop()` sets
        `self._proc = None` after killing, so reading `_proc` AFTER
        calling `stop()` would always return None and falsely report
        success. We therefore save a reference to the original proc
        BEFORE calling `stop()` and then check the saved reference's
        `poll()` value AFTER.

        Returns:
          (True,  "no managed proc to stop") — no managed proc exists
                                                (lifespan never
                                                started one); this
                                                is a no-op, not a
                                                failure.
          (True,  "")                       — proc was running and
                                                is now dead.
          (False, "<error message>")        — proc was running but
                                                still alive after
                                                stop, lifecycle.stop
                                                raised, or poll
                                                after stop raised.
        """
        if self._managed_lifecycle is None:
            return True, "no managed lifecycle"
        proc = getattr(self._managed_lifecycle, "_proc", None)
        if proc is None:
            return True, "no managed proc to stop"
        try:
            self._managed_lifecycle.stop()
        except Exception as exc:
            return False, f"stop raised: {exc}"
        try:
            alive = proc.poll() is None
        except Exception as exc:
            return False, f"poll after stop raised: {exc}"
        if alive:
            return False, "process still alive after stop"
        return True, ""

    # ── Server registry (managed + external) ───────────────────────────────

    def register_external(self, host: str, port: int) -> int:
        """Record an externally-discovered OpenCode server. Returns the
        new id. The row is marked owner='external' / managed=0 so the
        UI can distinguish operator-managed processes from servers we
        started ourselves (and therefore can stop).

        Idempotent: returns the existing id if a row for host/port
        already exists. Discover and Attach both rely on this so a
        repeated call never produces duplicate rows.
        """
        from task_hounds_api.db import connect

        with connect() as db:
            existing = db.execute(
                "SELECT id FROM opencode_server_instances WHERE host=? AND port=?",
                (host, port),
            ).fetchone()
            if existing:
                return int(existing["id"])
            cur = db.execute(
                """
                INSERT INTO opencode_server_instances
                    (power_teams_session_id, agent_role, host, port, pid,
                     owner, managed, status, started_at)
                VALUES (?, ?, ?, ?, NULL, 'external', 0, 'reachable',
                        CURRENT_TIMESTAMP)
                """,
                ("external", f"external-{port}", host, port),
            )
            db.commit()
        return int(cur.lastrowid)

    def list_servers(self) -> list[dict]:
        """Return all known servers (managed + external)."""
        from task_hounds_api.db.ops import runtime as db_rt
        return db_rt.list_servers()

    def test_server(self, host: str, port: int) -> dict:
        """Ping a server by host/port. Returns reachability snapshot."""
        from task_hounds_api.opencode.process import is_reachable

        return {
            "host": host,
            "port": port,
            "reachable": bool(is_reachable(host, port, timeout=1.0)),
        }

    def ignore_server(self, host: str, port: int, reason: str = "") -> bool:
        """Mark (host, port) as ignored. The row is upserted with
        status='ignored' so subsequent discover scans do not reattach
        it. Returns True on success."""
        from task_hounds_api.db import connect

        with connect() as db:
            existing = db.execute(
                "SELECT id FROM opencode_server_instances WHERE host=? AND port=?",
                (host, port),
            ).fetchone()
            if existing:
                db.execute(
                    "UPDATE opencode_server_instances "
                    "SET status='ignored', last_error=? WHERE id=?",
                    (reason or "ignored by user", existing["id"]),
                )
            else:
                db.execute(
                    """INSERT INTO opencode_server_instances
                       (power_teams_session_id, agent_role, host, port, pid,
                        owner, managed, status, started_at, last_error)
                       VALUES (?, ?, ?, ?, NULL, 'external', 0, 'ignored',
                               CURRENT_TIMESTAMP, ?)""",
                    ("external", f"ignored-{port}", host, port, reason or "ignored by user"),
                )
            db.commit()
        return True

    def unignore_server(self, host: str, port: int) -> bool:
        """Clear the 'ignored' status so a future discover scan can
        re-attach this server. The ignored row is deleted; the next
        discover scan will register a fresh row. Returns True if a
        row was deleted."""
        from task_hounds_api.db import connect

        with connect() as db:
            cur = db.execute(
                "DELETE FROM opencode_server_instances "
                "WHERE host=? AND port=? AND status='ignored'",
                (host, port),
            )
            db.commit()
        return cur.rowcount > 0

    def list_ignored_servers(self) -> list[dict]:
        """Return all server rows with status='ignored'."""
        from task_hounds_api.db import connect

        with connect() as db:
            rows = db.execute(
                "SELECT * FROM opencode_server_instances "
                "WHERE status='ignored' ORDER BY started_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def _is_ignored(self, host: str, port: int) -> bool:
        from task_hounds_api.db import connect

        with connect() as db:
            row = db.execute(
                "SELECT 1 FROM opencode_server_instances "
                "WHERE host=? AND port=? AND status='ignored'",
                (host, port),
            ).fetchone()
        return row is not None

    def discover_candidate_ports(
        self,
        host: str = "127.0.0.1",
        start_port: int = 18765,
        end_port: int = 18865,
        extra_ports: list[int] | None = None,
    ) -> dict:
        """Scan a port range for reachable OpenCode servers and
        register newly-discovered ones. Idempotent — a port that is
        already known is returned as 'already_known' rather than
        creating a duplicate row. Ignored ports are reported as
        'ignored' and NOT re-registered.

        Returns {servers, discovered, new_count}.
        """
        from task_hounds_api.opencode.process import is_reachable
        from task_hounds_api.db.ops import runtime as db_rt

        candidates = list(range(start_port, end_port + 1))
        if extra_ports:
            candidates = sorted(set(candidates) | set(extra_ports))

        discovered: list[dict] = []
        new_count = 0
        for port in candidates:
            if not is_reachable(host, port, timeout=0.8):
                continue
            existing = next(
                (
                    s for s in db_rt.list_servers()
                    if s.get("host") == host and s.get("port") == port
                ),
                None,
            )
            if existing:
                if existing.get("status") == "ignored":
                    discovered.append({
                        "host": host, "port": port,
                        "status": "ignored",
                        "instance_id": existing.get("id"),
                    })
                else:
                    discovered.append({
                        "host": host, "port": port,
                        "status": "already_known",
                        "instance_id": existing.get("id"),
                        "owner": existing.get("owner"),
                    })
                continue
            new_id = self.register_external(host, port)
            new_count += 1
            discovered.append({
                "host": host, "port": port,
                "status": "registered",
                "instance_id": new_id,
            })
        return {
            "servers": self.list_servers(),
            "discovered": discovered,
            "new_count": new_count,
        }

    def stop_server(self, instance_id: int) -> str:
        """Stop a server by id. Only managed servers (we started them)
        are actually killed. External rows are reported as
        'skipped_external' and removed from the registry so they
        disappear from list_servers() (without killing a process the
        operator started). Idempotent: returns 'not_found' if no
        such id, 'already_stopped' if the row is already gone."""
        from task_hounds_api.db import connect

        with connect() as db:
            row = db.execute(
                "SELECT id, pid, owner, managed, status FROM opencode_server_instances WHERE id=?",
                (instance_id,),
            ).fetchone()
        if not row:
            return "not_found"
        owner = row["owner"] or ""
        managed = row["managed"]
        pid = row["pid"]
        if owner == "external" or managed in (0, False):
            with connect() as db:
                db.execute(
                    "DELETE FROM opencode_server_instances WHERE id=?",
                    (instance_id,),
                )
                db.commit()
            return "skipped_external"
        if pid and self._managed_lifecycle is not None:
            try:
                proc = getattr(self._managed_lifecycle, "_proc", None)
                if proc is not None and getattr(proc, "pid", None) == pid:
                    self._managed_lifecycle.stop()
            except Exception:
                pass
        with connect() as db:
            db.execute(
                "DELETE FROM opencode_server_instances WHERE id=?",
                (instance_id,),
            )
            db.commit()
        return "stopped"

    def stop_all(self) -> dict:
        """Stop everything owned by the manager. Per-server outcome
        is determined by three distinct cases:

          1. External row (operator-owned): outcome='skipped_external',
             row preserved, ok=True.
          2. Managed row + actual proc handle: outcome='stopped' (proc
             died) or outcome='failed' (proc lingered); row DELETEd
             on success, kept on failure.
          3. Managed row + no proc handle (stale DB row, e.g. the
             managed subprocess was never started or already exited):
             outcome='stale_removed', row DELETEd (it was dead
             already — we just clean up the registry), ok=True.

        Top-level response shape:
          ok:       True if no managed server failed. Case 1 and
                    case 3 both return ok=True (nothing actually
                    failed; case 3 is a registry cleanup, not a
                    server kill).
          stopped:  True ONLY when case 2 succeeded — i.e. a real
                    managed proc was actually killed. False in case 1
                    (external), case 3 (stale row, no proc to kill),
                    and case 2-failure (proc lingered).
          results:  per-server outcome list. UI reloads + shows.
        """
        from task_hounds_api.opencode import registry as oc_registry
        from task_hounds_api.db import connect
        from task_hounds_api.db.ops import runtime as db_rt

        killed_runs = oc_registry.kill_all_runs()

        has_managed_proc = (
            self._managed_lifecycle is not None
            and getattr(self._managed_lifecycle, "_proc", None) is not None
        )
        managed_ok, managed_err = self.stop_managed()

        results: list[dict] = []
        for s in db_rt.list_servers():
            sid = s.get("id")
            owner = s.get("owner") or ""
            managed = s.get("managed")
            host = s.get("host") or ""
            port = s.get("port") or 0
            is_external = owner == "external" or managed in (0, False)
            if is_external:
                results.append({
                    "server_id": f"external-{port}",
                    "instance_id": sid,
                    "ok": True,
                    "error": None,
                    "outcome": "skipped_external",
                    "host": host,
                    "port": port,
                })
                continue
            if not has_managed_proc:
                with connect() as db:
                    db.execute(
                        "DELETE FROM opencode_server_instances WHERE id=?",
                        (sid,),
                    )
                    db.commit()
                results.append({
                    "server_id": f"opencode-serve-{port}",
                    "instance_id": sid,
                    "ok": True,
                    "error": None,
                    "outcome": "stale_removed",
                    "host": host,
                    "port": port,
                })
                continue
            if managed_ok:
                with connect() as db:
                    db.execute(
                        "DELETE FROM opencode_server_instances WHERE id=?",
                        (sid,),
                    )
                    db.commit()
                results.append({
                    "server_id": f"opencode-serve-{port}",
                    "instance_id": sid,
                    "ok": True,
                    "error": None,
                    "outcome": "stopped",
                    "host": host,
                    "port": port,
                })
            else:
                results.append({
                    "server_id": f"opencode-serve-{port}",
                    "instance_id": sid,
                    "ok": False,
                    "error": managed_err,
                    "outcome": "failed",
                    "host": host,
                    "port": port,
                })
        if not results:
            if not has_managed_proc:
                results.append({
                    "server_id": "opencode-serve",
                    "instance_id": None,
                    "ok": True,
                    "error": None,
                    "outcome": "noop",
                })
            else:
                results.append({
                    "server_id": "opencode-serve",
                    "instance_id": None,
                    "ok": managed_ok,
                    "error": managed_err or None,
                    "outcome": "stopped" if managed_ok else "noop",
                })

        if not has_managed_proc:
            top_ok = True
            top_stopped = False
            managed_servers_killed = 0
        else:
            top_ok = bool(managed_ok)
            top_stopped = bool(managed_ok)
            managed_servers_killed = 1 if managed_ok else 0
        return {
            "ok": top_ok,
            "stopped": top_stopped,
            "results": results,
            "killed": {
                "opencode_runs": int(killed_runs),
                "managed_servers": managed_servers_killed,
            },
        }

    def reconcile_servers(self) -> int:
        """Delete server rows whose pid is no longer alive. Returns count removed."""
        from task_hounds_api.db import connect

        with connect() as db:
            rows = db.execute(
                "SELECT id, pid FROM opencode_server_instances WHERE pid IS NOT NULL"
            ).fetchall()
        removed = 0
        for r in rows:
            pid = r["pid"]
            if not pid:
                continue
            try:
                os.kill(pid, 0)
                continue
            except (OSError, ProcessLookupError):
                pass
            with connect() as db:
                db.execute(
                    "DELETE FROM opencode_server_instances WHERE id=?",
                    (r["id"],),
                )
                db.commit()
            removed += 1
        return int(removed)

    def auto_bind_four_roles(self) -> int:
        """Upsert default bindings for manager/worker/reviewer/chat with
        full host/port/agent/model/server_instance_id populated, and
        mirror the model into the corresponding agent_registry row so
        the UI and the executor agree on which model each role uses."""
        from task_hounds_api.db.ops import runtime as db_rt
        from task_hounds_api.db.ops import agent as db_agent
        from task_hounds_api.db import connect

        server_instance_id = self._server_row_id_for_binding()
        roles = ("manager", "worker", "reviewer", "chat")
        agent_name_for_role = {
            "manager": "manager",
            "worker": "worker",
            "reviewer": "reviewer",
            "chat": "chat",
        }
        for role in roles:
            agent = self._default_agent_for_role(role)
            model = self._default_model_for_role(role)
            db_rt.upsert_binding(
                role,
                self._managed_host,
                self._managed_port,
                opencode_agent=agent,
                model=model,
                server_instance_id=server_instance_id,
                binding_source="auto",
            )
            agent_name = agent_name_for_role[role]
            if db_agent.get_agent(agent_name) is not None:
                db_agent.update_agent(agent_name, model=model, opencode_agent=agent)
        return len(roles)

    def _default_agent_for_role(self, role: str) -> str:
        return os.environ.get(f"TASK_HOUNDS_{role.upper()}_OPENCODE_AGENT") or os.environ.get(
            "TASK_HOUNDS_OPENCODE_AGENT", "Sisyphus - ultraworker"
        )

    def _default_model_for_role(self, role: str) -> str:
        return os.environ.get(f"TASK_HOUNDS_{role.upper()}_OPENCODE_MODEL") or os.environ.get(
            "TASK_HOUNDS_OPENCODE_MODEL", "minimax-coding-plan/MiniMax-M2.7"
        )

    def _managed_server_row_id(self) -> int | None:
        from task_hounds_api.db import connect
        with connect() as db:
            row = db.execute(
                "SELECT id FROM opencode_server_instances "
                "WHERE power_teams_session_id='managed' AND host=? AND port=?",
                (self._managed_host, self._managed_port),
            ).fetchone()
        return int(row["id"]) if row else None

    def _server_row_id_for_binding(self) -> int | None:
        """Return the server_instance_id that bindings should reference.
        Prefer the managed server (we own it). Fall back to the most
        recent server row when no managed server exists — typically
        an external server the operator attached. Returning None (so
        bindings have server_instance_id=NULL) leaves the runtime
        status reporting 'servers: empty' while bindings point at
        no one, which is what Issue 3 in the review flagged."""
        managed_id = self._managed_server_row_id()
        if managed_id is not None:
            return managed_id
        from task_hounds_api.db import connect
        with connect() as db:
            row = db.execute(
                "SELECT id FROM opencode_server_instances "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return int(row["id"]) if row else None
