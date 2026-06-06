from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


EXPECTED_OPENCODE_VERSION = "1.15.13"
EXPECTED_PLUGINS = {
    "oh-my-openagent": "4.5.12",
    "opencode-scheduler": "1.3.0",
}
EXPECTED_MCP = {
    "@playwright/mcp": "0.0.75",
}
EXPECTED_DEFAULT_MODEL = "bailian-coding-plan/MiniMax-M2.5"
ROLES = ("manager", "worker", "reviewer", "chat")


class CheckFailure(Exception):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


ROOT = repo_root()
CORE = ROOT / "core"
RUNTIME = CORE / "runtime"
DB_PATH = CORE / "db" / "power_teams.db"


def pass_line(name: str, detail: str = "") -> None:
    print(f"[PASS] {name}{': ' + detail if detail else ''}")


def warn_line(name: str, detail: str) -> None:
    print(f"[WARN] {name}: {detail}")


def fail_line(name: str, detail: str) -> None:
    print(f"[FAIL] {name}: {detail}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def http_json(url: str, *, timeout: int = 5, method: str = "GET", body: dict | None = None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return response.status, json.loads(raw) if raw else None


def wait_http_json(url: str, *, timeout: int = 30):
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            return http_json(url, timeout=5)
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.75)
    raise CheckFailure(f"{url} did not return JSON within {timeout}s: {last_error}")


def port_open(host: str, port: int, *, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def import_project_modules():
    sys.path.insert(0, str(CORE))
    from power_teams.runtime.opencode_binary import find_opencode_bin
    from power_teams.runtime.opencode_supervisor import opencode_env
    from power_teams.runtime.opencode_lifecycle import OpenCodeLifecycleManager
    from power_teams.db import connect

    return find_opencode_bin, opencode_env, OpenCodeLifecycleManager, connect


def check_managed_opencode_binary() -> None:
    find_opencode_bin, _opencode_env, _manager_cls, _connect = import_project_modules()
    expected_name = "opencode.exe" if os.name == "nt" else "opencode"
    expected = RUNTIME / "opencode_runtime" / "node_modules" / "opencode-ai" / "bin" / expected_name

    # This confirms Task Hounds cannot accidentally use a global npm or PATH OpenCode.
    resolved = Path(find_opencode_bin(required=True)).resolve()
    require(resolved == expected.resolve(), f"resolved {resolved}, expected {expected}")

    result = subprocess.run(
        [str(resolved), "--version"],
        text=True,
        capture_output=True,
        timeout=20,
    )
    require(result.returncode == 0, result.stderr.strip() or result.stdout.strip())
    version = result.stdout.strip() or result.stderr.strip()
    require(version == EXPECTED_OPENCODE_VERSION, f"version {version}, expected {EXPECTED_OPENCODE_VERSION}")
    pass_line("managed OpenCode binary", str(resolved))


def check_runtime_config_files() -> None:
    xdg_config = RUNTIME / "opencode_home" / ".config" / "opencode" / "opencode.jsonc"
    config_dir = RUNTIME / "opencode_config" / "opencode.jsonc"
    for path in (xdg_config, config_dir):
        require(path.exists(), f"missing {path}")
        data = load_json(path)

        # This catches accidental @latest usage in install/config scripts.
        plugins = data.get("plugin") or []
        for name, version in EXPECTED_PLUGINS.items():
            require(f"{name}@{version}" in plugins, f"{path} missing {name}@{version}")

        command = (((data.get("mcp") or {}).get("playwright") or {}).get("command") or [])
        for name, version in EXPECTED_MCP.items():
            require(f"{name}@{version}" in command, f"{path} missing {name}@{version}")

        require(data.get("model") == EXPECTED_DEFAULT_MODEL, f"{path} model={data.get('model')!r}, expected {EXPECTED_DEFAULT_MODEL!r}")
        require("latest" not in json.dumps(data), f"{path} still contains latest")
    pass_line("pinned OpenCode config", "no @latest in plugins or playwright MCP")


def check_isolated_env() -> None:
    _find_opencode_bin, opencode_env, _manager_cls, _connect = import_project_modules()
    env = opencode_env()
    require(Path(env["XDG_CONFIG_HOME"]).resolve() == (RUNTIME / "opencode_home" / ".config").resolve(), "wrong XDG_CONFIG_HOME")
    require(Path(env["XDG_DATA_HOME"]).resolve() == (RUNTIME / "opencode_home" / ".local" / "share").resolve(), "wrong XDG_DATA_HOME")
    require(Path(env["OPENCODE_CONFIG_DIR"]).resolve() == (RUNTIME / "opencode_config").resolve(), "wrong OPENCODE_CONFIG_DIR")
    require("OPENCODE_HOME" not in env, "OPENCODE_HOME must be removed")
    pass_line("isolated OpenCode env", "XDG_CONFIG_HOME, XDG_DATA_HOME, OPENCODE_CONFIG_DIR")


def check_retry_guard() -> None:
    _find_opencode_bin, _opencode_env, manager_cls, _connect = import_project_modules()
    count = manager_cls()._recent_start_failure_count()

    # Only real startup failures should count here. process_gone/stale history must not lock startup.
    require(count < 10, f"recent startup failure guard is locked at {count}")
    pass_line("OpenCode startup retry guard", f"count={count}")


def check_default_model_run() -> None:
    find_opencode_bin, opencode_env, _manager_cls, _connect = import_project_modules()
    workspace = load_json(RUNTIME / "settings.json").get("workspace_path") or str(ROOT)
    result = subprocess.run(
        [
            find_opencode_bin(required=True),
            "run",
            "--format",
            "json",
            "--dangerously-skip-permissions",
            "--dir",
            str(workspace),
            "diagnostic model ping; reply ok",
        ],
        text=True,
        capture_output=True,
        timeout=120,
        env=opencode_env(),
    )
    output = (result.stdout or "") + (result.stderr or "")
    require(result.returncode == 0, output.strip()[:1000])
    require('"type":"error"' not in output and '"type": "error"' not in output, output.strip()[:1000])
    pass_line("default OpenCode model ping", EXPECTED_DEFAULT_MODEL)


def check_db_bindings() -> dict | None:
    _find_opencode_bin, _opencode_env, _manager_cls, connect = import_project_modules()
    if not DB_PATH.exists():
        warn_line("db bindings", f"database not found: {DB_PATH}")
        return None

    with connect(DB_PATH) as db:
        rows = db.execute(
            "SELECT role, host, port, opencode_agent, model, server_instance_id, binding_source, updated_at "
            "FROM agent_runtime_bindings ORDER BY role"
        ).fetchall()
    by_role = {row["role"]: dict(row) for row in rows}
    missing = [role for role in ROLES if role not in by_role]
    require(not missing, f"missing role bindings: {missing}")

    ports = {int(by_role[role]["port"] or 0) for role in ROLES}
    hosts = {by_role[role]["host"] or "127.0.0.1" for role in ROLES}
    require(len(ports) == 1, f"roles are split across ports: {by_role}")
    require(len(hosts) == 1, f"roles are split across hosts: {by_role}")

    host = next(iter(hosts))
    port = next(iter(ports))
    require(port > 0, f"invalid bound port: {port}")
    pass_line("DB role bindings", f"{','.join(ROLES)} -> {host}:{port}")
    return {"host": host, "port": port, "bindings": by_role}


def check_bound_opencode_server(binding: dict | None) -> None:
    if not binding:
        return
    host = binding["host"]
    port = int(binding["port"])
    require(port_open(host, port), f"{host}:{port} is not reachable")

    # Startup discovery used to run too early and return 0 agents. Retry here before failing.
    _status, agents = wait_http_json(f"http://{host}:{port}/agent", timeout=45)
    require(isinstance(agents, list), f"/agent did not return a list: {agents!r}")
    require(len(agents) > 0, "/agent returned 0 agents after retry window")
    names = [item.get("name") for item in agents if isinstance(item, dict)]
    agent_modes = {str(item.get("name") or item.get("id") or ""): str(item.get("mode") or "") for item in agents if isinstance(item, dict)}
    for role, row in binding["bindings"].items():
        selected = str(row.get("opencode_agent") or "")
        require(selected, f"{role} has empty opencode_agent")
        require(agent_modes.get(selected) != "subagent", f"{role} is bound to subagent {selected!r}")
    pass_line("OpenCode /agent discovery", ", ".join(str(name) for name in names[:8]))


def check_fastapi(api_base: str) -> None:
    status, data = http_json(f"{api_base}/api/health", timeout=5)
    require(status == 200, f"health status={status}")
    pass_line("FastAPI health", json.dumps(data, ensure_ascii=False)[:200])

    status, data = http_json(f"{api_base}/api/chat/status", timeout=10)
    require(status == 200, f"chat/status status={status}")
    require(data.get("enabled"), f"chat runtime disabled: {data}")
    pass_line("chat runtime status", data.get("reason") or "enabled")


def check_chat_ping(api_base: str, timeout: int) -> None:
    # This is optional because it writes a user/chat message into the active session.
    payload = {"content": "diagnostic ping from Task Hounds runtime contract test; reply with one short sentence"}
    try:
        status, data = http_json(f"{api_base}/api/chat/send", timeout=timeout, method="POST", body=payload)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CheckFailure(f"chat/send HTTP {exc.code}: {detail}") from exc

    require(status == 200, f"chat/send status={status}")
    require(data.get("ok"), f"chat/send not ok: {data}")
    reply = (data.get("reply") or "").strip()
    require(reply, f"chat/send returned empty reply: {data}")
    pass_line("chat agent ping", reply[:200])


def main() -> int:
    parser = argparse.ArgumentParser(description="Confirm Task Hounds backend/OpenCode runtime contracts.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8766")
    parser.add_argument("--skip-api", action="store_true", help="Only run local DB/runtime checks.")
    parser.add_argument("--chat-ping", action="store_true", help="Also POST to /api/chat/send. This writes chat history.")
    parser.add_argument("--chat-timeout", type=int, default=180)
    args = parser.parse_args()

    print("Task Hounds runtime contract test")
    print(f"repo={ROOT}")
    print()

    checks = [
        ("managed OpenCode binary", check_managed_opencode_binary),
        ("pinned runtime config", check_runtime_config_files),
        ("isolated env", check_isolated_env),
        ("retry guard", check_retry_guard),
        ("default model ping", check_default_model_run),
    ]

    failures = 0
    for name, fn in checks:
        try:
            fn()
        except Exception as exc:
            failures += 1
            fail_line(name, str(exc))

    binding = None
    try:
        binding = check_db_bindings()
        check_bound_opencode_server(binding)
    except Exception as exc:
        failures += 1
        fail_line("DB/OpenCode binding", str(exc))

    if not args.skip_api:
        try:
            check_fastapi(args.api_base.rstrip("/"))
        except Exception as exc:
            failures += 1
            fail_line("FastAPI/chat status", str(exc))

        if args.chat_ping:
            try:
                check_chat_ping(args.api_base.rstrip("/"), args.chat_timeout)
            except Exception as exc:
                failures += 1
                fail_line("chat ping", str(exc))
        else:
            warn_line("chat ping", "skipped; add --chat-ping to write a diagnostic chat message")

    print()
    if failures:
        print(f"FAILED: {failures} check(s) failed")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
