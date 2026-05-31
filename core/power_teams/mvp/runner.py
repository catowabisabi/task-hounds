"""
runner.py - Thin orchestration entry point.

All agent logic lives in power_teams/agents/:
  base.py     - shared utilities, send_to_agent, handoff helpers
  manager.py  - manager_cycle
  worker.py   - worker_cycle
  reviewer.py - run_reviewer_session, _trigger_reviewer_async

This file contains only run_loop() and main().
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time

from power_teams.agents.base import (
    ROOT,
    DB_PATH,
    _get_active_suggestion,
    check_tmux_idle,
    get_active_session_id,
    get_settings,
    init_runtime_files,
    log,
    read_text,
    update_suggestion,
    user_input_path,
)
from power_teams.agents.manager import manager_cycle
from power_teams.agents.worker import worker_cycle
from power_teams.db import (
    connect,
    get_agent,
    get_latest_user_directive,
    init_db,
    seed_default_agents,
)


def _todo_stop_state() -> tuple[bool, str]:
    sid = get_active_session_id()
    if not sid:
        return False, ""
    try:
        with connect() as db:
            rows = db.execute(
                """SELECT id, content, status, position
                   FROM session_todos
                  WHERE session_id=?
                  ORDER BY parent_id IS NOT NULL, parent_id, position, id""",
                (sid,),
            ).fetchall()
        if not rows:
            return False, ""
        signature = "|".join(
            f"{row['id']}:{row['content']}:{row['status']}:{row['position']}"
            for row in rows
        )
        all_done = all(row["status"] == "completed" for row in rows)
        return all_done, signature
    except Exception as exc:
        log(f"todo stop-state check failed: {exc}")
        return False, ""


def _opencode_reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _get_latest_manager_message() -> str | None:
    """Return the most recent manager message content, or None."""
    from power_teams.agents.base import _list_manager_messages

    msgs = _list_manager_messages()
    if msgs:
        row = msgs[0]
        return row["content"] if "content" in row.keys() else ""
    return None


def _ensure_shared_opencode_server(supervisor, ping_interval: float = 300.0) -> None:
    def current_endpoint() -> tuple[str, int]:
        with connect() as db:
            row = db.execute(
                """
                SELECT host, port
                  FROM agent_runtime_bindings
                 WHERE role='manager'
                 LIMIT 1
                """
            ).fetchone()
            if row is None:
                row = db.execute(
                    "SELECT host, port FROM agent_registry WHERE name='manager'"
                ).fetchone()
        host = (row["host"] if row else supervisor.host) or supervisor.host
        port = int((row["port"] if row else supervisor.manager_port) or supervisor.manager_port)
        return host, port

    host, port = current_endpoint()
    if _opencode_reachable(host, port):
        return

    log(f"OpenCode shared server not reachable on {host}:{port} - reconciling lifecycle")
    try:
        from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager

        result = OpenCodeLifecycleManager(db_path=DB_PATH).reconcile_runtime(
            start_if_missing=True,
            restart_unowned=False,
        )
        log(f"OpenCode lifecycle reconcile result: {result}")
    except Exception as exc:
        log(f"Error reconciling shared OpenCode server: {exc}")
        return

    host, port = current_endpoint()
    if not _opencode_reachable(host, port):
        log(f"CRITICAL: OpenCode shared server still not reachable after start attempt on {host}:{port}")


def run_loop(
    once: bool,
    manager_interval: int,
    worker_poll_interval: int,
    auto_release: bool = False,
) -> None:
    init_db(DB_PATH)
    seed_default_agents(DB_PATH)
    init_runtime_files()

    # Guard: require a directive before starting the loop
    current_sid = get_active_session_id()
    pending_directive = get_latest_user_directive(current_sid, status="pending", path=DB_PATH) if current_sid else None
    directive = pending_directive["directive"] if pending_directive else read_text(user_input_path())
    if not directive.strip():
        log("No directive — loop locked")
        return

    last_manager = 0.0

    from power_teams.runtime.opencode_supervisor import OpenCodeSupervisor

    supervisor = OpenCodeSupervisor(cwd=ROOT, startup_timeout=90)
    opencode_checked = False
    last_opencode_ping = 0.0
    last_session_id: str | None = None
    completed_todo_signature: str | None = None
    completed_todo_stable_ticks = 0

    while True:
        now = time.monotonic()
        current_sid = get_active_session_id()

        if not opencode_checked:
            _ensure_shared_opencode_server(supervisor)
            opencode_checked = True
            last_opencode_ping = now

        if now - last_opencode_ping >= 300.0:
            _ensure_shared_opencode_server(supervisor)
            last_opencode_ping = now

        if current_sid != last_session_id:
            log(
                f"Session changed: {last_session_id} -> {current_sid}; "
                "keeping shared OpenCode server running"
            )
            last_session_id = current_sid

        suggestion = _get_active_suggestion()
        worker_done_pending = suggestion and suggestion["status"] == "worker_done"

        if once or worker_done_pending or now - last_manager >= manager_interval:
            try:
                manager_cycle()
            except Exception as exc:
                log(f"ERROR in manager_cycle: {exc}")
            last_manager = time.monotonic()

            new_suggestion = _get_active_suggestion()
            if new_suggestion is None or new_suggestion["status"] not in ("pending", "released"):
                latest_msg = _get_latest_manager_message()
                if latest_msg and ("DIRECTIVE_COMPLETE" in latest_msg or "TASK_HOUNDS_STOP_LOOP" in latest_msg):
                    log("Manager stop signal received - stopping loop.")
                    return

        all_done, todo_signature = _todo_stop_state()
        if all_done:
            if todo_signature == completed_todo_signature:
                completed_todo_stable_ticks += 1
            else:
                completed_todo_signature = todo_signature
                completed_todo_stable_ticks = 1
            if completed_todo_stable_ticks >= 4:
                log("All todos completed and unchanged for 3 extra loop ticks - stopping loop.")
                return
        else:
            completed_todo_signature = None
            completed_todo_stable_ticks = 0

        settings_now = get_settings()
        ar_enabled = auto_release or settings_now.get("auto_release", True)
        if ar_enabled:
            suggestion = _get_active_suggestion()
            if suggestion and suggestion["status"] == "pending":
                update_suggestion(suggestion["id"], status="released")
                log(f"[auto-release] suggestion #{suggestion['id']} released -> worker")

        suggestion = _get_active_suggestion()
        if suggestion and suggestion["status"] == "released":
            try:
                worker_status = dict(get_agent("worker") or {}).get("state") or "idle"
            except Exception:
                worker_status = "idle"
            tmux_idle_ok = os.environ.get("POWER_TEAMS_USE_TMUX_IDLE", "").lower() in {
                "1",
                "true",
                "yes",
            }
            if tmux_idle_ok:
                idle_result = check_tmux_idle()
                if idle_result is None:
                    log("run_loop: TMUX unavailable, proceeding with time-based check")
                elif idle_result:
                    log("run_loop: TMUX idle confirmed, proceeding to worker_cycle")
                else:
                    log("run_loop: TMUX pane shows busy, skipping")
                    time.sleep(worker_poll_interval)
                    continue
            if worker_status == "idle":
                try:
                    worker_cycle()
                    last_manager = 0.0
                except Exception as exc:
                    log(f"ERROR in worker_cycle: {exc}")

        if once:
            return
        time.sleep(worker_poll_interval)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Task Hounds manager/worker runner")
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Create DB tables and seed default agents, then exit",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one manager + worker cycle then exit",
    )
    parser.add_argument(
        "--manager-interval",
        type=int,
        default=600,
        help="Seconds between automatic manager checks (default 600)",
    )
    parser.add_argument(
        "--worker-poll",
        type=int,
        default=10,
        help="Seconds between worker poll checks (default 10)",
    )
    parser.add_argument(
        "--auto-release",
        action="store_true",
        help="Auto-release pending suggestions (for testing without human approval)",
    )
    args = parser.parse_args(argv)

    if args.init_db:
        init_db(DB_PATH)
        seed_default_agents(DB_PATH)
        print("DB initialised.")
        return 0

    run_loop(
        once=args.once,
        manager_interval=args.manager_interval,
        worker_poll_interval=args.worker_poll,
        auto_release=args.auto_release,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
