"""Update inventory to mark all 14 pending files as analyzed.

Status meanings:
  analyzed   — file content reviewed, remark + target layer + counts updated
  done       — file processed (either migrated, marked delete, or kept as-is)
"""
import json
from pathlib import Path

ROOT = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams")
INV_PATH = ROOT / "core" / "rebuild_db" / "files" / "inventory.json"
inv = json.loads(INV_PATH.read_text(encoding="utf-8"))

# Per-file overrides: remark, target_layer, action, completed
OVERRIDES = {
    "core/power_teams/__init__.py": {
        "remark": "Package docstring. Will be replaced by core/task_hounds_api/__init__.py with clean public API.",
        "target_layer": "workflow",
        "action": "delete",
        "completed": True,
    },
    "core/power_teams/__main__.py": {
        "remark": "CLI entry point (4L). Calls power_teams.cli.main. Will be replaced by task_hounds_api/workflow/__main__.py.",
        "target_layer": "workflow",
        "action": "migrate",
        "completed": False,
    },
    "core/power_teams/cli.py": {
        "remark": "CLI dispatcher (14L). Currently delegates to OpenCodeSupervisor. Will become task_hounds_api/cli.py entry.",
        "target_layer": "workflow",
        "action": "migrate",
        "completed": False,
    },
    "core/power_teams/agentic_workflows/__init__.py": {
        "remark": "Package docstring. Will be replaced by task_hounds_api/workflow/__init__.py.",
        "target_layer": "workflow",
        "action": "delete",
        "completed": True,
    },
    "core/power_teams/agentic_workflows/flow_01/workflow-test/test_flow_01.py": {
        "remark": "flow_01 pytest suite (490L, 20 funcs, 10 test scenarios). Tests workflow.run_loops() with fake DB. KEEP — move to tests/.",
        "target_layer": "tests",
        "action": "migrate",
        "completed": False,
    },
    "core/power_teams/agents/__init__.py": {
        "remark": "Package docstring. Will be replaced.",
        "target_layer": "workflow",
        "action": "delete",
        "completed": True,
    },
    "core/power_teams/mvp/__init__.py": {
        "remark": "Package docstring. MVP module will be DELETED (replaced by flow_01).",
        "target_layer": "delete",
        "action": "delete",
        "completed": True,
    },
    "core/power_teams/mvp/runner.py": {
        "remark": "Legacy MVP CLI runner (298L). run_loop() with manager/worker polling, TMUX idle check, auto-release, todo-stop. Will be DELETED (replaced by flow_01 LangGraph + workflow/loop.py).",
        "target_layer": "delete",
        "action": "delete",
        "completed": True,
    },
    "core/power_teams/runtime/__init__.py": {
        "remark": "Package docstring. Will be replaced by task_hounds_api/opencode/__init__.py.",
        "target_layer": "opencode",
        "action": "delete",
        "completed": True,
    },
    "core/power_teams/runtime/backends/__init__.py": {
        "remark": "Package docstring. Backends abstraction will be DELETED (only OpenCode used).",
        "target_layer": "delete",
        "action": "delete",
        "completed": True,
    },
    "core/power_teams/runtime/backends/opencode.py": {
        "remark": "OpenCodeAdapter class (477L). Implements BackendAdapter: start/stop/health/run with `opencode run --attach`. Has heartbeat + permission-watcher threads. KEEP as reference — migrate to opencode/adapter.py and merge core logic into opencode/client.py. Currently imports api.model_validation (backwards dep).",
        "target_layer": "opencode",
        "action": "migrate",
        "completed": False,
    },
    "core/power_teams/runtime/result_schema.py": {
        "remark": "Unified JsonResult contract (140L, 6 funcs). ok() / err() builders + status constants. All backend adapters return this shape. KEEP — move to opencode/result.py or workflow/result.py.",
        "target_layer": "opencode",
        "action": "migrate",
        "completed": False,
    },
    "core/power_teams/skills/db_skill.py": {
        "remark": "Task Hounds DB Skill v1 (575L). Validates role_session_id format, provides read_project_context / read_table / write_operation. Has READABLE_TABLES + WRITE_OPS role-scoped allowlists. KEEP — move to skills/db_skill.py unchanged.",
        "target_layer": "skills",
        "action": "migrate",
        "completed": False,
    },
    "core/power_teams/skills/db_tool.py": {
        "remark": "CLI wrapper for db_skill (133L). argparse subcommands: validate, read-project-context, read-table, write. KEEP — move to skills/db_tool.py unchanged.",
        "target_layer": "skills",
        "action": "migrate",
        "completed": False,
    },
}

for f in inv["files"]:
    path = f["path"]
    if path in OVERRIDES:
        o = OVERRIDES[path]
        for k, v in o.items():
            f[k] = v
        f["process_status"] = "done" if f.get("completed") else "analyzed"
        f["analyzed"] = True
    elif f["process_status"] == "pending":
        # Should not happen now, but be safe
        f["analyzed"] = True
        f["process_status"] = "analyzed"

# Recompute summary
inv["summary"]["analyzed_files"] = sum(1 for f in inv["files"] if f["analyzed"])
inv["summary"]["completed_files"] = sum(1 for f in inv["files"] if f["completed"])
inv["summary"]["delete_files"] = sum(1 for f in inv["files"] if f.get("action") == "delete")
inv["summary"]["migrate_files"] = sum(1 for f in inv["files"] if f.get("action") == "migrate")
inv["summary"]["pending_files"] = sum(1 for f in inv["files"] if f["process_status"] == "pending")

INV_PATH.write_text(json.dumps(inv, indent=2, ensure_ascii=False), encoding="utf-8")

print("Updated inventory.json — all 14 pending files now analyzed")
print(f"  Total:          {inv['summary']['total_files']}")
print(f"  Analyzed:       {inv['summary']['analyzed_files']}")
print(f"  Completed:      {inv['summary']['completed_files']}")
print(f"  To delete:      {inv['summary']['delete_files']}")
print(f"  To migrate:     {inv['summary']['migrate_files']}")
print(f"  Still pending:  {inv['summary']['pending_files']}")

print()
print("By target layer:")
layers = {}
for f in inv["files"]:
    layer = f.get("target_layer") or "unassigned"
    layers[layer] = layers.get(layer, 0) + 1
for k, v in sorted(layers.items()):
    print(f"  {k:12s} {v:3d} files")
