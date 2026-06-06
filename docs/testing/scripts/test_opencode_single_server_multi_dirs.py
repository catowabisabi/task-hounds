import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


PROJECTS = [
    Path(r"C:\Users\enoma\Desktop\projects\cato-todo"),
    Path(r"C:\Users\enoma\Desktop\projects\task-hounds-projects"),
    Path(r"C:\Users\enoma\Desktop\projects\test"),
]
PORT = 40964


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
    env["XDG_CONFIG_HOME"] = str(runtime / "opencode_single_dir_test" / ".config")
    env["XDG_DATA_HOME"] = str(runtime / "opencode_single_dir_test" / ".local" / "share")
    env["OPENCODE_CONFIG_DIR"] = str(runtime / "opencode_config")
    return env


def wait_ready(port: int, proc: subprocess.Popen) -> None:
    deadline = time.time() + 30
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/session", timeout=2):
                return
        except urllib.error.HTTPError:
            return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"server not ready on {port}: {last_error}")


def run_attached(opencode: str, project: Path, env: dict[str, str]) -> str | None:
    prompt = f"Reply with only your current working directory. Marker: {project}"
    result = subprocess.run(
        [
            opencode,
            "run",
            "--attach",
            f"http://127.0.0.1:{PORT}",
            "--dir",
            str(project),
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
    print(f"\nrun --dir {project}")
    print(f"exit={result.returncode}")
    if result.stderr.strip():
        print("stderr:")
        print(result.stderr.strip()[-1000:])

    session_id = None
    final_texts = []
    reasoning = []
    for line in result.stdout.splitlines():
        if not line.strip().startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("sessionID"):
            session_id = event["sessionID"]
        if event.get("type") == "text":
            part = event.get("part") or {}
            final_texts.append(part.get("text") or event.get("text") or "")
        if event.get("type") == "reasoning":
            part = event.get("part") or {}
            reasoning.append(part.get("text") or event.get("text") or "")

    if final_texts:
        print("text:")
        print("\n".join(final_texts).strip())
    elif reasoning:
        print("reasoning-only:")
        print("\n".join(reasoning).strip()[-1000:])
    else:
        print("no model text returned")
        print(result.stdout[-1000:])
    print(f"sessionID={session_id}")
    return session_id


def list_sessions(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/session", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    opencode = find_opencode()
    env = env_for_child()
    serve_dir = PROJECTS[0]
    print(f"OpenCode: {opencode}")
    print(f"Single serve cwd: {serve_dir}")
    print(f"Port: {PORT}")

    proc = subprocess.Popen(
        [opencode, "serve", "--hostname", "127.0.0.1", "--port", str(PORT)],
        cwd=str(serve_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_ready(PORT, proc)
        print(f"Ready: http://127.0.0.1:{PORT} pid={proc.pid}")

        session_ids = []
        for project in PROJECTS:
            if not project.is_dir():
                raise RuntimeError(f"missing project dir: {project}")
            session_ids.append(run_attached(opencode, project, env))

        print("\n=== Session metadata from the single server ===")
        sessions = list_sessions(PORT)
        wanted = set(item for item in session_ids if item)
        for session in sessions:
            if session.get("id") in wanted:
                print(
                    json.dumps(
                        {
                            "id": session.get("id"),
                            "directory": session.get("directory"),
                            "path": session.get("path"),
                            "projectID": session.get("projectID"),
                            "title": session.get("title"),
                        },
                        ensure_ascii=False,
                    )
                )
    finally:
        print("\nStopping server...")
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
