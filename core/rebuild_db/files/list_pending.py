"""List the pending files (process_status == 'pending')."""
import json
from pathlib import Path

inv = json.loads(
    Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core\rebuild_db\files\inventory.json").read_text()
)
for f in inv["files"]:
    if f["process_status"] == "pending":
        print(f"{f['path']:75s} {f['lines']:5d}L  funcs={f['counts']['functions']:3d}  classes={f['counts']['classes']:3d}")
