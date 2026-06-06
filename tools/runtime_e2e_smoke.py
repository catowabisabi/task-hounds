"""Runtime reliability E2E smoke.

Starts a real uvicorn server on port 18951, submits a directive, starts
the loop, then WAITS for the directive to reach a terminal state and
verifies all four success conditions. Reports FAIL on any deviation.

Verified conditions (all must hold for E2E PASS):
  C1. directive status == 'processed'
  C2. manager output exists (manager_messages has rows for the session)
  C3. worker report exists (worker_reports has rows for the session)
  C4. reviewer report exists (reviewer_sessions has rows for the session
      with status='completed')

When credentials are missing or the opencode subprocess crashes, the
directive lands in status='failed' (not 'processed') and this script
reports E2E FAILED with the captured error. The previous version
reported PASSED even when Manager crashed with exit code 1, which
masked real regressions.

Run:   set PYTHONPATH=core
        python tools/runtime_e2e_smoke.py

Exit 0 on success, 1 on any failure.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYTHONPATH", str(REPO / "core"))

TMP_DB = tempfile.NamedTemporaryFile(
    prefix="task_hounds_e2e_", suffix=".db", delete=False
)
TMP_DB.close()
os.environ["POWER_TEAMS_DB"] = TMP_DB.name

PORT = 18951
BASE = f"http://127.0.0.1:{PORT}"
DIRECTIVE_TIMEOUT_S = 30.0
POLL_INTERVAL_S = 0.5
FAILURES: list[str] = []


def fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"  FAIL: {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"  OK:   {msg}", flush=True)


def http(path: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict | str]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, f"EXC: {e!r}"


def wait_for_server(proc: subprocess.Popen, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def wait_for_directive_terminal(session_id: str, timeout: float) -> dict | None:
    """Poll /api/workflow/directives until the latest directive reaches
    a terminal state ('processed' or 'failed') or the timeout expires.
    Returns the directive row, or None on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, directives = http(f"/api/workflow/directives?session_id={session_id}")
        if status == 200 and isinstance(directives, list) and directives:
            latest = directives[0]
            if latest.get("status") in ("processed", "failed"):
                return latest
        time.sleep(POLL_INTERVAL_S)
    return None


def db_query(sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(TMP_DB.name)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def main() -> int:
    print(f"== Runtime Reliability E2E ==")
    print(f"Tmp DB: {TMP_DB.name}")
    print(f"Port:   {PORT}")
    print()

    print("== Starting uvicorn ==")
    cmd = [
        sys.executable, "-m", "uvicorn",
        "task_hounds_api.api.main:app",
        "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning",
    ]
    env = {**os.environ, "PYTHONPATH": str(REPO / "core")}
    proc = subprocess.Popen(
        cmd, cwd=str(REPO), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if not wait_for_server(proc, timeout=15.0):
        fail("uvicorn did not become ready within 15s")
        proc.terminate()
        return 1
    ok(f"uvicorn ready on port {PORT} (pid={proc.pid})")

    tmp_dir: Path | None = None
    try:
        print()
        print("== Runtime health (credential surface) ==")
        _, health = http("/api/runtime/status")
        if not isinstance(health, dict):
            fail(f"GET /api/runtime/status: expected dict, got {health!r}")
        else:
            warnings = health.get("managed_health", {}).get("credential_warnings") or []
            if warnings:
                for w in warnings:
                    print(f"  WARN: {w}")
            else:
                ok("runtime health: all credentials present")
            if "runtime_available" not in health:
                fail("runtime_available field missing from /api/runtime/status")
            elif not health.get("runtime_available"):
                reason = health.get("unavailable_reason") or ""
                if not reason:
                    fail("runtime_available=False but unavailable_reason empty")
                else:
                    print(f"  runtime unavailable: {reason}")

        print()
        print("== E1: full directive lifecycle ==")

        status, projects = http("/api/projects")
        if status != 200 or not isinstance(projects, list) or not projects:
            fail(f"GET /api/projects: expected non-empty list, got {status} {projects!r}")
        else:
            ok(f"GET /api/projects: {len(projects)} project(s)")

        tmp_dir = Path(tempfile.mkdtemp(prefix="task_hounds_e2e_proj_"))
        status, created = http(
            "/api/projects", method="POST",
            body={"workspace_path": str(tmp_dir), "name": "e2e-proj"},
        )
        if status != 200 or not isinstance(created, dict) or "id" not in created:
            fail(f"POST /api/projects: expected 200 with id, got {status} {created!r}")
            return 1
        new_id = created["id"]
        ok(f"POST /api/projects -> id={new_id}")

        status, _ = http(f"/api/projects/{new_id}/activate", method="POST")
        if status != 200:
            fail(f"POST /api/projects/{{id}}/activate: {status}")
            return 1
        ok(f"project activated: {new_id}")

        status, directive = http(
            "/api/workflow/directive", method="POST",
            body={"session_id": new_id, "directive": "E2E smoke test directive"},
        )
        if status != 200 or not isinstance(directive, dict) or "id" not in directive:
            fail(f"POST /api/workflow/directive: {status} {directive!r}")
            return 1
        directive_id = directive["id"]
        ok(f"directive submitted: id={directive_id}")

        status, started = http("/api/workflow/start-loop", method="POST")
        if status != 200 or not isinstance(started, dict) or not started.get("started"):
            fail(f"POST /api/workflow/start-loop: {status} {started!r}")
        else:
            ok(f"loop started: pid={started.get('pid')}")

        print(f"  waiting for directive to reach terminal state (timeout={DIRECTIVE_TIMEOUT_S}s)...")
        final_directive = wait_for_directive_terminal(new_id, DIRECTIVE_TIMEOUT_S)
        if final_directive is None:
            fail(f"directive {directive_id} did NOT reach terminal state within {DIRECTIVE_TIMEOUT_S}s")
        else:
            ok(f"directive terminal status: {final_directive.get('status')!r}")
            if final_directive.get("error"):
                print(f"  captured error: {final_directive.get('error')!r}")

        print()
        print("== E2: 4-condition success check ==")

        if final_directive is not None:
            if final_directive.get("status") == "processed":
                ok("C1: directive status == 'processed'")
            else:
                fail(f"C1: expected status='processed', got {final_directive.get('status')!r}")
        else:
            fail("C1: directive did not reach a terminal state")

        mgr_rows = db_query(
            "SELECT COUNT(*) AS n FROM manager_messages WHERE session_id=?",
            (new_id,),
        )
        mgr_count = mgr_rows[0]["n"] if mgr_rows else 0
        if mgr_count > 0:
            ok(f"C2: manager output exists ({mgr_count} message(s) in manager_messages)")
        else:
            fail("C2: manager output missing — manager_messages has 0 rows for this session")

        wr_rows = db_query(
            "SELECT COUNT(*) AS n FROM worker_reports WHERE session_id=?",
            (new_id,),
        )
        wr_count = wr_rows[0]["n"] if wr_rows else 0
        if wr_count > 0:
            ok(f"C3: worker report exists ({wr_count} report(s) in worker_reports)")
        else:
            fail("C3: worker report missing — worker_reports has 0 rows for this session")

        rev_rows = db_query(
            """SELECT rs.id, rs.status, rs.completed_at
               FROM reviewer_sessions rs
               JOIN suggestion_queue sq ON rs.suggestion_id = sq.id
               WHERE sq.session_id=?""",
            (new_id,),
        )
        completed = [r for r in rev_rows if r.get("status") == "completed"]
        if completed:
            ok(f"C4: reviewer report exists ({len(completed)} completed session(s) in reviewer_sessions)")
        elif rev_rows:
            fail(f"C4: reviewer_sessions has {len(rev_rows)} row(s) but none completed: statuses={[r.get('status') for r in rev_rows]}")
        else:
            fail("C4: reviewer report missing — reviewer_sessions has 0 rows for this session")

        print()
        print("== E3: stop-loop returns new shape ==")
        status, stopped = http("/api/workflow/stop-loop", method="POST")
        if status != 200 or not isinstance(stopped, dict):
            fail(f"POST /api/workflow/stop-loop: {status} {stopped!r}")
        else:
            for key in ("stopping", "current_run_cancel_requested", "current_run_killed"):
                if key not in stopped:
                    fail(f"stop response missing key {key!r}")
            if stopped.get("stopping") is True and stopped.get("current_run_cancel_requested") is True:
                ok(f"stop response shape correct: {stopped}")
            else:
                fail(f"stop response shape wrong: {stopped!r}")

        time.sleep(1.0)
        status, post = http("/api/workflow/status")
        if status == 200:
            ok(f"loop_running after stop = {post.get('loop_running')!r}")

    finally:
        print()
        print("== Teardown ==")
        if proc.poll() is None:
            print(f"  killing uvicorn pid={proc.pid}")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        ok("uvicorn stopped")

        try:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            if stderr.strip():
                print(f"  uvicorn stderr (last 800 chars): {stderr[-800:]}")
        except Exception:
            pass

        if tmp_dir is not None:
            try:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
                ok(f"removed temp project dir: {tmp_dir}")
            except Exception as e:
                fail(f"could not remove temp dir: {e}")
        try:
            os.unlink(TMP_DB.name)
            for suf in ("-wal", "-shm"):
                p = TMP_DB.name + suf
                if os.path.exists(p):
                    os.unlink(p)
            ok(f"removed temp db: {TMP_DB.name}")
        except Exception as e:
            fail(f"could not remove temp db: {e}")

    print()
    if FAILURES:
        print(f"== E2E FAILED ({len(FAILURES)} issue(s)) ==")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("== E2E PASSED ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
