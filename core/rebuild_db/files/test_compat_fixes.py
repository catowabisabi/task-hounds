"""Verify schema migration + chat 422 fix."""
import os
os.environ['PYTHONPATH'] = r'C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core'
import sys
sys.path.insert(0, r'C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core')

# Force re-init the dev DB
import shutil
from pathlib import Path
DB_PATH = Path(r'C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core\db\power_teams.db')
for ext in ['', '-shm', '-wal']:
    f = DB_PATH.parent / (DB_PATH.name + ext)
    if f.exists():
        try:
            f.unlink()
            print(f"Removed {f.name}")
        except Exception as e:
            print(f"Could not remove {f.name}: {e}")

from task_hounds_api.db import init_db, connect
init_db()

with connect() as db:
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    print(f"\nTotal tables: {len(tables)}")
    print("workflow_runs present:", "workflow_runs" in tables)
    print("flow_checkpoints present:", "flow_checkpoints" in tables)

# Test the API endpoints
from fastapi.testclient import TestClient
from task_hounds_api.api import create_app
app = create_app()
client = TestClient(app)

# Need to create a project session first
from task_hounds_api.db.ops import project as db_project
sid = "ps_test_compat"
db_project.create_session(sid, "C:/tmp", name="test")
db_project.activate_session(sid)

print("\n=== Test /api/chat/messages (was 422) ===")
r = client.get("/api/chat/messages")
print(f"  Status: {r.status_code}, body: {r.json()[:1] if isinstance(r.json(), list) else r.json()}")

print("\n=== Test /api/workflows/flow_01/runs (was 500) ===")
r = client.get("/api/workflows/flow_01/runs?limit=1")
print(f"  Status: {r.status_code}, body: {r.json()}")

print("\n=== Test /api/workflows/flow_01/plan ===")
r = client.get("/api/workflows/flow_01/plan")
print(f"  Status: {r.status_code}, body: {r.json()}")

print("\n=== Test /api/workflows/flow_01/todos ===")
r = client.get("/api/workflows/flow_01/todos")
print(f"  Status: {r.status_code}, body: {r.json()}")

print("\n=== Test /api/user-input/has-content ===")
r = client.get("/api/user-input/has-content")
print(f"  Status: {r.status_code}, body: {r.json()}")

print("\n=== Test /api/directive/status ===")
r = client.get("/api/directive/status")
print(f"  Status: {r.status_code}, body: {r.json()}")

print("\n=== Test /api/stream/manager ===")
r = client.get("/api/stream/manager")
print(f"  Status: {r.status_code}, body: {r.json()}")
