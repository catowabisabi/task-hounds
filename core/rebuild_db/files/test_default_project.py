"""Test default project creation on startup."""
import os
import sys
import shutil
from pathlib import Path

ROOT = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams")
DB_PATH = ROOT / "core" / "db" / "power_teams.db"

# Wipe the DB to simulate first run
for ext in ["", "-shm", "-wal"]:
    f = DB_PATH.parent / (DB_PATH.name + ext)
    if f.exists():
        try:
            f.unlink()
            print(f"Removed {f.name}")
        except Exception as e:
            print(f"Could not remove {f.name}: {e}")

# Wipe default project folder if exists
default = Path("C:/task-hounds-projects/default-project")
if default.exists():
    shutil.rmtree(default.parent, ignore_errors=True)
    print(f"Removed {default.parent}")

# Reset opencode config cache
sys.path.insert(0, str(ROOT / "core"))
os.environ["PYTHONPATH"] = str(ROOT / "core")

# Force fresh import
for mod in list(sys.modules.keys()):
    if mod.startswith("task_hounds_api"):
        del sys.modules[mod]

# Import and create
print("\n=== Importing task_hounds_api ===")
from task_hounds_api.api import create_app

print("=== Calling create_app() - first time, no projects exist ===")
app = create_app()

# Check default project was created
from task_hounds_api.db.ops import project as db_project
sessions = db_project.list_sessions()
print(f"\nProjects in DB: {len(sessions)}")
for s in sessions:
    print(f"  - {s['id']}: {s['name']} @ {s['workspace_path']}")

active = db_project.get_active_session()
print(f"\nActive session: {active['id'] if active else None}")

# Check folder was created
if default.exists():
    print(f"\nDefault folder created at: {default}")
else:
    print(f"\nWARNING: default folder not created")

# Second call - should NOT re-create
print("\n=== Second create_app() call - should be idempotent ===")
app2 = create_app()
sessions2 = db_project.list_sessions()
print(f"Projects after 2nd call: {len(sessions2)}")
