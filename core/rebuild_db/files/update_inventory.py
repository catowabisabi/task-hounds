"""Update inventory with process_status and completed flags.

Status semantics:
  pending    — file discovered, not yet analyzed
  analyzed   — file analyzed, remark + counts filled in
  migrated   — code moved to task_hounds_api/ (manual update)
  done       — file fully processed (migrated OR marked for deletion)
  error      — analysis failed
  fail       — analysis succeeded but discovered blockers
"""
import json
from pathlib import Path

ROOT = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams")
INV_PATH = ROOT / "core" / "rebuild_db" / "files" / "inventory.json"
inv = json.loads(INV_PATH.read_text(encoding="utf-8"))

# Files that have been analyzed
ANALYZED = {
    "core/api/fastapi_server.py",
    "core/api/server.py",
    "core/api/server_legacy.py",
    "core/api/model_validation.py",
    "core/api/services/legacy.py",
    "core/api/services/__init__.py",
    "core/power_teams/db.py",
    "core/power_teams/agents/base.py",
    "core/power_teams/agents/manager.py",
    "core/power_teams/agents/worker.py",
    "core/power_teams/agents/reviewer.py",
    "core/power_teams/agentic_workflows/flow_01/__init__.py",
    "core/power_teams/agentic_workflows/flow_01/interface.py",
    "core/power_teams/agentic_workflows/flow_01/graph.py",
    "core/power_teams/agentic_workflows/flow_01/workflow.py",
    "core/power_teams/agentic_workflows/flow_01/adapters.py",
    "core/power_teams/agentic_workflows/flow_01/constants.py",
    "core/power_teams/runtime/opencode_lifecycle.py",
    "core/power_teams/runtime/opencode_supervisor.py",
    "core/power_teams/runtime/opencode_binary.py",
    "core/power_teams/runtime/opencode_connect.py",
}

# Files where prompts have been extracted to agent_prompts/
PROMPTS_EXTRACTED = {
    "core/power_teams/agents/manager.py",
    "core/power_teams/agentic_workflows/flow_01/workflow.py",
    "core/power_teams/agentic_workflows/flow_01/graph.py",
    "core/api/fastapi_server.py",
    "core/power_teams/agents/base.py",
}

# Files marked for deletion (no migration needed)
DELETE = {
    "core/power_teams/runtime/backends/hermes.py",
    "core/power_teams/runtime/backends/openclaw.py",
    "core/power_teams/runtime/backend_registry.py",
    "core/power_teams/runtime/backends/base.py",
    "core/power_teams/integrations/__init__.py",
    "core/power_teams/integrations/base_provider.py",
    "core/power_teams/integrations/opencode_provider.py",
    "core/power_teams/integrations/opencode_cli_provider.py",
    "core/power_teams/agentic_workflows/flow_00_temp/__init__.py",
    "core/power_teams/agentic_workflows/flow_00_temp/interface.py",
    "core/power_teams/agentic_workflows/flow_00_temp/workflow.py",
    "core/power_teams/agentic_workflows/flow_00_temp/workflow-test/test_flow_01.py",
    "core/power_teams/agentic_workflows/flow_01/put_bigsmall_directive.py",
    "core/power_teams/agentic_workflows/flow_01/start_bigsmall_loop.py",
    "core/power_teams/agentic_workflows/flow_01/start_flow_01_api_test.py",
    "core/power_teams/api/services/legacy.py",
    "core/power_teams/api/server_legacy.py",
    "core/power_teams/api/server.py",
}

for f in inv["files"]:
    path = f["path"]
    if path in DELETE:
        f["process_status"] = "done"
        f["completed"] = True
        f["analyzed"] = True
        f["action"] = "delete"
    elif path in ANALYZED:
        f["process_status"] = "analyzed"
        f["analyzed"] = True
        f["completed"] = False
        f["action"] = "migrate"
        if path in PROMPTS_EXTRACTED:
            f["prompts_extracted"] = True
    else:
        f["process_status"] = "pending"
        f["analyzed"] = False
        f["completed"] = False
        f["action"] = "analyze"

# Recompute summary
inv["summary"]["analyzed_files"] = sum(1 for f in inv["files"] if f["analyzed"])
inv["summary"]["completed_files"] = sum(1 for f in inv["files"] if f["completed"])
inv["summary"]["delete_files"] = sum(1 for f in inv["files"] if f.get("action") == "delete")
inv["summary"]["migrate_files"] = sum(1 for f in inv["files"] if f.get("action") == "migrate")
inv["summary"]["pending_files"] = sum(1 for f in inv["files"] if f["process_status"] == "pending")
inv["summary"]["prompts_extracted"] = sum(1 for f in inv["files"] if f.get("prompts_extracted"))

INV_PATH.write_text(json.dumps(inv, indent=2, ensure_ascii=False), encoding="utf-8")

print("Updated inventory.json")
print(f"  Total:          {inv['summary']['total_files']}")
print(f"  Analyzed:       {inv['summary']['analyzed_files']}")
print(f"  Completed:      {inv['summary']['completed_files']}")
print(f"  To delete:      {inv['summary']['delete_files']}")
print(f"  To migrate:     {inv['summary']['migrate_files']}")
print(f"  Still pending:  {inv['summary']['pending_files']}")
print(f"  Prompts extracted: {inv['summary']['prompts_extracted']}")
