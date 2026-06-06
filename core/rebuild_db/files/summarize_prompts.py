"""Print summary of prompt scan."""
import json
from pathlib import Path

data = json.loads(
    Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core\rebuild_db\files\prompt_scan.json").read_text()
)
print("Files with prompts (sorted by count):")
for f in sorted(data, key=lambda x: -x.get("prompt_count", 0)):
    print(f"  {f['path']:65s} prompts={f['prompt_count']:3d}  lines={f.get('lines',0)}")
print(f"\nTotal: {sum(f.get('prompt_count', 0) for f in data)} prompts in {len(data)} files")
