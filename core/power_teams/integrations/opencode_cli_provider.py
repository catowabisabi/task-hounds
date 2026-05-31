"""
opencode_cli_provider.py — Backend provider that uses `opencode run --attach`.

This is the default provider.  It wraps the existing runner logic so that
send_to_agent() becomes backend-agnostic.

To add a new backend, create a sibling file (e.g. openclaw_provider.py)
that implements BaseProvider, then register it in base_provider.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from power_teams.integrations.base_provider import BaseProvider

ROOT = Path(__file__).resolve().parents[3]
FILES_DIR = ROOT / "core" / "runtime" / "agent_files"
LOG_DIR = ROOT / "core" / "runtime" / "logs"
RUN_LOG = LOG_DIR / "runner.log"
OPENCODE_CONFIG_HOME = ROOT / "core" / "runtime" / "opencode_home" / ".config"
OPENCODE_DATA_HOME = ROOT / "core" / "runtime" / "opencode_home" / ".local" / "share"
OPENCODE_CONFIG_DIR = ROOT / "core" / "runtime" / "opencode_config"


def _log(msg: str) -> None:
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).isoformat()
    line = f"[{stamp}] {msg}\n"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="", flush=True)


def _append(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as h:
        h.write(value)


def _opencode_env() -> dict[str, str]:
    env = os.environ.copy()
    OPENCODE_CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    OPENCODE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    env.pop("OPENCODE_HOME", None)
    env["XDG_CONFIG_HOME"] = str(OPENCODE_CONFIG_HOME)
    env["OPENCODE_CONFIG_DIR"] = str(OPENCODE_CONFIG_DIR)
    return env


def _opencode_bin() -> str:
    from power_teams.runtime.opencode_binary import find_opencode_bin
    return find_opencode_bin(required=True)


def _server_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    import socket as _sock
    try:
        with _sock.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


class OpencodeCLIProvider(BaseProvider):
    """
    Runs `opencode run --attach <url>` as a subprocess and streams the
    JSON output back.  Each call creates its own opencode session unless
    session reuse is enabled via POWER_TEAMS_REUSE_OPENCODE_SESSIONS.
    """

    def __init__(self, agent_row: dict, *, stream_file: Path | None = None, log_fn=None):
        self._row = agent_row
        self._stream = stream_file or FILES_DIR / f"{agent_row.get('name', 'agent')}_stream.txt"
        self._log = log_fn or (lambda msg: _log(f"{agent_row.get('name', 'agent')}: {msg}"))
        # Parse backend_config_json for extra flags
        cfg_raw = agent_row.get("backend_config_json") or "{}"
        try:
            self._cfg = json.loads(cfg_raw) if isinstance(cfg_raw, str) else (cfg_raw or {})
        except Exception:
            self._cfg = {}

    # ── BaseProvider interface ──────────────────────────────────

    def health_check(self) -> bool:
        host = self._row.get("host") or "127.0.0.1"
        port = int(self._row.get("port") or 0)
        return bool(port and _server_reachable(host, port))

    def ensure_running(self) -> None:
        """Start the opencode server if it is not reachable."""
        if self.health_check():
            return
        base_url = self._base_url()
        self._log(f"server at {base_url} unreachable — restarting opencode supervisor")
        _append(self._stream, "[system] opencode server unreachable, restarting…\n")
        try:
            from power_teams.runtime.opencode_supervisor import OpenCodeSupervisor
            supervisor = OpenCodeSupervisor(cwd=ROOT, startup_timeout=90)
            supervisor.start()
            self._log(f"opencode servers restarted — manager={supervisor.manager_port}")
            _append(self._stream,
                    f"[system] opencode servers restarted "
                    f"manager={supervisor.manager_port} worker={supervisor.worker_port}\n")
        except Exception as exc:
            self._log(f"ERROR — could not restart opencode supervisor: {exc}")
            _append(self._stream, f"[system] ERROR: could not restart opencode servers: {exc}\n")

    # ── Core send ───────────────────────────────────────────────

    def send_message(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        on_chunk: Callable[[str], None] | None = None,
        timeout: int = 300,
    ) -> str:
        """Run opencode, stream JSON output, return full response text."""
        self.ensure_running()

        # Re-read row in case port changed after restart
        try:
            from power_teams.db import get_agent
            self._row = dict(get_agent(self._row["name"]))
        except Exception:
            pass

        base_url = self._base_url()

        # Pre-create a session so `opencode run --attach` never gets
        # "Session not found" (the CLI doesn't auto-create in attach mode).
        if not session_id:
            session_id = self._precreate_session(base_url)

        cmd = self._build_cmd(base_url, session_id)
        self._log(f"opencode run --attach {base_url}  session={session_id and session_id[:16]}")
        _append(self._stream,
                f"[system] opencode run → {base_url}  "
                f"agent={self._row.get('opencode_agent', 'general')}  "
                f"session={session_id and session_id[:16]}\n")

        return self._run_cmd(cmd, prompt, base_url, on_chunk=on_chunk)

    # ── Helpers ─────────────────────────────────────────────────

    def _base_url(self) -> str:
        host = self._row.get("host") or "127.0.0.1"
        port = self._row.get("port") or 4096
        return f"http://{host}:{port}"

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
            self._log(f"pre-created session {sid}")
            return sid
        except Exception as exc:
            self._log(f"ERROR — could not pre-create session: {exc}")
            _append(self._stream, f"[system] ERROR: pre-create session failed: {exc}\n")
            return None

    def _build_cmd(self, base_url: str, session_id: str | None) -> list[str]:
        agent_name = str(self._row.get("opencode_agent") or self._cfg.get("agent") or "").strip()
        model = self._row.get("model")

        cmd = [
            _opencode_bin(), "run",
            "--attach", base_url,
            "--format", "json",
            "--dangerously-skip-permissions",
        ]
        # thinking flag — on by default, can be disabled via backend_config_json
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
                    self._log(f"{elapsed}s elapsed (deep thinking…)")
                    _append(self._stream, f"\n[system] {elapsed}s elapsed — deep thinking…\n")
                time.sleep(5)

        def _permission_watcher():
            import urllib.request as _urlreq
            import urllib.error as _urlerr
            while running[0]:
                try:
                    req = _urlreq.Request(f"{base_url}/permission",
                                          headers={"Accept": "application/json"})
                    with _urlreq.urlopen(req, timeout=3) as resp:
                        pending = json.loads(resp.read().decode())
                    for perm in (pending or []):
                        pid = perm.get("id")
                        if not pid:
                            continue
                        pname = perm.get("permission", "?")
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
                            self._log(msg)
                            _append(self._stream, msg + "\n")
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
                    ev = json.loads(raw)
                    etype = ev.get("type", "")

                    if etype == "text":
                        txt = (ev.get("part") or {}).get("text", "").strip()
                        if txt:
                            text_parts.append(txt)
                            _append(self._stream, txt + "\n")
                            if on_chunk:
                                on_chunk(txt)
                            print(txt, flush=True)

                    elif etype == "reasoning":
                        txt = (ev.get("part") or {}).get("text", "").strip()
                        if txt:
                            _append(self._stream, f"[think] {txt}\n")
                            preview = txt[:200].replace("\n", " ")
                            print(f"[think] {preview}{'…' if len(txt) > 200 else ''}", flush=True)

                    elif etype == "tool_use":
                        part = ev.get("part") or {}
                        tool_name = part.get("tool", "?")
                        state_obj = part.get("state") or {}
                        detail = state_obj.get("output") or str(state_obj.get("input") or "")[:120]
                        _append(self._stream, f"[tool: {tool_name}] {str(detail)[:200]}\n")
                        print(f"[tool: {tool_name}] {str(detail)[:120]}", flush=True)

                    elif etype == "error":
                        err_msg = str(ev.get("error", "unknown error"))
                        self._log(f"session error: {err_msg}")
                        _append(self._stream, f"[error] {err_msg}\n")

                except json.JSONDecodeError:
                    _append(self._stream, raw + "\n")

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
