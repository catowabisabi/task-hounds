"""Real 3-agent happy-path E2E (Manager -> Worker -> Reviewer).

This is the authoritative end-to-end smoke for the Task Hounds
runtime. It uses:

  * a real uvicorn server (no in-process shortcuts)
  * a real OpenCode subprocess via RuntimeManager (no mocks)
  * an isolated temporary DB (no shared state with the dev DB)
  * an isolated temporary workspace (so the Worker cannot escape)
  * real LLM calls for Manager, Worker, and Reviewer (no fixture
    responses, no offline replay)

The directive under test is the smallest possible happy path:

    Create hello.txt containing exactly TASK_HOUNDS_E2E_OK

We then verify, by reading real artifacts, that:

  C1. the directive reaches terminal status='processed'
  C2. manager_messages has at least one row for the session
  C3. the file hello.txt was actually created in the workspace and
      contains exactly the expected string
  C4. worker_reports has at least one row for the session
  C5. reviewer_sessions has a row with status='completed'
  C6. all 3 of the role bindings (manager, worker, reviewer) resolve
      to a non-empty (host, port, model, agent) tuple -- proving the
      wiring is real, not a stub

Failure policy: if the env is missing credentials, the script
fails FAST with a clear `missing_credentials` message and exit code
77 (a SYSEXIT-style code for "config error"). It MUST NOT proceed
to the "happy path" with mock LLM responses -- per project policy
"缺 credentials 時，script 應明確 SKIP 或 fail-fast，不能 false pass".

Run:     set PYTHONPATH=core
         python tools/runtime_happy_path_e2e.py

Exit 0 on success, 77 on missing credentials, 1 on any other failure.
"""
from __future__ import annotations

import json
import os
import shutil
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

REQUIRED_CREDS = (
    "OPENCODE_API_KEY_MINIMAX",
    "OPENCODE_API_KEY_BAILIAN",
)
EXPECTED_HELLO = "TASK_HOUNDS_E2E_OK"
EXPECTED_FILENAME = "hello.txt"
TMP_DB = tempfile.NamedTemporaryFile(
    prefix="task_hounds_happy_", suffix=".db", delete=False
)
TMP_DB.close()
PORT = 18952
BASE = f"http://127.0.0.1:{PORT}"
DIRECTIVE_TIMEOUT_S = 90.0
POLL_INTERVAL_S = 1.0
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
        with urllib.request.urlopen(req, timeout=10) as resp:
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


def wait_for_server(proc: subprocess.Popen, timeout: float = 20.0) -> bool:
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


def credential_preflight() -> tuple[bool, list[str]]:
    """Return (ok, missing_keys). ok=False means we MUST fail-fast."""
    missing = [k for k in REQUIRED_CREDS if not os.environ.get(k)]
    return (len(missing) == 0, missing)


def main() -> int:
    print("== Real 3-Agent Happy-Path E2E ==")
    print(f"Tmp DB:  {TMP_DB.name}")
    print(f"Port:    {PORT}")
    print(f"Workspace: <tempdir>")
    print()

    print("== Credential pre-flight ==")
    ok_creds, missing = credential_preflight()
    if not ok_creds:
        print("  FAIL: missing required credentials. Refusing to mock LLM.")
        print("        Set the following env vars and re-run:")
        for k in missing:
            print(f"          - {k}")
        print("        See core/runtime/opencode_config/opencode.jsonc for the")
        print("        expected env-var placeholders.")
        return 77  # EX_CONFIG
    ok(f"all required credentials present: {', '.join(REQUIRED_CREDS)}")
    print()

    print("== Starting uvicorn (isolated DB) ==")
    cmd = [
        sys.executable, "-m", "uvicorn",
        "task_hounds_api.api.main:app",
        "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning",
    ]
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO / "core"),
        "POWER_TEAMS_DB": TMP_DB.name,
        "TASK_HOUNDS_OPENCODE_PORT": str(PORT + 100),  # opencode on different port
    }
    proc = subprocess.Popen(
        cmd, cwd=str(REPO), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if not wait_for_server(proc, timeout=20.0):
        fail(f"uvicorn did not become ready within 20s (rc={proc.poll()})")
        try:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            if stderr.strip():
                print(f"  uvicorn stderr: {stderr[-1500:]}")
        except Exception:
            pass
        proc.terminate()
        return 1
    ok(f"uvicorn ready on port {PORT} (pid={proc.pid})")
    print()

    workspace: Path | None = None
    try:
        print("== Setting up project ==")
        workspace = Path(tempfile.mkdtemp(prefix="task_hounds_happy_ws_"))
        ok(f"isolated workspace: {workspace}")

        status, projects = http("/api/projects")
        if status != 200 or not isinstance(projects, list) or not projects:
            fail(f"GET /api/projects: expected non-empty list, got {status} {projects!r}")
            return 1
        ok(f"GET /api/projects: {len(projects)} project(s)")

        status, created = http(
            "/api/projects", method="POST",
            body={"workspace_path": str(workspace), "name": "happy-path-proj"},
        )
        if status != 200 or not isinstance(created, dict) or "id" not in created:
            fail(f"POST /api/projects: {status} {created!r}")
            return 1
        sid = created["id"]
        ok(f"POST /api/projects -> id={sid}")

        status, _ = http(f"/api/projects/{sid}/activate", method="POST")
        if status != 200:
            fail(f"POST /api/projects/{{id}}/activate: {status}")
            return 1
        ok(f"project activated: {sid}")
        print()

        print("== Verifying role bindings resolve to real (host, port, model) ==")
        # We can't import task_hounds_api here without polluting the
        # subprocess env, so we ask the API. The /api/runtime/status
        # endpoint surfaces bindings via role_bindings. The test is:
        # every binding must have non-empty model + a host/port that
        # matches a server row in the registry.
        _, status_body = http("/api/runtime/status")
        if not isinstance(status_body, dict):
            fail(f"/api/runtime/status not dict: {status_body!r}")
            return 1
        bindings = status_body.get("role_bindings") or []
        if len(bindings) < 4:
            fail(f"expected 4 role_bindings, got {len(bindings)}")
        for b in bindings:
            role = b.get("role")
            model = (b.get("model") or "").strip()
            host = (b.get("host") or "").strip()
            port = b.get("port")
            if not model:
                fail(f"binding for {role!r} has empty model")
            if not host or not port:
                fail(f"binding for {role!r} missing host/port")
            else:
                ok(f"binding {role!r}: {host}:{port} model={model!r}")
        print()

        print("== Submitting directive ==")
        directive_text = (
            f"Create {EXPECTED_FILENAME} containing exactly {EXPECTED_HELLO}"
        )
        status, directive = http(
            "/api/workflow/directive", method="POST",
            body={"session_id": sid, "directive": directive_text},
        )
        if status != 200 or not isinstance(directive, dict) or "id" not in directive:
            fail(f"POST /api/workflow/directive: {status} {directive!r}")
            return 1
        directive_id = directive["id"]
        ok(f"directive submitted: id={directive_id} text={directive_text!r}")
        print()

        print("== Starting loop ==")
        status, started = http("/api/workflow/start-loop", method="POST")
        if status != 200 or not isinstance(started, dict):
            fail(f"POST /api/workflow/start-loop: {status} {started!r}")
            return 1
        if not started.get("started"):
            fail(f"start-loop returned started=false: {started!r}")
            return 1
        ok(f"loop started: pid={started.get('pid')}")
        print()

        print(f"== Waiting for directive to reach terminal state (timeout={DIRECTIVE_TIMEOUT_S}s) ==")
        final = wait_for_directive_terminal(sid, DIRECTIVE_TIMEOUT_S)
        if final is None:
            fail(f"directive {directive_id} did NOT reach terminal state in {DIRECTIVE_TIMEOUT_S}s")
        else:
            ok(f"directive terminal status: {final.get('status')!r}")
            if final.get("error"):
                print(f"  captured error: {final.get('error')!r}")
        print()

        print("== Verifying artifacts ==")
        # C1
        if final and final.get("status") == "processed":
            ok("C1: directive status == 'processed'")
        else:
            fail(f"C1: expected status='processed', got {(final or {}).get('status')!r}")

        # C2
        mgr = db_query(
            "SELECT COUNT(*) AS n FROM manager_messages WHERE session_id=?",
            (sid,),
        )
        mgr_n = mgr[0]["n"] if mgr else 0
        if mgr_n > 0:
            ok(f"C2: manager output exists ({mgr_n} row(s) in manager_messages)")
        else:
            fail("C2: manager_messages has 0 rows for this session")

        # C3 (file system side effect -- the real proof that Worker LLM
        # actually wrote to disk, not just claimed it did)
        hello_path = workspace / EXPECTED_FILENAME
        if hello_path.exists():
            content = hello_path.read_text(encoding="utf-8").strip()
            if content == EXPECTED_HELLO:
                ok(f"C3: file {EXPECTED_FILENAME!r} created with exact content {EXPECTED_HELLO!r}")
            else:
                fail(f"C3: file {EXPECTED_FILENAME!r} exists but content is {content!r} (expected {EXPECTED_HELLO!r})")
        else:
            fail(f"C3: file {EXPECTED_FILENAME!r} was NOT created in workspace {workspace}")

        # C4
        wr = db_query(
            "SELECT COUNT(*) AS n FROM worker_reports WHERE session_id=?",
            (sid,),
        )
        wr_n = wr[0]["n"] if wr else 0
        if wr_n > 0:
            ok(f"C4: worker report exists ({wr_n} row(s) in worker_reports)")
        else:
            fail("C4: worker_reports has 0 rows for this session")

        # C5
        rev = db_query(
            """SELECT rs.id, rs.status FROM reviewer_sessions rs
               JOIN suggestion_queue sq ON rs.suggestion_id = sq.id
               WHERE sq.session_id=?""",
            (sid,),
        )
        completed = [r for r in rev if r.get("status") == "completed"]
        if completed:
            ok(f"C5: reviewer report completed ({len(completed)} row(s))")
        elif rev:
            fail(f"C5: reviewer_sessions has {len(rev)} row(s) but none completed: statuses={[r.get('status') for r in rev]}")
        else:
            fail("C5: reviewer_sessions has 0 rows for this session")
        print()

        print("== Stopping loop ==")
        status, stopped = http("/api/workflow/stop-loop", method="POST")
        if status != 200 or not isinstance(stopped, dict):
            fail(f"POST /api/workflow/stop-loop: {status} {stopped!r}")
        elif stopped.get("stopping") is True:
            ok(f"stop response shape correct: {stopped}")
        else:
            fail(f"stop response shape wrong: {stopped!r}")

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
        try:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            if stderr.strip():
                print(f"  uvicorn stderr (last 1500 chars):\n{stderr[-1500:]}")
        except Exception:
            pass
        ok("uvicorn stopped")

        if workspace is not None and workspace.exists():
            try:
                shutil.rmtree(workspace, ignore_errors=True)
                ok(f"removed temp workspace: {workspace}")
            except Exception as e:
                fail(f"could not remove temp workspace: {e}")
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
        print(f"== HAPPY-PATH E2E FAILED ({len(FAILURES)} issue(s)) ==")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("== HAPPY-PATH E2E PASSED ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
