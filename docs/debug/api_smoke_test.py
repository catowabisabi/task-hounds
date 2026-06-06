import subprocess
import time
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("SMOKE TEST: Task Hounds Desktop App")
print("=" * 60)

print("\n[1/6] Starting server...")
server_proc = subprocess.Popen(
    [sys.executable, "-m", "api.fastapi_server", "--port", "8765"],
    cwd=str(Path(__file__).resolve().parents[1]),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
time.sleep(4)

import urllib.request

def api_get(path):
    try:
        url = f"http://127.0.0.1:8765{path}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            if path == "/":
                return resp.status, body
            return resp.status, json.loads(body)
    except Exception as e:
        return -1, str(e)

def api_put(path, data):
    try:
        url = f"http://127.0.0.1:8765{path}"
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, method="PUT", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return -1, str(e)

def api_post(path):
    try:
        url = f"http://127.0.0.1:8765{path}"
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return -1, str(e)

print("\n[2/6] Testing API endpoints...")
endpoints = [
    ("/api/agents", "Agents list"),
    ("/api/suggestions/unscoped", "Unscoped suggestions"),
    ("/api/files/user_input", "User input"),
    ("/api/files/tasks", "Tasks"),
    ("/api/files/worker_report", "Worker report"),
    ("/api/files/manager_feedback", "Manager feedback"),
    ("/api/files/manager_msg_user", "Manager message"),
    ("/api/files/work_status", "Work status"),
    ("/api/stream/manager", "Manager stream"),
    ("/api/stream/worker", "Worker stream"),
    ("/api/timer/manager", "Manager timer"),
    ("/api/timer/worker", "Worker timer"),
]
for path, name in endpoints:
    status, data = api_get(path)
    if status == 200:
        print(f"  [OK] {name}")
    else:
        print(f"  [FAIL] {name} (status={status}, data={data})")

print("\n[3/6] Testing save input...")
status, data = api_put("/api/files/user_input", {"content": "Test input from smoke test"})
if status == 200 and data.get("ok"):
    print("  [OK] Save input")
else:
    print(f"  [FAIL] Save input")

status, data = api_get("/api/files/user_input")
if status == 200 and data.get("content") == "Test input from smoke test":
    print("  [OK] Read back input")
else:
    print(f"  [FAIL] Read back input")

print("\n[4/6] Testing run cycle and loop endpoints...")
status, data = api_post("/api/run-cycle")
if status == 200 and data.get("ok"):
    print("  [OK] Run cycle")
else:
    print(f"  [FAIL] Run cycle")

status, data = api_get("/api/loop/status")
if status == 200 and "running" in data:
    print("  [OK] Loop status")
else:
    print(f"  [FAIL] Loop status (status={status}, data={data})")

print("\n[5/6] Testing main page (React SPA)...")
status, data = api_get("/")
failures = 0
if status == 200:
    html = data
    # React SPA serves a minimal HTML shell; all UI is rendered client-side by JS.
    # We only verify static markers present in the built index.html.
    checks = [
        (len(html) > 200,                            "Response is non-trivial HTML"),
        ("<html" in html.lower(),                    "Valid HTML document"),
        ('id="root"' in html or "id='root'" in html, "React root mount point (#root)"),
        (".js" in html or "assets/" in html,         "JS bundle referenced"),
    ]
    for check, name in checks:
        if check:
            print(f"  [FOUND] {name}")
        else:
            failures += 1
            print(f"  [MISSING] {name}")
    if len(html) < 10000:
        print(f"  [WARN] HTML is only {len(html)} bytes — frontend may not be built.")
        print("         Run: cd frontend && npm run build")
else:
    failures = 1
    print(f"  [FAIL] Main page (status={status})")

print("\n[6/6] Testing rendered agent data API...")
status, data = api_get("/api/agents")
if status == 200 and isinstance(data, list) and data:
    agent_names = {item.get("name") for item in data}
    if {"manager", "worker"}.issubset(agent_names) or {"Manager", "Worker"}.issubset(agent_names):
        print("  [OK] Manager/Worker agents available for UI rendering")
    else:
        print(f"  [WARN] Agents returned but expected manager/worker names missing: {agent_names}")
else:
    print(f"  [FAIL] Agents data unavailable (status={status}, data={data})")

print("\n[Cleanup] Stopping server...")
server_proc.terminate()
server_proc.wait(timeout=5)

print("\n" + "=" * 60)
print("SMOKE TEST COMPLETE")
print("=" * 60)
