"""opencode.client — HTTP client to the OpenCode serve endpoint.

Methods:
  health(host, port) -> bool
  list_agents(host, port) -> list[dict]
  run(host, port, *, agent, model, prompt, session_id) -> JsonResult

Uses urllib (stdlib, no extra deps). Streams JSON events from
`opencode run --attach` and assembles the final text.
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from task_hounds_api.opencode import result as rs
from task_hounds_api.opencode import registry
from task_hounds_api.opencode.binary import find
from task_hounds_api.opencode.config import model_supports_thinking
from task_hounds_api.opencode.process import _isolated_env, is_reachable, wait_for_ready


def health(host: str, port: int, timeout: float = 2.0) -> bool:
    return is_reachable(host, port, timeout)


def list_agents(host: str, port: int, timeout: float = 4.0) -> list[dict]:
    """GET /agent. Returns the list of opencode agent dicts."""
    import urllib.request as urlreq
    try:
        url = f"http://{host}:{port}/agent"
        req = urlreq.Request(url, headers={"Accept": "application/json"})
        with urlreq.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return []
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list):
                return [a for a in data if isinstance(a, dict)]
            return []
    except Exception:
        return []


def precreate_session(host: str, port: int) -> str | None:
    """POST /session with empty body. Returns the new session id, or None on failure."""
    import urllib.request as urlreq
    try:
        req = urlreq.Request(
            f"http://{host}:{port}/session",
            data=json.dumps({}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlreq.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode()).get("id")
    except Exception:
        return None


def run(
    *,
    agent: str,
    prompt: str,
    host: str = "127.0.0.1",
    port: int = 18765,
    model: str | None = None,
    session_id: str | None = None,
    on_chunk: Callable[[str], None] | None = None,
    timeout: int = 300,
    cwd: str | Path | None = None,
) -> dict:
    """Send a prompt to the manager/worker/reviewer agent. Returns a JsonResult.

    The call:
      1. Ensures the server is up
      2. Pre-creates a session if none given
      3. Spawns `opencode run --attach --format json`
      4. Streams text/reasoning/tool_use/error events
      5. Returns the assembled text

    The `timeout` argument bounds the total runtime. If exceeded, the
    subprocess is killed and a TimeoutError JsonResult is returned.
    """
    if not is_reachable(host, port):
        return rs.err(
            agent=agent,
            error_type="ConnectionError",
            message=f"opencode serve not reachable on {host}:{port}",
            retryable=True,
        )

    run_id = rs.new_run_id()

    if not session_id:
        session_id = precreate_session(host, port)

    binary = find(required=True)
    cmd = _build_cmd(binary, host, port, agent, model, session_id)
    try:
        text = _run_cmd(cmd, prompt, run_id, on_chunk, timeout=timeout, cwd=cwd)
        return rs.ok(agent=agent, run_id=run_id, status=rs.STATUS_COMPLETED, text=text)
    except Exception as exc:
        raw = str(exc)
        retryable = "balance" not in raw.lower() and "unauthorized" not in raw.lower()
        return rs.err(
            agent=agent,
            run_id=run_id,
            error_type=type(exc).__name__,
            message=str(exc),
            retryable=retryable,
            raw=raw,
        )


def _build_cmd(binary, host, port, agent, model, session_id) -> list[str]:
    cmd = [
        str(binary), "run",
        "--attach", f"http://{host}:{port}",
        "--format", "json",
        "--dangerously-skip-permissions",
    ]
    if model_supports_thinking(model or ""):
        cmd.append("--thinking")
    if agent and agent.lower() not in {"default", "general"}:
        cmd += ["--agent", agent]
    if model:
        cmd += ["--model", model]
    if session_id:
        cmd += ["--session", session_id]
    return cmd


def _kill_proc(proc: subprocess.Popen) -> None:
    """Best-effort kill that closes the stdout pipe AND kills the process tree.

    Always calls proc.terminate() first (soft kill) so the
    HangingStream / stdout pipe closes — the reader thread in
    _run_cmd sees EOF and the main thread's queue.get() unblocks.
    Then delegates to registry.kill_process_tree() for the actual
    process-tree kill (taskkill /T /F on Windows, proc.kill() elsewhere).
    """
    try:
        proc.terminate()
    except (ProcessLookupError, OSError):
        pass
    from task_hounds_api.opencode.registry import kill_process_tree
    kill_process_tree(proc)


def _process_line(raw: str, text_parts: list[str], on_chunk) -> None:
    raw = raw.rstrip("\n")
    if not raw:
        return
    try:
        ev = json.loads(raw)
    except json.JSONDecodeError:
        return
    etype = ev.get("type", "")
    if etype == "text":
        txt = (ev.get("part") or {}).get("text", "").strip()
        if txt:
            text_parts.append(txt)
            if on_chunk:
                on_chunk(txt)
    elif etype == "reasoning":
        txt = (ev.get("part") or {}).get("text", "").strip()
        if txt:
            print(f"[think] {txt[:200]}", flush=True)
    elif etype == "tool_use":
        part = ev.get("part") or {}
        tool_name = part.get("tool", "?")
        state = part.get("state") or {}
        detail = state.get("output") or str(state.get("input") or "")[:120]
        print(f"[tool: {tool_name}] {str(detail)[:120]}", flush=True)
    elif etype == "error":
        err_msg = str(ev.get("error", "unknown error"))
        print(f"[error] {err_msg}", flush=True)


def _run_cmd(
    cmd: list[str],
    prompt: str,
    run_id: str,
    on_chunk,
    timeout: int | None = None,
    cwd: str | Path | None = None,
) -> str:
    """Spawn `opencode run` and stream stdout, enforcing an optional timeout.

    Implementation note: reading from proc.stdout blocks until the OS
    pipe has data — there is no per-line timeout API on Python file
    objects, and `select.select` does not work on Windows pipes. So
    we drain stdout on a reader thread into a queue, and the main
    thread polls the queue with a short interval so it can check
    elapsed time and kill the subprocess on timeout.
    """
    text_parts: list[str] = []
    error_parts: list[str] = []
    start = time.monotonic()
    running = [True]

    def _heartbeat():
        prev = 0
        while running[0]:
            elapsed = int(time.monotonic() - start)
            if elapsed >= prev + 30:
                prev = elapsed
                print(f"[{run_id}] {elapsed}s elapsed", flush=True)
            time.sleep(5)

    threading.Thread(target=_heartbeat, daemon=True).start()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_isolated_env(),
        cwd=str(cwd) if cwd else None,
    )
    registry.register_run(run_id, proc)
    proc.stdin.write(prompt)
    proc.stdin.close()

    line_queue: queue.Queue = queue.Queue()
    _EOF = object()

    def _reader():
        try:
            for line in proc.stdout:
                line_queue.put(line)
        except Exception:
            pass
        finally:
            line_queue.put(_EOF)

    reader = threading.Thread(target=_reader, daemon=True, name=f"oc-reader-{run_id}")
    reader.start()

    try:
        poll_interval = 0.05
        while True:
            if timeout is not None and (time.monotonic() - start) >= timeout:
                _kill_proc(proc)
                raise TimeoutError(
                    f"opencode run timed out after {timeout}s (run_id={run_id})"
                )
            try:
                line = line_queue.get(timeout=poll_interval)
            except queue.Empty:
                continue
            if line is _EOF:
                break
            _process_line(line, text_parts, on_chunk)
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                ev = {}
            if ev.get("type") == "error":
                error_parts.append(json.dumps(ev.get("error", ev), ensure_ascii=False))

        proc.wait()
        running[0] = False

        if proc.returncode != 0:
            stderr_out = proc.stderr.read()
            raise RuntimeError(f"opencode run exited {proc.returncode}: {stderr_out[:300]}")
        if error_parts:
            raise RuntimeError(f"opencode stream error: {error_parts[-1][:500]}")

        return "\n".join(text_parts).strip()
    finally:
        running[0] = False
        registry.unregister_run(run_id)
        reader.join(timeout=2)
