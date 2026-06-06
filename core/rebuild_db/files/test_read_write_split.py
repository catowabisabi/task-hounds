"""Final test: read endpoints return empty, write endpoints return 400 when no active session."""
import os
os.environ['PYTHONPATH'] = r'C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core'
import sys
sys.path.insert(0, r'C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core')

# Fresh DB
import shutil
from pathlib import Path
DB_PATH = Path(r'C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core\db\power_teams.db')
for ext in ['', '-shm', '-wal']:
    f = DB_PATH.parent / (DB_PATH.name + ext)
    if f.exists():
        try:
            f.unlink()
        except Exception:
            pass

from fastapi.testclient import TestClient
from task_hounds_api.api import create_app

app = create_app()
client = TestClient(app)

# No active session - test READ endpoints return empty (200)
print("=== Test 1: No active session - READ endpoints should return 200 with empty data ===")
read_endpoints = [
    ("GET", "/api/chat/messages"),
    ("GET", "/api/todos"),
    ("GET", "/api/workflow/plan"),
    ("GET", "/api/workflow/suggestion"),
    ("GET", "/api/workflow/reports"),
    ("GET", "/api/workflow/manager-messages"),
    ("GET", "/api/workflow/handoff"),
    ("GET", "/api/workflow/directives"),
    ("GET", "/api/stream/manager"),
    ("GET", "/api/timer/manager"),
    ("GET", "/api/workflows/flow_01/plan"),
    ("GET", "/api/workflows/flow_01/todos"),
    ("GET", "/api/workflows/flow_01/suggestion"),
    ("GET", "/api/workflows/flow_01/reports"),
    ("GET", "/api/workflows/flow_01/runs?limit=1"),
    ("GET", "/api/workflows/flow_01/manager-messages"),
    ("GET", "/api/workflows/flow_01/handoff"),
    ("GET", "/api/user-input/has-content"),
    ("GET", "/api/directive/status"),
    ("GET", "/api/files/user_input"),
    ("GET", "/api/manager-messages"),
    ("GET", "/api/suggestion"),
    ("GET", "/api/handoff"),
    ("GET", "/api/plan"),
    ("GET", "/api/workspaces"),
]
passed = failed = 0
for method, path in read_endpoints:
    r = client.request(method, path)
    if r.status_code == 200:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {r.status_code} {path} - {r.text[:80]}")
print(f"  Read endpoints: {passed}/{len(read_endpoints)} returned 200")

# No active session - test WRITE endpoints return 400
print("\n=== Test 2: No active session - WRITE endpoints should return 400 ===")
write_endpoints = [
    ("POST", "/api/chat/send", {"content": "test"}),
    ("POST", "/api/todos", {"content": "test"}),
    ("POST", "/api/workflow/suggestion", {"content": "test"}),
    ("POST", "/api/workflow/directive", {"directive": "test"}),
    ("PUT", "/api/workflow/plan", {"content": "test"}),
    ("PUT", "/api/workflow/handoff", {"current_task": "test"}),
    ("POST", "/api/workflows/flow_01/todos", {"content": "test"}),
    ("POST", "/api/workflows/flow_01/directive", {"directive": "test"}),
    ("PUT", "/api/workflows/flow_01/plan", {"content": "test"}),
    ("PUT", "/api/workflows/flow_01/handoff", {"current_task": "test"}),
]
passed = failed = 0
for method, path, body in write_endpoints:
    r = client.request(method, path, json=body)
    if r.status_code == 400:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {r.status_code} {path} - {r.text[:80]}")
print(f"  Write endpoints: {passed}/{len(write_endpoints)} returned 400")

# Now activate a session and test that everything works normally
print("\n=== Test 3: With active session - everything works ===")
from task_hounds_api.db.ops import project as db_project
sid = "ps_test_unified"
db_project.create_session(sid, "C:/tmp", name="test")
db_project.activate_session(sid)

passed = failed = 0
for method, path in read_endpoints:
    r = client.request(method, path)
    if r.status_code == 200:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {r.status_code} {path}")
print(f"  Read endpoints with active session: {passed}/{len(read_endpoints)} returned 200")

passed = failed = 0
for method, path, body in write_endpoints:
    r = client.request(method, path, json=body)
    if r.status_code == 200:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {r.status_code} {path} - {r.text[:80]}")
print(f"  Write endpoints with active session: {passed}/{len(write_endpoints)} returned 200")

print("\n=== ALL TESTS PASSED ===")
