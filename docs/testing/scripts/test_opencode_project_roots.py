import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


PROJECTS = [
    Path(r"C:\Users\enoma\Desktop\projects\cato-todo"),
    Path(r"C:\Users\enoma\Desktop\projects\task-hounds-projects"),
    Path(r"C:\Users\enoma\Desktop\projects\test"),
]
PORTS = [40961, 40962, 40963]


def find_opencode() -> str:
    local = Path.home() / ".opencode" / "bin" / "opencode.exe"
    if local.exists():
        return str(local)
    for part in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(part) / "opencode.exe"
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("opencode.exe not found")


def env_for_child() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key.upper() == "PATH" and key != "Path":
            env.pop(key, None)
    env.pop("OPENCODE_HOME", None)
    runtime = Path.cwd() / "core" / "runtime"
    env["XDG_CONFIG_HOME"] = str(runtime / "opencode_home" / ".config")
    env["XDG_DATA_HOME"] = str(runtime / "opencode_home" / ".local" / "share")
    env["OPENCODE_CONFIG_DIR"] = str(runtime / "opencode_config")
    return env


def wait_ready(port: int, proc: subprocess.Popen) -> None:
    url = f"http://127.0.0.1:{port}/session"
    deadline = time.time() + 30
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except urllib.error.HTTPError:
            return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"server not ready on {port}: {last_error}")


def ask_root(opencode: str, port: int, project: Path, env: dict[str, str]) -> str:
    prompt = (
        "Reply with ONLY this information, no explanation:\n"
        "1. current working directory\n"
        "2. project root directory\n"
        f"3. whether you can see this exact marker path: {project}\n"
    )
    result = subprocess.run(
        [
            opencode,
            "run",
            "--attach",
            f"http://127.0.0.1:{port}",
            "--format",
            "json",
            "--thinking",
            "--dangerously-skip-permissions",
            prompt,
        ],
        cwd=str(project),
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        return f"ERROR exit={result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    texts: list[str] = []
    reasoning: list[str] = []
    session_ids: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip().startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("sessionID"):
            session_ids.append(event["sessionID"])
        if event.get("type") == "text":
            part = event.get("part") or {}
            texts.append(part.get("text") or event.get("text") or "")
        if event.get("type") == "reasoning":
            part = event.get("part") or {}
            reasoning.append(part.get("text") or event.get("text") or "")

    session_note = ""
    if session_ids:
        session_id = session_ids[-1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/session", timeout=5) as response:
                sessions = json.loads(response.read().decode("utf-8"))
            matched = next((item for item in sessions if item.get("id") == session_id), None)
            if matched:
                session_note = (
                    f"\n\nOpenCode session metadata:\n"
                    f"id={matched.get('id')}\n"
                    f"directory={matched.get('directory')}\n"
                    f"path={matched.get('path')}\n"
                    f"projectID={matched.get('projectID')}"
                )
        except Exception as exc:
            session_note = f"\n\nCould not read session metadata: {exc}"

    text = "\n".join(texts).strip()
    if text:
        return text + session_note
    fallback = "\n".join(reasoning).strip()
    if fallback:
        return "(model returned reasoning but no final text)\n" + fallback + session_note
    return "(no text answer returned)" + session_note + "\nRAW STDOUT:\n" + result.stdout[-2000:] + "\nRAW STDERR:\n" + result.stderr[-2000:]


def main() -> int:
    opencode = find_opencode()
    env = env_for_child()
    procs: list[tuple[int, Path, subprocess.Popen]] = []

    print(f"OpenCode: {opencode}")
    try:
        for project, port in zip(PROJECTS, PORTS):
            if not project.is_dir():
                raise RuntimeError(f"missing project dir: {project}")
            print(f"\n=== Starting {project} on port {port} ===", flush=True)
            proc = subprocess.Popen(
                [opencode, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                cwd=str(project),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            procs.append((port, project, proc))
            wait_ready(port, proc)
            print(f"Ready: http://127.0.0.1:{port} pid={proc.pid}")

        print("\n=== Asking each server for its root ===")
        for port, project, _proc in procs:
            print(f"\n--- Port {port} / {project} ---")
            print(ask_root(opencode, port, project, env))
    finally:
        print("\nStopping test servers...")
        for _port, _project, proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for _port, _project, proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
