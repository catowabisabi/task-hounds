"""Check workflow_runs table and try list_workflow_runs."""
import os
os.environ['PYTHONPATH'] = r'C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core'
import sys
sys.path.insert(0, r'C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core')

from task_hounds_api.db import connect
with connect() as db:
    rows = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='workflow_runs'").fetchall()
    print('workflow_runs table exists:', len(rows) > 0)
    if not rows:
        all_tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        print('Tables in DB:', all_tables)
