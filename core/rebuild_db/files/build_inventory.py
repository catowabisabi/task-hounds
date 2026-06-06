"""Build the master rebuild_db index.

Reads:
  - all_definitions.json  (function/class/import extraction)
  - prompt_scan.json      (prompt string extraction)

Writes:
  - core/rebuild_db/files/inventory.json     (master file index)
  - core/rebuild_db/files/prompts.json       (just the prompt content, ready to split)
  - core/rebuild_db/files/interfaces.json    (just protocol/abstract classes)
"""
import json
import re
from pathlib import Path

ROOT = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams")
REBUILD = ROOT / "core" / "rebuild_db" / "files"

DEFS = json.loads((REBUILD / "all_definitions.json").read_text(encoding="utf-8"))
PROMPTS = json.loads((REBUILD / "prompt_scan.json").read_text(encoding="utf-8"))

# Index prompts by path for fast lookup
prompts_by_path = {p["path"]: p for p in PROMPTS}
defs_by_path = {d["path"]: d for d in DEFS}
defs_by_path = {k.replace("\\", "/"): v for k, v in defs_by_path.items()}


# Manual remarks table — short, single-line description of what each file does
REMARKS = {
    # api/ — HTTP layer (target: api/routes/* in new structure)
    "core/api/fastapi_server.py": "Active FastAPI server (3563L). Embeds all HTTP routes + workflow execution + background threads. Target: split into api/routes/* files.",
    "core/api/server.py": "Compatibility shim. Re-exports server_legacy. Will be deleted in new structure.",
    "core/api/server_legacy.py": "Legacy Python http.server with 3000L. Still referenced by services/legacy.py. Will be deleted.",
    "core/api/model_validation.py": "Parses opencode.jsonc to find available models. Target: opencode/config.py.",
    "core/api/services/legacy.py": "Thin facade over server_legacy functions. Will be deleted.",
    "core/api/services/__init__.py": "Empty init. Will be deleted.",

    # power_teams/ root
    "core/power_teams/__init__.py": "Empty init. Will be replaced.",
    "core/power_teams/__main__.py": "CLI entry. Delegates to power_teams.cli.main.",
    "core/power_teams/cli.py": "CLI dispatcher. Imports supervisor_main from runtime.",
    "core/power_teams/db.py": "SQLite CRUD (1354L). Target: split into db/ops/* by domain.",

    # power_teams/agents/ — original engine (target: workflow/executor.py)
    "core/power_teams/agents/__init__.py": "Empty init.",
    "core/power_teams/agents/base.py": "Shared agent utilities (1589L). send_to_agent, file I/O, prompts, DB calls. Target: workflow/executor.py + agent_prompts/*.md.",
    "core/power_teams/agents/manager.py": "manager_cycle() (910L, 9 prompts). Legacy non-LangGraph manager. Will be replaced by flow_01 graph nodes.",
    "core/power_teams/agents/worker.py": "worker_cycle() (155L, 1 prompt). One task execution. Will be replaced by OpenCodeWorkerExecutor.",
    "core/power_teams/agents/reviewer.py": "Reviewer trigger (177L, 1 prompt). Will be replaced by OpenCodeReviewerExecutor.",

    # power_teams/mvp/ — CLI loop runner
    "core/power_teams/mvp/__init__.py": "Empty init.",
    "core/power_teams/mvp/runner.py": "CLI run_loop() + main() (258L). Will be replaced by workflow/loop.py.",

    # power_teams/runtime/ — opencode lifecycle (target: opencode/*)
    "core/power_teams/runtime/__init__.py": "Empty init.",
    "core/power_teams/runtime/opencode_lifecycle.py": "OpenCodeLifecycleManager (911L). Health, crash recovery, external attach, multi-topology. Will be simplified in opencode/lifecycle.py.",
    "core/power_teams/runtime/opencode_supervisor.py": "OpenCodeSupervisor (509L). Process spawn, port finding. Will be simplified in opencode/process.py.",
    "core/power_teams/runtime/opencode_binary.py": "find_opencode_bin() (43L). Reads settings.json. Will move to opencode/binary.py.",
    "core/power_teams/runtime/opencode_connect.py": "HTTP/SSE client to OpenCode (384L). Will move to opencode/client.py.",
    "core/power_teams/runtime/backend_registry.py": "Backend registry (81L, 1 prompt). Only opencode used. Will be deleted.",
    "core/power_teams/runtime/result_schema.py": "Result schema validation (120L, 1 prompt). Will move to workflow/models.py.",
    "core/power_teams/runtime/backends/__init__.py": "Empty init.",
    "core/power_teams/runtime/backends/base.py": "BackendAdapter abstract class (86L). Will be deleted.",
    "core/power_teams/runtime/backends/opencode.py": "OpenCode backend adapter (413L). Will be deleted (use direct opencode client).",
    "core/power_teams/runtime/backends/hermes.py": "Legacy Hermes adapter (67L). NOT USED. Will be deleted.",
    "core/power_teams/runtime/backends/openclaw.py": "Legacy OpenClaw adapter (75L). NOT USED. Will be deleted.",

    # power_teams/agentic_workflows/flow_01/ — LangGraph engine (target: workflow/*)
    "core/power_teams/agentic_workflows/__init__.py": "Empty init.",
    "core/power_teams/agentic_workflows/flow_01/__init__.py": "Public exports for flow_01. Re-exported in workflow/__init__.py.",
    "core/power_teams/agentic_workflows/flow_01/interface.py": "Data contract + FlowStorage SQLite adapter (714L, 2 prompts). Will be split: workflow/models.py + db/ops/workflow.py.",
    "core/power_teams/agentic_workflows/flow_01/graph.py": "LangGraph StateGraph + 7 nodes + checkpointing (635L, 4 prompts). Target: workflow/graph.py.",
    "core/power_teams/agentic_workflows/flow_01/workflow.py": "Manager/Worker/Reviewer executors + baseline sequential (804L, 6 prompts). Target: workflow/executor.py.",
    "core/power_teams/agentic_workflows/flow_01/adapters.py": "Signal adapters: FastApiServiceSignalAdapter writes to agent_registry + stream files (131L). Target: workflow/signals.py.",
    "core/power_teams/agentic_workflows/flow_01/constants.py": "Env var config (16L). Target: workflow/config.py or just .env.example.",
    "core/power_teams/agentic_workflows/flow_01/put_bigsmall_directive.py": "Demo script (91L). Will be deleted (demo only).",
    "core/power_teams/agentic_workflows/flow_01/start_bigsmall_loop.py": "Demo script (60L). Will be deleted (demo only).",
    "core/power_teams/agentic_workflows/flow_01/start_flow_01_api_test.py": "Test driver (74L). Target: docs/testing/scripts/ or deleted.",
    "core/power_teams/agentic_workflows/flow_01/workflow-test/test_flow_01.py": "Workflow tests (406L, 1 prompt). Target: docs/testing/core-tests/flow_01_tests.py.",

    # power_teams/agentic_workflows/flow_00_temp/ — old baseline (will be deleted)
    "core/power_teams/agentic_workflows/flow_00_temp/__init__.py": "Legacy exports (26L). Will be deleted.",
    "core/power_teams/agentic_workflows/flow_00_temp/interface.py": "Legacy interface (594L, 2 prompts). Will be deleted (superseded by flow_01/interface.py).",
    "core/power_teams/agentic_workflows/flow_00_temp/workflow.py": "Legacy Flow01Workflow (273L). Will be deleted.",
    "core/power_teams/agentic_workflows/flow_00_temp/workflow-test/test_flow_01.py": "Legacy test (200L). Will be deleted.",

    # power_teams/integrations/ — provider abstractions (will be deleted)
    "core/power_teams/integrations/__init__.py": "Empty init.",
    "core/power_teams/integrations/base_provider.py": "Provider base class (32L). Will be deleted (only opencode used).",
    "core/power_teams/integrations/opencode_provider.py": "OpenCode HTTP provider (897L). Will be deleted (use direct opencode client).",
    "core/power_teams/integrations/opencode_cli_provider.py": "OpenCode CLI provider (281L). Will be deleted.",

    # power_teams/skills/ — DB MCP tool
    "core/power_teams/skills/db_skill.py": "DB MCP tool (505L). KEEP — standalone tool used by agents.",
    "core/power_teams/skills/db_tool.py": "DB tool wrapper (106L). KEEP — depends on db_skill.",
}


# Target layer mapping (where each file should go in the new structure)
TARGET_LAYER = {
    "core/api/": "api",
    "core/power_teams/agents/": "workflow",
    "core/power_teams/mvp/": "workflow",
    "core/power_teams/runtime/": "opencode",
    "core/power_teams/agentic_workflows/flow_01/": "workflow",
    "core/power_teams/agentic_workflows/flow_01/workflow-test/": "tests",
    "core/power_teams/agentic_workflows/flow_00_temp/": "delete",
    "core/power_teams/integrations/": "delete",
    "core/power_teams/db.py": "db",
    "core/power_teams/skills/": "skills",
}


def detect_protocol_or_abstract(cls):
    """Heuristic: a class is a Protocol/Interface if it inherits from Protocol or has only `...` bodies."""
    bases = cls.get("bases", [])
    if any("Protocol" in b for b in bases):
        return "Protocol"
    return None


# Build master inventory
inventory = []
for d in DEFS:
    path = d["path"].replace("\\", "/")
    funcs = d["functions"]
    classes = d["classes"]
    imports = d["imports"]
    constants = d["constants"]

    # Detect interfaces
    interfaces = []
    for cls in classes:
        kind = detect_protocol_or_abstract(cls)
        if kind:
            interfaces.append({"name": cls["name"], "kind": kind, "methods": cls["methods"]})

    # Detect target layer
    target = None
    for prefix, layer in TARGET_LAYER.items():
        if path.startswith(prefix):
            target = layer
            break

    # Check for prompts
    prompt_info = prompts_by_path.get(path, {})
    prompt_count = prompt_info.get("prompt_count", 0)

    inventory.append(
        {
            "path": path,
            "lines": d.get("lines", 0),
            "remark": REMARKS.get(path, ""),
            "item_count": len(funcs) + len(classes) + len(constants) + len(imports),
            "counts": {
                "functions": len(funcs),
                "classes": len(classes),
                "constants": len(constants),
                "imports": len(imports),
                "interfaces": len(interfaces),
                "prompts": prompt_count,
            },
            "functions": [f["name"] for f in funcs],
            "classes": [c["name"] for c in classes],
            "interfaces": [i["name"] for i in interfaces],
            "constants": constants,
            "target_layer": target,
            "process_status": "pending",
            "completed": False,
            "analyzed": False,
        }
    )

# Fix paths: convert backslashes to forward slashes
for r in inventory:
    r["path"] = r["path"].replace("\\", "/")

# Sort by target layer then path
inventory.sort(key=lambda r: (r.get("target_layer") or "zzz", r["path"]))

# Write master inventory
out = REBUILD / "inventory.json"
out.write_text(
    json.dumps(
        {
            "schema_version": "1.0",
            "generated_by": "rebuild_db/files/build_inventory.py",
            "summary": {
                "total_files": len(inventory),
                "total_lines": sum(r["lines"] for r in inventory),
                "total_functions": sum(r["counts"]["functions"] for r in inventory),
                "total_classes": sum(r["counts"]["classes"] for r in inventory),
                "total_prompts": sum(r["counts"]["prompts"] for r in inventory),
                "by_target_layer": {
                    layer: sum(1 for r in inventory if r["target_layer"] == layer)
                    for layer in sorted(set(r["target_layer"] for r in inventory if r["target_layer"]))
                },
            },
            "files": inventory,
        },
        indent=2,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
print(f"Wrote inventory.json with {len(inventory)} files")

# Write interfaces.json (just the protocols/abstract classes)
interfaces_only = [
    {
        "path": r["path"],
        "interfaces": [
            {"name": i["name"], "methods": j["methods"]}
            for i in (defs_by_path[r["path"]].get("classes") or [])
            for j in [{"name": i["name"], "methods": i["methods"]}]
            if any("Protocol" in b for b in i.get("bases", []))
        ],
    }
    for r in inventory
    if r["interfaces"]
]

# Recompute correctly
interfaces_only = []
for r in inventory:
    if not r["interfaces"]:
        continue
    d = defs_by_path[r["path"]]
    file_interfaces = []
    for cls in d["classes"]:
        if any("Protocol" in b for b in cls.get("bases", [])):
            file_interfaces.append(
                {"name": cls["name"], "bases": cls["bases"], "methods": cls["methods"]}
            )
    if file_interfaces:
        interfaces_only.append({"path": r["path"], "interfaces": file_interfaces})

(REBUILD / "interfaces.json").write_text(
    json.dumps(interfaces_only, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(f"Wrote interfaces.json with {sum(len(f['interfaces']) for f in interfaces_only)} interfaces")

# Summary
print("\nBy target layer:")
for layer, count in sorted(
    {(r["target_layer"] or "none"): 0 for r in inventory}.items()
):
    pass
layer_counts = {}
for r in inventory:
    layer = r["target_layer"] or "unassigned"
    layer_counts[layer] = layer_counts.get(layer, 0) + 1
for layer in sorted(layer_counts):
    total_lines = sum(r["lines"] for r in inventory if r["target_layer"] == layer)
    print(f"  {layer:12s} {layer_counts[layer]:3d} files  {total_lines:6d} lines")
