"""Find unassigned files."""
import json
from pathlib import Path

inv = json.loads(Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core\rebuild_db\files\inventory.json").read_text())
for f in inv["files"]:
    if f.get("target_layer") == "unassigned":
        print(f["path"])
