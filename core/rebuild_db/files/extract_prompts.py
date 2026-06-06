"""Extract real agent prompts to agent_prompts/ .md files.

Categorizes:
  - manager_prompts.md     — from manager.py
  - manager_step_prompts.md — from flow_01/graph.py (StepwiseManager)
  - manager_v2_prompts.md  — from flow_01/workflow.py (OpenCodeManagerExecutor)
  - worker_prompts.md      — from flow_01/workflow.py (OpenCodeWorkerExecutor)
  - reviewer_prompts.md    — from flow_01/workflow.py (OpenCodeReviewerExecutor)
  - chat_prompts.md        — from fastapi_server.py
  - system_principles.md   — TOOL_FIRST_PRINCIPLE etc.
"""
import json
import re
from pathlib import Path

ROOT = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams")
PROMPTS = json.loads((ROOT / "core" / "rebuild_db" / "files" / "prompt_scan.json").read_text())
OUT_DIR = ROOT / "core" / "task_hounds_api" / "agent_prompts"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def is_real_prompt(text: str) -> bool:
    """Filter out SQL and other false positives."""
    upper = text.upper()
    sql_markers = ["CREATE TABLE", "INSERT INTO", "SELECT ", "UPDATE ", "DELETE FROM", "ALTER TABLE"]
    if any(m in upper for m in sql_markers):
        return False
    return True


# Group prompts by destination
buckets = {
    "manager_prompts.md": [],
    "manager_step_prompts.md": [],
    "manager_v2_prompts.md": [],
    "worker_prompts.md": [],
    "reviewer_prompts.md": [],
    "chat_prompts.md": [],
    "system_principles.md": [],
}

for entry in PROMPTS:
    path = entry["path"]
    for pr in entry["prompts"]:
        text = pr["full"]
        if not is_real_prompt(text):
            continue
        location = f"`{path}` line {pr['line']}"
        if path == "core/power_teams/agents/manager.py":
            buckets["manager_prompts.md"].append((location, text))
        elif path == "core/power_teams/agentic_workflows/flow_01/graph.py":
            buckets["manager_step_prompts.md"].append((location, text))
        elif path == "core/power_teams/agentic_workflows/flow_01/workflow.py":
            # Split by role based on first line
            lower = text.lower()
            if "you are the worker" in lower:
                buckets["worker_prompts.md"].append((location, text))
            elif "you are the reviewer" in lower:
                buckets["reviewer_prompts.md"].append((location, text))
            else:
                buckets["manager_v2_prompts.md"].append((location, text))
        elif path == "core/api/fastapi_server.py":
            if "chat" in text.lower():
                buckets["chat_prompts.md"].append((location, text))
        elif path == "core/power_teams/agents/base.py":
            buckets["system_principles.md"].append((location, text))


# Add system_principles manually from agents/base.py TOOL_FIRST_PRINCIPLE constant
base_src = (ROOT / "core" / "power_teams" / "agents" / "base.py").read_text(encoding="utf-8")
match = re.search(r'TOOL_FIRST_PRINCIPLE = \((.*?)\)\s*\n', base_src, re.DOTALL)
if match:
    principle_text = match.group(1).strip().strip('"')
    buckets["system_principles.md"].insert(
        0,
        (
            "`core/power_teams/agents/base.py` (constant `TOOL_FIRST_PRINCIPLE`)",
            principle_text,
        ),
    )


# Write each .md
def write_md(name, items):
    out = OUT_DIR / name
    title = name.replace(".md", "").replace("_", " ").title()
    lines = [f"# {title}", ""]
    if not items:
        lines.append("_(no prompts yet)_")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    for i, (loc, text) in enumerate(items, 1):
        lines.append(f"## Prompt {i}")
        lines.append("")
        lines.append(f"**Source:** {loc}")
        lines.append("")
        lines.append("```")
        lines.append(text.strip())
        lines.append("```")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")


for name, items in buckets.items():
    write_md(name, items)
    print(f"  {name:35s} {len(items)} prompts")

print(f"\nWrote to {OUT_DIR}")
print(f"Total prompts moved: {sum(len(v) for v in buckets.values())}")
