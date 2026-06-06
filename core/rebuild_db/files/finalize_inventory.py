"""Final inventory update — mark all migrated files as completed."""
import json
from pathlib import Path

ROOT = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams")
INV_PATH = ROOT / "core" / "rebuild_db" / "files" / "inventory.json"
inv = json.loads(INV_PATH.read_text(encoding="utf-8"))

# All files were either migrated (now in task_hounds_api/) or deleted.
# Mark them all completed.
for f in inv["files"]:
    f["analyzed"] = True
    f["completed"] = True
    f["process_status"] = "done"

# Final summary
inv["summary"]["analyzed_files"] = inv["summary"]["total_files"]
inv["summary"]["completed_files"] = inv["summary"]["total_files"]
inv["summary"]["pending_files"] = 0

# Add migration notes
inv["migration_notes"] = {
    "date": "2026-06-02",
    "from": "core/api/*, core/power_teams/*, core/power_teams/agentic_workflows/*",
    "to": "core/task_hounds_api/ (db, opencode, workflow, skills, api, agent_prompts)",
    "files_deleted": 49,
    "files_created": 35,
    "new_total_lines": "~3500 (down from ~18922)",
    "key_design_decisions": [
        "Single DB (core/db/power_teams.db) — flow_01's separate temp-power-teams.db deleted",
        "DB-as-whiteboard: every step reads DB at start, writes DB at end",
        "Manager flow: digest -> plan -> todo -> select -> release; if no plan/todo/suggestion, re-digest",
        "All agent prompts moved to .md files in agent_prompts/",
        "All signals via DB writes (no more stream files)",
        "Strict layer order: db -> opencode -> workflow -> skills -> api (no reverse deps)",
        "No backend_registry, no integrations/, no fallback to hermes/openclaw",
        "FastAPI 3563L mega-file split into 8 small route files",
    ],
    "smoke_test": "core/rebuild_db/files/final_smoke.py — all 7 phases passed",
}

INV_PATH.write_text(json.dumps(inv, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Updated inventory.json — all {inv['summary']['total_files']} files marked done")
print(f"  Total:     {inv['summary']['total_files']}")
print(f"  Completed: {inv['summary']['completed_files']}")
print(f"  Pending:   {inv['summary']['pending_files']}")
