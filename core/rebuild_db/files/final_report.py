"""Print final inventory summary."""
import json
from pathlib import Path

inv = json.loads(
    Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core\rebuild_db\files\inventory.json").read_text()
)
s = inv["summary"]
print("=" * 60)
print("TASK HOUNDS REBUILD — INVENTORY REPORT")
print("=" * 60)
print(f"Total files:         {s['total_files']}")
print(f"Total lines:         {s['total_lines']:,}")
print(f"Total functions:     {s['total_functions']}")
print(f"Total classes:       {s['total_classes']}")
print(f"Total prompts:       {s['total_prompts']} (raw scan)")
print()
print("BY TARGET LAYER:")
total_lines_by_layer = {}
for f in inv["files"]:
    layer = f.get("target_layer") or "unassigned"
    total_lines_by_layer[layer] = total_lines_by_layer.get(layer, 0) + f["lines"]
for k, v in s["by_target_layer"].items():
    print(f"  {k:12s} {v:3d} files  {total_lines_by_layer.get(k, 0):>7,} lines")
print()
print("PROCESS STATUS:")
print(f"  Analyzed:          {s['analyzed_files']}")
print(f"  Completed:         {s['completed_files']}")
print(f"  To delete:         {s['delete_files']}")
print(f"  To migrate:        {s['migrate_files']}")
print(f"  Still pending:     {s['pending_files']}")
print(f"  Prompts extracted: {s['prompts_extracted']}")
