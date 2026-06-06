"""Debug target layer detection."""
import json
from pathlib import Path

ROOT = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams")
REBUILD = ROOT / "core" / "rebuild_db" / "files"

DEFS = json.loads((REBUILD / "all_definitions.json").read_text(encoding="utf-8"))

# Print first 3 paths
for d in DEFS[:5]:
    print(repr(d["path"]))

# Check against prefixes
TARGET_LAYER = {
    "core/api/": "api",
    "core/power_teams/agents/": "workflow",
    "core/power_teams/mvp/": "workflow",
    "core/power_teams/runtime/": "opencode",
    "core/power_teams/agentic_workflows/flow_01/": "workflow",
    "core/power_teams/agentic_workflows/flow_00_temp/": "delete",
    "core/power_teams/integrations/": "delete",
    "core/power_teams/db.py": "db",
    "core/power_teams/skills/": "skills",
}

# Check first 5
for d in DEFS[:5]:
    p = d["path"]
    target = None
    for prefix, layer in TARGET_LAYER.items():
        if p.startswith(prefix):
            target = layer
            break
    print(f"{p:60s} -> {target}")
