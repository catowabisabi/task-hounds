"""
opencode.py — BackendAdapter for the OpenCode CLI backend.

By default every Task Hounds role shares one long-lived `opencode serve`
process. Each project-role still uses its own OpenCode session id, so
conversation memory stays isolated. Dedicated per-role servers remain
available via POWER_TEAMS_OPENCODE_TOPOLOGY=per_role for future extensions.
This adapter:
  - ensures the configured serve endpoint is reachable
  - runs `opencode run --attach` to send a prompt
  - returns JsonResult (never raises for expected failures)

Config fields (from agent_row or backend_config_json):
    host            str     default "127.0.0.1"
    port            int     assigned by supervisor / registry
    opencode_agent  str     agent persona, e.g. "general", "build"
    model           str     optional model override
    thinking        bool    enable extended thinking (default True)
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from power_teams.runtime.opencode_binary import find_opencode_bin

from power_teams.runtime.backends.base import BackendAdapter
from power_teams.runtime import result_schema as rs

ROOT = Path(__file__).resolve().parents[4]
LOG_DIR = ROOT / "core" / "runtime" / "logs"
OPENCODE_CONFIG_HOME = ROOT / "core" / "runtime" / "opencode_home" / ".config"
OPENCODE_DATA_HOME = ROOT / "core" / "runtime" / "opencode_home" / ".local" / "share"
OPENCODE_CONFIG_DIR  = ROOT / "core" / "runtime" / "opencode_config"
FILES_DIR            = ROOT / "core" / "runtime" / "agent_files"


# ── Module-level helpers ────────────────────────────────────────────────────

def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _opencode_env() -> dict[str, str]:
    env = os.environ.copy()
    OPENCODE_CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    OPENCODE_DATA_HOME.mkdir(parents=True, exist_ok=True)
    OPENCODE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    env["XDG_CONFIG_HOME"] = str(OPENCODE_CONFIG_HOME)
    env["XDG_DATA_HOME"] = str(OPENCODE_DATA_HOME)
    env["OPENCODE_CONFIG_DIR"] = str(OPENCODE_CONFIG_DIR)
    return env


def _is_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _read_tail(path: Path, max_chars: int = 4000) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-max_chars:]


class OpenCodeAdapter(BackendAdapter):
    """
    BackendAdapter implementation for the OpenCode CLI.

    One adapter instance per Task Hounds role.
    """

    BACKEND = "opencode"

    def __init__(self, agent_row: dict, *, stream_file: Path | None = None, log_fn=None):
        self._row      = dict(agent_row)
        self._agent_name = agent_row.get("name", "agent")
        self._stream   = stream_file or FILES_DIR / f"{self._agent_name}_stream.txt"
        self._log_fn   = log_fn or self._default_log
        self._log_path = LOG_DIR / "opencode" / f"{self._agent_name}.log"
        self._process: subprocess.Popen | None = None  # owned serve process
        self._process_lock = threading.Lock()

        cfg_raw = agent_row.get("backend_config_json") or "{}"
        try:
            self._cfg = json.loads(cfg_raw) if isinstance(cfg_raw, str) else (cfg_raw or {})
        except Exception:
            self._cfg = {}

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def backend_name(self) -> str:
        return self.BACKEND

    @property
    def agent_name(self) -> str:
        return self._agent_name

    def _host(self) -> str:
        return self._row.get("host") or "127.0.0.1"

    def _port(self) -> int:
        return int(self._row.get("port") or 0)

    def _base_url(self) -> str:
        return f"http://{self._host()}:{self._port()}"

    def _default_log(self, msg: str) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        run_log = LOG_DIR / "runner.log"
        line = f"[{_utc_now()}] {self._agent_name}: {msg}\n"
        with run_log.open("a", encoding="utf-8") as f:
            f.write(line)
        print(line, end="", flush=True)

    def _append_stream(self, text: str) -> None:
        self._stream.parent.mkdir(parents=True, exist_ok=True)
        with self._stream.open("a", encoding="utf-8") as h:
            h.write(text)

    def _refresh_row(self) -> None:
        """Re-read agent row from DB in case port changed after restart."""
        try:
            from power_teams.db import get_agent
            self._row = dict(get_agent(self._agent_name))
        except Exception:
            pass

    # ── BackendAdapter: lifecycle ────────────────────────────────────────────

    def start(self) -> dict:
        """Start this agent's opencode serve process if not already running."""
        run_id = rs.new_run_id()

        # First: trust the supervisor — if the port is reachable, we're up.
        if _is_reachable(self._host(), self._port()):
            return rs.ok(
                backend=self.BACKEND, agent=self._agent_name, run_id=run_id,
                status="already_running",
                text=f"opencode serve already reachable on {self._base_url()}",
            )

        # Try to use the shared supervisor to start all agents.
        self._log_fn(f"server at {self._base_url()} unreachable — starting supervisor")
        self._append_stream(f"[system] server unreachable, restarting opencode supervisor…\n")
        try:
            from power_teams.runtime.opencode_supervisor import OpenCodeSupervisor
            supervisor = OpenCodeSupervisor(cwd=ROOT, startup_timeout=90)
            supervisor.start()
            # Re-read port from DB (supervisor wrote the new port)
            self._refresh_row()
            self._log_fn(f"supervisor restarted — now on {self._base_url()}")
            self._append_stream(f"[system] opencode supervisor restarted — {self._base_url()}\n")
            return rs.ok(
                backend=self.BACKEND, agent=self._agent_name, run_id=run_id,
                status=rs.STATUS_STARTED,
                text=f"opencode serve started at {self._base_url()}",
            )
        except Exception as exc:
            self._log_fn(f"ERROR — could not start supervisor: {exc}")
            self._append_stream(f"[system] ERROR: could not start opencode: {exc}\n")
            return rs.err(
                backend=self.BACKEND, agent=self._agent_name, run_id=run_id,
                error_type="StartError",
                message=f"Could not start opencode serve: {exc}",
                retryable=True,
                raw=str(exc),
            )

    def stop(self) -> dict:
        """Stop the owned serve process, if any."""
        run_id = rs.new_run_id()
        with self._process_lock:
            proc = self._process
            if proc and proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                    )
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                self._process = None
                self._log_fn("serve process stopped")
                return rs.ok(
                    backend=self.BACKEND, agent=self._agent_name, run_id=run_id,
                    status=rs.STATUS_STOPPED, text="opencode serve stopped",
                )
        return rs.ok(
            backend=self.BACKEND, agent=self._agent_name, run_id=run_id,
            status=rs.STATUS_STOPPED, text="no owned process to stop",
        )

    def health(self) -> dict:
        """Check if the opencode serve port is reachable."""
        run_id = rs.new_run_id()
        port = self._port()
        if not port:
            return rs.err(
                backend=self.BACKEND, agent=self._agent_name, run_id=run_id,
                error_type="ConfigError",
                message="No port configured for this agent",
                retryable=False,
                status=rs.STATUS_UNHEALTHY,
            )
        alive = _is_reachable(self._host(), port)
        if alive:
            return rs.ok(
                backend=self.BACKEND, agent=self._agent_name, run_id=run_id,
                status=rs.STATUS_HEALTHY,
                text=f"opencode serve reachable on port {port}",
                metrics={"port": port, "host": self._host()},
            )
        return rs.err(
            backend=self.BACKEND, agent=self._agent_name, run_id=run_id,
            error_type="ConnectionError",
            message=f"opencode serve not reachable on {self._host()}:{port}",
            retryable=True,
            status=rs.STATUS_UNHEALTHY,
        )

    # ── BackendAdapter: core execution ───────────────────────────────────────

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        on_chunk: Callable[[str], None] | None = None,
        timeout: int = 300,
    ) -> dict:
        """
        Run opencode run --attach, stream JSON output, return JsonResult.
        Auto-starts the server if not reachable.
        """
        run_id = rs.new_run_id()

        # Ensure server is up
        start_result = self.start()
        if not start_result["ok"]:
            start_result["run_id"] = run_id
            return start_result

        # Re-read row in case port changed
        self._refresh_row()
        base_url = self._base_url()

        # Pre-create session
        if not session_id:
            session_id = self._precreate_session(base_url)

        cmd = self._build_cmd(base_url, session_id)
        self._log_fn(f"run --attach {base_url}  session={session_id and session_id[:16]}")
        self._append_stream(
            f"[system] opencode run → {base_url}  "
            f"agent={self._row.get('opencode_agent', 'general')}  "
            f"session={session_id and session_id[:16]}\n"
        )

        try:
            text = self._run_cmd(cmd, prompt, base_url, run_id=run_id, on_chunk=on_chunk)
            return rs.ok(
                backend=self.BACKEND, agent=self._agent_name, run_id=run_id,
                status=rs.STATUS_COMPLETED, text=text,
            )
        except Exception as exc:
            raw = str(exc)
            retryable = "balance" not in raw.lower() and "unauthorized" not in raw.lower()
            return rs.err(
                backend=self.BACKEND, agent=self._agent_name, run_id=run_id,
                error_type=type(exc).__name__,
                message=str(exc),
                retryable=retryable,
                raw=raw,
            )

    # ── BackendAdapter: observability ────────────────────────────────────────

    def logs(self, tail: int = 100) -> dict:
        run_id = rs.new_run_id()
        text = _read_tail(self._log_path)
        lines = text.splitlines()
        trimmed = "\n".join(lines[-tail:]) if len(lines) > tail else text
        return rs.ok(
            backend=self.BACKEND, agent=self._agent_name, run_id=run_id,
            status=rs.STATUS_COMPLETED, text=trimmed,
            metrics={"log_path": str(self._log_path), "lines": len(lines)},
        )

    # ── Private helpers ──────────────────────────────────────────────────────

    def _precreate_session(self, base_url: str) -> str | None:
        import urllib.request as _req
        try:
            r = _req.Request(
                f"{base_url}/session",
                data=json.dumps({}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _req.urlopen(r, timeout=10) as resp:
                sid = json.loads(resp.read().decode()).get("id")
            self._log_fn(f"pre-created session {sid}")
            return sid
        except Exception as exc:
            self._log_fn(f"ERROR — could not pre-create session: {exc}")
            self._append_stream(f"[system] ERROR: pre-create session failed: {exc}\n")
            return None

    def _build_cmd(self, base_url: str, session_id: str | None) -> list[str]:
        opencode_bin = find_opencode_bin(required=True)
        agent_name = str(self._row.get("opencode_agent") or self._cfg.get("agent") or "").strip()
        model = self._row.get("model")

        cmd = [
            opencode_bin, "run",
            "--attach", base_url,
            "--format", "json",
            "--dangerously-skip-permissions",
        ]
        if self._cfg.get("thinking", True):
            cmd.append("--thinking")
        if agent_name and agent_name.lower() not in {"default", "general"}:
            cmd += ["--agent", agent_name]
        if model:
            cmd += ["--model", model]
        if session_id:
            cmd += ["--session", session_id]
        return cmd

    def _run_cmd(
        self,
        cmd: list[str],
        prompt: str,
        base_url: str,
        run_id: str,
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        text_parts: list[str] = []
        start = time.monotonic()
        running = [True]

        def _heartbeat():
            prev = 0
            while running[0]:
                elapsed = int(time.monotonic() - start)
                if elapsed >= prev + 30:
                    prev = elapsed
                    self._log_fn(f"{elapsed}s elapsed (deep thinking…)  run={run_id}")
                    self._append_stream(f"\n[system] {elapsed}s elapsed — deep thinking…\n")
                time.sleep(5)

        def _permission_watcher():
            import urllib.request as _urlreq
            import urllib.error as _urlerr
            while running[0]:
                try:
                    req = _urlreq.Request(
                        f"{base_url}/permission",
                        headers={"Accept": "application/json"},
                    )
                    with _urlreq.urlopen(req, timeout=3) as resp:
                        pending = json.loads(resp.read().decode())
                    for perm in (pending or []):
                        pid = perm.get("id")
                        if not pid:
                            continue
                        pname   = perm.get("permission", "?")
                        patterns = perm.get("patterns", [])
                        body = json.dumps({"reply": "always"}).encode()
                        preq = _urlreq.Request(
                            f"{base_url}/permission/{pid}/reply",
                            data=body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        try:
                            _urlreq.urlopen(preq, timeout=3)
                            msg = f"[permission:auto-approved] {pname} {patterns}"
                            self._log_fn(msg)
                            self._append_stream(msg + "\n")
                        except Exception:
                            pass
                except (_urlerr.URLError, Exception):
                    pass
                time.sleep(2)

        threading.Thread(target=_heartbeat, daemon=True).start()
        threading.Thread(target=_permission_watcher, daemon=True).start()

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_opencode_env(),
            )
            proc.stdin.write(prompt)
            proc.stdin.close()

            for raw in proc.stdout:
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                try:
                    ev    = json.loads(raw)
                    etype = ev.get("type", "")

                    if etype == "text":
                        txt = (ev.get("part") or {}).get("text", "").strip()
                        if txt:
                            text_parts.append(txt)
                            self._append_stream(txt + "\n")
                            if on_chunk:
                                on_chunk(txt)
                            print(txt, flush=True)

                    elif etype == "reasoning":
                        txt = (ev.get("part") or {}).get("text", "").strip()
                        if txt:
                            self._append_stream(f"[think] {txt}\n")
                            preview = txt[:200].replace("\n", " ")
                            print(f"[think] {preview}{'…' if len(txt) > 200 else ''}", flush=True)

                    elif etype == "tool_use":
                        part     = ev.get("part") or {}
                        tool_name = part.get("tool", "?")
                        state_obj = part.get("state") or {}
                        detail    = state_obj.get("output") or str(state_obj.get("input") or "")[:120]
                        self._append_stream(f"[tool: {tool_name}] {str(detail)[:200]}\n")
                        print(f"[tool: {tool_name}] {str(detail)[:120]}", flush=True)

                    elif etype == "error":
                        err_msg = str(ev.get("error", "unknown error"))
                        self._log_fn(f"session error: {err_msg}")
                        self._append_stream(f"[error] {err_msg}\n")

                except json.JSONDecodeError:
                    self._append_stream(raw + "\n")

            proc.wait()
            running[0] = False

            if proc.returncode != 0:
                stderr_out = proc.stderr.read()
                raise RuntimeError(
                    f"opencode run exited {proc.returncode}: {stderr_out[:300]}"
                )

            return "\n".join(text_parts).strip()

        finally:
            running[0] = False
