"""Final smoke test after session_id simplification."""
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
from task_hounds_api.db.ops import project as db_project

app = create_app()
client = TestClient(app)

# Set up active session
sid = "ps_test_unified"
db_project.create_session(sid, "C:/tmp", name="unified test")
db_project.activate_session(sid)

# Test ALL routes without session_id (should all default to active)
endpoints = [
    ("GET",  "/api/chat/messages"),
    ("GET",  "/api/todos"),
    ("GET",  "/api/workflow/plan"),
    ("GET",  "/api/workflow/suggestion"),
    ("GET",  "/api/workflow/reports"),
    ("GET",  "/api/workflow/manager-messages"),
    ("GET",  "/api/workflow/handoff"),
    ("GET",  "/api/workflow/directives"),
    ("GET",  "/api/stream/manager"),
    ("GET",  "/api/timer/manager"),
    ("GET",  "/api/workflows/flow_01/plan"),
    ("GET",  "/api/workflows/flow_01/todos"),
    ("GET",  "/api/workflows/flow_01/suggestion"),
    ("GET",  "/api/workflows/flow_01/reports"),
    ("GET",  "/api/workflows/flow_01/runs?limit=1"),
    ("GET",  "/api/workflows/flow_01/manager-messages"),
    ("GET",  "/api/workflows/flow_01/handoff"),
    ("GET",  "/api/user-input/has-content"),
    ("GET",  "/api/directive/status"),
    ("GET",  "/api/files/user_input"),
    ("GET",  "/api/manager-messages"),
    ("GET",  "/api/suggestion"),
    ("GET",  "/api/handoff"),
    ("GET",  "/api/plan"),
    ("GET",  "/api/workspaces"),
]

print(f"Testing {len(endpoints)} endpoints (all should be 200):")
passed = 0
failed = []
for method, path in endpoints:
    r = client.request(method, path)
    if r.status_code == 200:
        passed += 1
    else:
        failed.append((path, r.status_code, r.text[:100]))

print(f"\nResult: {passed}/{len(endpoints)} passed")
if failed:
    print("Failures:")
    for path, code, text in failed:
        print(f"  {code} {path}: {text}")

# Now test what happens with no active session
print("\n=== Test: no active session ===")
with DB_PATH.parent.joinpath(DB_PATH.name + "-shm").open("rb") as f:
    pass  # warm

# Deactivate the session by deleting it... actually we can't easily do that without resetting
# Just verify that the helpers raise properly when no active session
from task_hounds_api.api.deps import resolve_session_id, get_active_session_id
try:
    # create a separate client with no active session
    from task_hounds_api.db import init_db, connect
    init_db()
    # Delete the active session
    with connect() as db:
        db.execute("UPDATE project_sessions SET is_active=0")
        db.commit()
    r = client.get("/api/chat/messages")
    print(f"  /api/chat/messages with no active: {r.status_code} (expected 400)")
except Exception as e:
    print(f"  Could not test: {e}")
