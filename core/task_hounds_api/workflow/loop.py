"""workflow.loop — background loop runner.

Polls the DB for pending directives and runs the workflow graph
in a background thread. Each loop reads the latest DB state and
writes its results back.

Directive lifecycle:
  pending  --(claim_pending_directive)-->  running
  running  --(graph.run_loop success)-->  processed
  running  --(graph.run_loop raises)-->   failed(error=<message>)

The finally block does NOT mark the directive as processed on
exception — the old code did that, which silently swallowed errors.

Loop state machine (Phase 2 fix for the silent-death bug):
  stopped  ->  starting  ->  running  ->  stopped
                   |
                   +-->  failed  ->  starting (on retry)
                                        ->  stopped  (only via stop())

`start()` is now a blocking call that waits for the
ensure_managed_running handshake to complete (success or fail) and
returns a result dict. This prevents the caller (the HTTP
`/api/workflow/start-loop` route) from claiming `started: true`
while the loop thread is actually dying because the OpenCode
binary is missing or unreachable.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import threading
import time

from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.db.ops import project as db_project
from task_hounds_api.opencode import lifecycle as oc_lifecycle
from task_hounds_api.opencode import registry
from task_hounds_api.opencode.runtime_manager import RuntimeManager
from task_hounds_api.workflow import graph, models as M
from task_hounds_api.workflow.signals import clear_runtime_agent_states

logger = logging.getLogger(__name__)

# State constants (also re-exported for the API layer)
STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_FAILED = "failed"

# How long start() will wait for the ensure_managed_running
# handshake to return before giving up. Generous because the opencode
# binary may take a few seconds to spawn + bind.
STARTUP_HANDSHAKE_TIMEOUT_S = 10.0


def _resolve_opencode_port(port: int | None) -> int:
    """Resolve the OpenCode port from explicit arg > env > default."""
    if port is not None:
        return port
    env_port = os.environ.get("TASK_HOUNDS_OPENCODE_PORT")
    return int(env_port) if env_port else 18765


class BackgroundLoop:
    """One background thread per loop. Polls DB for pending directives."""

    def __init__(
        self,
        *,
        interval: int = 60,
        host: str = "127.0.0.1",
        port: int | None = None,
    ):
        self.interval = interval
        self.host = host
        self.port = _resolve_opencode_port(port)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pid: int | None = None
        # Phase 2 state machine + error tracking
        self._state: str = STATE_STOPPED
        self._state_lock = threading.Lock()
        self._startup_done = threading.Event()
        self._last_start_error: str | None = None
        self._last_error_at: str | None = None
        # Generation counter: bumps on every start()/stop(). The
        # background thread captures the value at entry and only
        # transitions to RUNNING if its captured value is still
        # current. A late handshake (after start() timed out, or
        # after stop() was called) finds a stale value and returns
        # without touching shared state — this is the fix for the
        # 12s-handshake-completes-after-10s-timeout race.
        self._generation: int = 0

    def start(self) -> dict:
        """Start the loop, block on the startup handshake, return a
        result dict. NEVER claims `started: true` unless the
        ensure_managed_running handshake actually succeeded.

        Returns a dict with shape:
          ok       : bool   -- handshake completed without error
          started  : bool   -- loop is in `running` state
          running  : bool   -- alias of `started` (legacy field)
          state    : str    -- current loop state
          pid      : int|None -- opencode serve PID if known
          error    : str|None -- last startup error, if any
          reason   : str|None -- human-readable reason for started=false
        """
        with self._state_lock:
            if self._state == STATE_RUNNING and self._thread and self._thread.is_alive():
                return {
                    "ok": True,
                    "started": True,
                    "running": True,
                    "state": STATE_RUNNING,
                    "pid": self._pid,
                    "error": None,
                    "reason": "already running",
                }
            self._stop.clear()
            self._last_start_error = None
            self._last_error_at = None
            self._state = STATE_STARTING
            # Bump generation so any in-flight (late) handshake from
            # the previous thread cannot overwrite this thread's state.
            self._generation += 1
            captured_generation = self._generation
            # Phase-8 (P1 startup race): per-start wakeup event so
            # a stale thread's set() can't wake the new start().
            startup_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                args=(captured_generation, startup_event),
                daemon=True,
                name="workflow-loop",
            )
            self._thread.start()

        timed_out = not startup_event.wait(timeout=STARTUP_HANDSHAKE_TIMEOUT_S)
        if timed_out:
            with self._state_lock:
                self._state = STATE_FAILED
                self._last_start_error = (
                    f"startup handshake did not complete within "
                    f"{STARTUP_HANDSHAKE_TIMEOUT_S}s"
                )
                self._last_error_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
                # Invalidate the slow thread's captured generation so
                # its late handshake refuses to publish RUNNING. Without
                # this bump the race could flip FAILED back to RUNNING.
                self._generation += 1
            logger.error("startup handshake timed out")
            return {
                "ok": False,
                "started": False,
                "running": False,
                "state": STATE_FAILED,
                "pid": None,
                "error": self._last_start_error,
                "reason": "startup_timeout",
            }

        with self._state_lock:
            if self._state == STATE_RUNNING:
                return {
                    "ok": True,
                    "started": True,
                    "running": True,
                    "state": STATE_RUNNING,
                    "pid": self._pid,
                    "error": None,
                    "reason": None,
                }
            return {
                "ok": False,
                "started": False,
                "running": False,
                "state": self._state,
                "pid": None,
                "error": self._last_start_error,
                "reason": self._last_start_error or "startup_failed",
            }

    def get_state(self) -> str:
        """Current loop state: stopped / starting / running / failed."""
        with self._state_lock:
            return self._state

    def get_last_start_error(self) -> str | None:
        with self._state_lock:
            return self._last_start_error

    def get_last_error_at(self) -> str | None:
        with self._state_lock:
            return self._last_error_at

    def get_pid(self) -> int | None:
        """Return the opencode serve PID if known, else None."""
        return self._pid

    def stop(self) -> dict:
        """Stop the loop and request cancellation of any current OpenCode run.

        Returns the new stop semantics shape:
          - stopping: True (we set the stop event to block the next tick)
          - current_run_cancel_requested: True (we asked the OpenCode run to die)
          - current_run_killed: bool (whether there was actually a live run)
        """
        self._stop.set()
        killed = registry.kill_all_runs()
        # Bump generation so any in-flight handshake from a slow
        # start() finds a stale value when it tries to publish RUNNING.
        self._generation += 1
        with self._state_lock:
            self._state = STATE_STOPPED
        return {
            "stopping": True,
            "current_run_cancel_requested": True,
            "current_run_killed": killed > 0,
        }

    def is_running(self) -> bool:
        with self._state_lock:
            return (
                self._state == STATE_RUNNING
                and self._thread is not None
                and self._thread.is_alive()
                and not self._stop.is_set()
            )

    def _run(self, captured_generation: int, startup_event: threading.Event) -> None:
        """Thread body. Performs the startup handshake (signals
        startup_event), then enters the main tick loop. The handshake
        is what makes `start()` reliable — start() blocks on
        startup_event until this function signals it, so a missing
        opencode binary surfaces as started=false instead of a
        silent thread death.

        Generation guard: if `self._generation` has moved past the
        value captured at entry (because a new start() bumped it,
        or stop() bumped it), this thread is stale. It must NOT
        transition state to RUNNING — that would silently overwrite
        the FAILED state that start() already published when its
        10s timeout fired, or the STOPPED state that stop() set.
        A stale thread just signals startup_event so its own
        start()'s wait completes, then returns.

        Per-start event: startup_event is created by start() and
        passed in. Only this thread can signal it. A different
        (newer) start() creates its own event and is not affected
        by this thread's set().
        """
        rm = RuntimeManager.instance()
        try:
            ok = bool(rm.ensure_managed_running())
        except Exception as exc:
            with self._state_lock:
                if self._generation == captured_generation:
                    self._state = STATE_FAILED
                    self._last_start_error = f"startup exception: {exc!r}"
                    self._last_error_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
            logger.exception("cannot start opencode")
            startup_event.set()
            return
        if not ok:
            with self._state_lock:
                if self._generation == captured_generation:
                    self._state = STATE_FAILED
                    self._last_start_error = "opencode not reachable on startup"
                    self._last_error_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
            logger.warning("opencode not reachable; loop exiting")
            startup_event.set()
            return

        # Handshake OK. Refuse to publish RUNNING if we're stale.
        if self._generation != captured_generation:
            logger.info(
                "stale handshake completed (gen=%s, current=%s); discarding",
                captured_generation, self._generation,
            )
            startup_event.set()
            return

        self._pid = (rm.get_managed_health() or {}).get("pid")
        with self._state_lock:
            self._state = STATE_RUNNING
        lc = rm.get_managed_lifecycle()
        startup_event.set()

        while not self._stop.is_set():
            try:
                self._tick(lc)
            except Exception:
                logger.exception("tick error")
            self._stop.wait(self.interval)

    def _tick(self, lc: oc_lifecycle.OpenCodeLifecycle) -> None:
        active = db_project.get_active_session()
        if not active:
            return
        sid = active["id"]
        directive = db_chat.claim_pending_directive(sid)
        if not directive:
            return
        did = directive["id"]
        fi = M.FlowInput(
            power_team_project_id=f"pt_{sid}",
            project_session_id=sid,
            human_directive=directive["directive"],
            workspace_path=active.get("workspace_path", "") or "",
        )
        try:
            result = graph.run_loop(fi)
            _record_directive_lifecycle(did, result)
        except Exception as exc:
            db_chat.mark_directive_status(did, "failed", error=str(exc))
            logger.exception("graph run failed for directive %s", did)
        finally:
            clear_runtime_agent_states()


# ── One-shot entry point ────────────────────────────────────────────────────

def run_once(*, host: str = "127.0.0.1", port: int | None = None) -> dict | None:
    """Run one loop synchronously, picking up the next pending directive."""
    active = db_project.get_active_session()
    if not active:
        return None
    sid = active["id"]
    directive = db_chat.claim_pending_directive(sid)
    if not directive:
        return None
    resolved_port = _resolve_opencode_port(port)
    rm = RuntimeManager.instance()
    rm.ensure_managed_running()
    lc = rm.get_managed_lifecycle() or oc_lifecycle.OpenCodeLifecycle(host, resolved_port)
    did = directive["id"]
    fi = M.FlowInput(
        power_team_project_id=f"pt_{sid}",
        project_session_id=sid,
        human_directive=directive["directive"],
        workspace_path=active.get("workspace_path", "") or "",
    )
    try:
        result = graph.run_loop(fi)
        _record_directive_lifecycle(did, result)
        return result
    except Exception as exc:
        db_chat.mark_directive_status(did, "failed", error=str(exc))
        raise
    finally:
        clear_runtime_agent_states()


def _record_directive_lifecycle(directive_id: int, result: object) -> None:
    """Map graph.run_loop's final state to a directive lifecycle status.

    The graph returns a dict whose top-level 'status' key is whatever
    state.status the last executor node set. Convention:
      "completed"     -> directive is processed (pass)
      "failed"        -> directive is failed
      "needs_review"  -> directive is failed (operator must intervene)
      anything else   -> directive is failed (unknown, safe default)

    A directive that fails must NOT be marked 'processed' — that is
    the silent-failure bug. Without this guard, a Reviewer reject
    (state.status='failed' / 'needs_review') and a successful pass
    are indistinguishable in the directive table.
    """
    if not isinstance(result, dict):
        db_chat.mark_directive_status(directive_id, "failed", error="graph returned non-dict")
        return
    status = str(result.get("status", "") or "").strip().lower()
    if status in {"completed", "pass", "ok", "processed"}:
        db_chat.mark_directive_status(directive_id, "processed")
        return
    if status in {"needs_review"}:
        db_chat.mark_directive_status(
            directive_id, "failed", error="reviewer requested changes"
        )
        return
    if status in {"skipped"}:
        db_chat.mark_directive_status(
            directive_id, "failed", error="step skipped (credentials or missing input)"
        )
        return
    # 'failed' or anything unrecognized
    error = ""
    if status:
        error = f"graph final status={status}"
    db_chat.mark_directive_status(directive_id, "failed", error=error)
