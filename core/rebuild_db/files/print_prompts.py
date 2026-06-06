"""Print all prompt content for review, grouped by source file."""
import json
from pathlib import Path

ROOT = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams")
prompts = json.loads((ROOT / "core" / "rebuild_db" / "files" / "prompt_scan.json").read_text())

# Group by file, sorted by count
for p in sorted(prompts, key=lambda x: -x.get("prompt_count", 0))[:5]:
    print(f"\n{'='*80}\n{p['path']} ({p['prompt_count']} prompts)\n{'='*80}")
    for i, pr in enumerate(p["prompts"], 1):
        print(f"\n--- Prompt {i} (line {pr['line']}, {pr['length']} chars) ---")
        print(pr["full"][:1500])
        if len(pr["full"]) > 1500:
            print(f"\n... [{len(pr['full']) - 1500} more chars]")
