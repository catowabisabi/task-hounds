"""Smoke test the new db layer."""
import os
os.environ["PYTHONPATH"] = r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core"
import sys
sys.path.insert(0, r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core")

from task_hounds_api.db import init_db, connect, DB_PATH
init_db()
print("DB_PATH:", DB_PATH)
with connect() as db:
    rows = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    tables = [r[0] for r in rows]
print(f"Tables ({len(tables)}):", tables)
