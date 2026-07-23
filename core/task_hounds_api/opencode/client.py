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
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from task_hounds_api.db import ROOT
from task_hounds_api.opencode import result as rs
from task_hounds_api.opencode import registry
from task_hounds_api.opencode.binary import find
from task_hounds_api.opencode.config import thinking_enabled
from task_hounds_api.opencode.process import _isolated_env, is_reachable, wait_for_ready
from task_hounds_api.opencode.log_rotation import rotate_if_needed

_EMIT_LOG_LOCK = threading.Lock()


def _safe_print(message: str) -> None:
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        try:
            stream = getattr(sys.stdout, "buffer", None)
            if stream is not None:
                encoding = sys.stdout.encoding or "utf-8"
                stream.write((message + "\n").encode(encoding, errors="backslashreplace"))
                stream.flush()
                return
        except Exception:
            pass


def _emit_log_path() -> Path:
    explicit = os.environ.get("TASK_HOUNDS_OPENCODE_EMIT_LOG")
    if explicit:
        return Path(explicit)
    return ROOT / "core" / "runtime" / "logs" / "opencode" / "emit.log"


def _redact(value: Any, key: str = "", depth: int = 0) -> Any:
    if depth > 8:
        return "[max-depth]"
    sensitive = any(
        token in key.lower()
        for token in ("key", "token", "secret", "password", "credential", "authorization")
    )
    if isinstance(value, str):
        if sensitive:
            if not value:
                return ""
            return {
                "redacted": True,
                "length": len(value),
                "prefix": value[:6],
                "suffix": value[-4:] if len(value) >= 4 else "",
            }
        return value
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k), depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, key, depth + 1) for item in value]
    return value


def _env_snapshot(env: dict[str, str]) -> dict[str, Any]:
    keys = [
        "OPENCODE_API_KEY_MINIMAX",
        "OPENCODE_API_KEY_BAILIAN",
        "MINIMAX_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_CONTENT",
        "OPENCODE_DISABLE_PROJECT_CONFIG",
        "POWER_TEAMS_RUNTIME_DIR",
        "TASK_HOUNDS_PORT",
    ]
    return {key: _redact(env.get(key, ""), key) for key in keys if key in env}


def _emit_log(event: str, data: dict[str, Any] | None = None) -> None:
    """Append one JSONL event for traffic between Task Hounds and opencode run.

    This intentionally lives at the OpenCode boundary so a future 4xx/stream
    error can be traced without guessing which higher-level route triggered it.
    """
    path = _emit_log_path()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "data": _redact(data or {}),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _EMIT_LOG_LOCK:
            rotate_if_needed(path, incoming_bytes=len(line.encode("utf-8")) + 1)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
    except Exception:
        # Debug logging must never break agent execution.
        pass


def health(host: str, port: int, timeout: float = 2.0) -> bool:
    return is_reachable(host, port, timeout)


def list_agents(host: str, port: int, timeout: float = 4.0) -> list[dict]:
    """GET /agent. Returns the list of opencode agent dicts."""
    import urllib.request as urlreq
    url = f"http://{host}:{port}/agent"
    _emit_log("opencode.serve.request", {
        "method": "GET",
        "url": url,
        "headers": {"Accept": "application/json"},
        "timeout": timeout,
    })
    try:
        req = urlreq.Request(url, headers={"Accept": "application/json"})
        with urlreq.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            _emit_log("opencode.serve.response", {
                "method": "GET",
                "url": url,
                "status": resp.status,
                "body": raw,
            })
            if resp.status != 200:
                return []
            data = json.loads(raw)
            if isinstance(data, list):
                return [a for a in data if isinstance(a, dict)]
            return []
    except Exception as exc:
        _emit_log("opencode.serve.exception", {
            "method": "GET",
            "url": url,
            "error_type": type(exc).__name__,
            "message": str(exc),
        })
        return []


def precreate_session(host: str, port: int, cwd: str | Path | None = None) -> str | None:
    """POST /session with empty body. Returns the new session id, or None on failure."""
    import urllib.request as urlreq
    url = f"http://{host}:{port}/session"
    body = {"directory": str(cwd)} if cwd else {}
    _emit_log("opencode.serve.request", {
        "method": "POST",
        "url": url,
        "headers": {"Content-Type": "application/json"},
        "body": body,
    })
    try:
        req = urlreq.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlreq.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            _emit_log("opencode.serve.response", {
                "method": "POST",
                "url": url,
                "status": resp.status,
                "body": raw,
            })
            return json.loads(raw).get("id")
    except Exception as exc:
        _emit_log("opencode.serve.exception", {
            "method": "POST",
            "url": url,
            "error_type": type(exc).__name__,
            "message": str(exc),
        })
        return None


def _run_direct(
    *,
    agent: str,
    prompt: str,
    host: str = "127.0.0.1",
    port: int = 18765,
    model: str | None = None,
    session_id: str | None = None,
    on_chunk: Callable[[str], None] | None = None,
    timeout: int = 300,
    stall_timeout: int | None = None,
    cwd: str | Path | None = None,
    workflow_run_id: int | None = None,
    execution_id: str | None = None,
    project_session_id: str | None = None,
    role: str | None = None,
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
    _emit_log("run.called", {
        "agent": agent,
        "model": model,
        "host": host,
        "port": port,
        "session_id": session_id,
        "cwd": str(cwd) if cwd else None,
        "prompt_chars": len(prompt or ""),
    })
    if not is_reachable(host, port):
        _emit_log("run.not_reachable", {
            "agent": agent,
            "host": host,
            "port": port,
        })
        return rs.err(
            agent=agent,
            error_type="ConnectionError",
            message=f"opencode serve not reachable on {host}:{port}",
            retryable=True,
        )

    run_id = rs.new_run_id()

    if not session_id:
        session_id = precreate_session(host, port, cwd=cwd)
        _emit_log("run.precreate_session", {
            "agent": agent,
            "host": host,
            "port": port,
            "session_id": session_id,
        })

    binary = find(required=True)
    cmd = _build_cmd(binary, host, port, agent, model, session_id)
    from task_hounds_api.opencode.question_bridge import QuestionListener
    question_listener = (
        QuestionListener(
            host=host,
            port=port,
            opencode_session_id=session_id,
            project_session_id=project_session_id,
            role=role or agent,
            workspace=cwd,
        )
        if session_id
        else None
    )
    if question_listener:
        question_listener.start()
    try:
        text = _run_cmd(
            cmd, prompt, run_id, on_chunk,
            timeout=timeout, stall_timeout=stall_timeout, cwd=cwd, agent=agent,
            workflow_run_id=workflow_run_id,
            execution_id=execution_id,
            waiting_for_question=question_listener.is_waiting if question_listener else None,
        )
        _emit_log("run.completed", {
            "agent": agent,
            "run_id": run_id,
            "session_id": session_id,
            "output_chars": len(text),
        })
        return rs.ok(
            agent=agent,
            run_id=run_id,
            status=rs.STATUS_COMPLETED,
            text=text,
            session_id=session_id,
        )
    except Exception as exc:
        raw = getattr(exc, "partial_text", "") or str(exc)
        retryable = "balance" not in raw.lower() and "unauthorized" not in raw.lower()
        _emit_log("run.failed", {
            "agent": agent,
            "run_id": run_id,
            "session_id": session_id,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "retryable": retryable,
        })
        return rs.err(
            agent=agent,
            run_id=run_id,
            error_type=type(exc).__name__,
            message=str(exc),
            retryable=retryable,
            raw=raw,
        )
    finally:
        if question_listener:
            question_listener.stop()


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
    stall_timeout: int | None = None,
    retry_stalled: bool = False,
    cwd: str | Path | None = None,
    workflow_run_id: int | None = None,
    project_session_id: str | None = None,
    role: str | None = None,
    purpose: str = "graph",
    execution_id: str | None = None,
) -> dict:
    """Run through event-driven per-server and per-project admission control."""
    from task_hounds_api.opencode.request_scheduler import scheduled_call

    def invoke(active_session_id: str | None, active_prompt: str = prompt) -> dict:
        return scheduled_call(
            host=host,
            port=port,
            project_session_id=project_session_id,
            purpose=purpose,
            timeout=timeout,
            func=lambda: _run_direct(
                agent=agent,
                prompt=active_prompt,
                host=host,
                port=port,
                model=model,
                session_id=active_session_id,
                on_chunk=on_chunk,
                timeout=timeout,
                stall_timeout=stall_timeout,
                cwd=cwd,
                workflow_run_id=workflow_run_id,
                execution_id=execution_id,
                project_session_id=project_session_id,
                role=role,
            ),
        )

    result = invoke(session_id)
    error = result.get("error") or {}
    if retry_stalled and error.get("type") == "StallTimeoutError":
        fresh_session_id = precreate_session(host, port, cwd=cwd)
        if not fresh_session_id:
            return result
        partial = str(error.get("raw") or "").strip()
        continuation_prompt = (
            f"{prompt}\n\n"
            "=== STALLED RUN RECOVERY ===\n"
            "The previous run stopped producing events. Continue the same task in "
            "this fresh session. Inspect the current workspace before acting. Do "
            "not repeat completed tool actions or file changes. Continue from the "
            "partial response below and finish the answer.\n\n"
            f"PARTIAL RESPONSE:\n{partial or '(no text was emitted)'}"
        )
        _emit_log("run.stall_fresh_session_retry", {
            "agent": agent,
            "previous_run_id": result.get("run_id"),
            "fresh_session_id": fresh_session_id,
            "project_session_id": project_session_id,
            "role": role,
            "partial_chars": len(partial),
        })
        retried = invoke(fresh_session_id, continuation_prompt)
        if retried.get("ok") and partial:
            output = retried.setdefault("output", {})
            continued = str(output.get("text") or "").strip()
            output["text"] = f"{partial}\n{continued}".strip()
        return retried
    if timeout != 900 or error.get("type") != "TimeoutError":
        return result

    fresh_session_id = precreate_session(host, port, cwd=cwd)
    if not fresh_session_id:
        return result
    _emit_log("run.timeout_fresh_session_retry", {
        "agent": agent,
        "previous_run_id": result.get("run_id"),
        "previous_session_id": session_id,
        "fresh_session_id": fresh_session_id,
        "project_session_id": project_session_id,
        "role": role,
    })
    if project_session_id and role:
        from task_hounds_api.db.ops import execution as db_execution
        db_execution.bind_opencode_session(
            project_session_id,
            role,
            fresh_session_id,
        )
    return invoke(fresh_session_id)


def _build_cmd(binary, host, port, agent, model, session_id) -> list[str]:
    cmd = [
        str(binary), "run",
        "--attach", f"http://{host}:{port}",
        "--format", "json",
        "--dangerously-skip-permissions",
    ]
    if thinking_enabled():
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


def _process_line(
    raw: str,
    text_parts: list[str],
    on_chunk,
    reasoning_parts: list[str] | None = None,
) -> None:
    raw = raw.rstrip("\n")
    if not raw:
        return
    if on_chunk:
        try:
            on_chunk(raw)
        except Exception:
            pass
    try:
        ev = json.loads(raw)
    except json.JSONDecodeError:
        return
    etype = ev.get("type", "")
    if etype == "text":
        txt = (ev.get("part") or {}).get("text", "").strip()
        if txt:
            text_parts.append(txt)
    elif etype == "reasoning":
        txt = (ev.get("part") or {}).get("text", "").strip()
        if txt:
            if reasoning_parts is not None:
                reasoning_parts.append(txt)
            _safe_print(f"[think] {txt[:200]}")
    elif etype == "tool_use":
        part = ev.get("part") or {}
        tool_name = part.get("tool", "?")
        state = part.get("state") or {}
        detail = state.get("output") or str(state.get("input") or "")[:120]
        _safe_print(f"[tool: {tool_name}] {str(detail)[:120]}")
    elif etype == "error":
        err_msg = str(ev.get("error", "unknown error"))
        _safe_print(f"[error] {err_msg}")


class StallTimeoutError(TimeoutError):
    def __init__(self, message: str, partial_text: str = "") -> None:
        super().__init__(message)
        self.partial_text = partial_text


def _run_cmd(
    cmd: list[str],
    prompt: str,
    run_id: str,
    on_chunk,
    timeout: int | None = None,
    stall_timeout: int | None = None,
    cwd: str | Path | None = None,
    agent: str = "general",
    workflow_run_id: int | None = None,
    execution_id: str | None = None,
    waiting_for_question: Callable[[], bool] | None = None,
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
    reasoning_parts: list[str] = []
    error_parts: list[str] = []
    start = time.monotonic()
    last_event_at = start
    last_clock_at = start
    paused_seconds = 0.0
    running = [True]
    env = _isolated_env()

    _emit_log("opencode.emit.request", {
        "run_id": run_id,
        "agent": agent,
        "cmd": cmd,
        "cwd": str(cwd) if cwd else None,
        "prompt": prompt,
        "prompt_chars": len(prompt or ""),
        "env": _env_snapshot(env),
    })

    def _heartbeat():
        prev = 0
        while running[0]:
            elapsed = int(time.monotonic() - start)
            if elapsed >= prev + 30:
                prev = elapsed
                _safe_print(f"[{run_id}] {elapsed}s elapsed")
            time.sleep(5)

    threading.Thread(target=_heartbeat, daemon=True).start()

    cwd_arg = None
    if cwd:
        cwd_path = Path(cwd)
        try:
            cwd_path = cwd_path.resolve(strict=True)
        except OSError as exc:
            _emit_log("opencode.process.invalid_cwd", {
                "run_id": run_id,
                "agent": agent,
                "cwd": str(cwd),
                "error_type": type(exc).__name__,
                "message": str(exc),
            })
            raise NotADirectoryError(f"opencode cwd is invalid: {cwd!s} ({exc})") from exc
        if not cwd_path.is_dir():
            _emit_log("opencode.process.invalid_cwd", {
                "run_id": run_id,
                "agent": agent,
                "cwd": str(cwd_path),
                "message": "resolved cwd is not a directory",
            })
            raise NotADirectoryError(f"opencode cwd is not a directory: {cwd_path}")
        cwd_arg = str(cwd_path)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd_arg,
    )
    _emit_log("opencode.process.started", {
        "run_id": run_id,
        "agent": agent,
        "pid": proc.pid,
    })
    registry.register_run(run_id, proc)
    registry.register_agent_run(agent, run_id, proc)
    if workflow_run_id is not None:
        registry.register_workflow_run(workflow_run_id, run_id, proc)
    if execution_id:
        registry.register_execution(execution_id, run_id, proc)
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
            now = time.monotonic()
            waiting = False
            if waiting_for_question:
                try:
                    waiting = waiting_for_question()
                except Exception:
                    waiting = False
            if waiting:
                paused_seconds += now - last_clock_at
                last_event_at = now
            last_clock_at = now
            active_elapsed = now - start - paused_seconds
            if timeout is not None and active_elapsed >= timeout:
                _kill_proc(proc)
                raise TimeoutError(
                    f"opencode run timed out after {timeout}s (run_id={run_id})"
                )
            if stall_timeout is not None and (now - last_event_at) >= stall_timeout:
                _kill_proc(proc)
                raise StallTimeoutError(
                    f"opencode stream stalled for {stall_timeout}s (run_id={run_id})",
                    "\n".join(text_parts).strip(),
                )
            try:
                line = line_queue.get(timeout=poll_interval)
            except queue.Empty:
                continue
            if line is _EOF:
                break
            last_event_at = time.monotonic()
            _emit_log("opencode.emit.stdout", {
                "run_id": run_id,
                "agent": agent,
                "line": line.rstrip("\n"),
            })
            _process_line(line, text_parts, on_chunk, reasoning_parts)
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
            _emit_log("opencode.process.exit_nonzero", {
                "run_id": run_id,
                "agent": agent,
                "returncode": proc.returncode,
                "stderr": stderr_out,
                "text_chars": sum(len(part) for part in text_parts),
                "error_events": error_parts,
            })
            raise RuntimeError(
                f"opencode run exited {proc.returncode} "
                f"(run_id={run_id}, emit_log={_emit_log_path()}): {stderr_out[:300]}"
            )
        if error_parts:
            _emit_log("opencode.emit.error_events", {
                "run_id": run_id,
                "agent": agent,
                "returncode": proc.returncode,
                "error_events": error_parts,
                "text_chars": sum(len(part) for part in text_parts),
            })
            raise RuntimeError(
                f"opencode stream error "
                f"(run_id={run_id}, emit_log={_emit_log_path()}): {error_parts[-1][:500]}"
            )

        stderr_out = proc.stderr.read()
        _emit_log("opencode.process.exit_zero", {
            "run_id": run_id,
            "agent": agent,
            "returncode": proc.returncode,
            "stderr": stderr_out,
            "text_chars": sum(len(part) for part in text_parts),
        })
        assembled = "\n".join(text_parts).strip()
        if not assembled and reasoning_parts:
            # Interleaved-thinking models (MiniMax M2.x, Kimi K3, Qwen with
            # thinking enabled) sometimes put their entire final answer in a
            # reasoning part and stop on tool-calls without ever emitting a
            # plain text part. Fall back to the last reasoning part so the
            # caller (e.g. the Chat agent) does not surface an empty reply.
            assembled = reasoning_parts[-1].strip()
            _emit_log("opencode.emit.reasoning_fallback", {
                "run_id": run_id,
                "agent": agent,
                "reasoning_parts": len(reasoning_parts),
                "fallback_chars": len(assembled),
            })
        return assembled
    finally:
        running[0] = False
        registry.unregister_run(run_id)
        registry.unregister_agent_run(agent, run_id)
        if workflow_run_id is not None:
            registry.unregister_workflow_run(workflow_run_id, run_id)
        if execution_id:
            registry.unregister_execution(execution_id, run_id)
        reader.join(timeout=2)
