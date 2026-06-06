"""Debug: show what audit sees vs what's actually registered."""
import sys
from pathlib import Path
from collections import defaultdict
import re

ROOT_UI = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\ui\web\src")
ROOT_CORE = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core")
sys.path.insert(0, str(ROOT_CORE))

api_calls = defaultdict(set)
for f in ROOT_UI.rglob("*.ts*"):
    src = f.read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r'\b(apiGet|apiPost|apiPut|apiPatch|apiDelete|fetch)\b', src):
        verb = m.group(1)
        method = {"apiGet":"GET","apiPost":"POST","apiPut":"PUT","apiPatch":"PATCH","apiDelete":"DELETE","fetch":"FETCH"}.get(verb, "")
        if not method:
            continue
        idx = m.end()
        snippet = src[idx:idx+500]
        for path_m in re.finditer(r'[`"\'](/api/[^`"\']+)[`"\']', snippet):
            path = path_m.group(1)
            path_norm = re.sub(r"\$\{[^}]+\}", "{id}", path)
            api_calls[(method, path_norm)].add(str(f.relative_to(ROOT_UI.parent.parent)))

from task_hounds_api.api import create_app
app = create_app()
registered = defaultdict(set)
for r in app.routes:
    if hasattr(r, "methods") and hasattr(r, "path"):
        for m in r.methods - {"HEAD", "OPTIONS"}:
            registered[r.path].add(m)

# Check some specific paths
print("=== Server has these chat routes: ===")
for p in sorted(registered.keys()):
    if "chat" in p:
        print(f"  {sorted(registered[p])} {p}")

print("\n=== UI calls these chat endpoints: ===")
for (method, path), sources in sorted(api_calls.items()):
    if "chat" in path:
        print(f"  {method:6s} {path}")

# Manual check
print("\n=== Manual check: does registered contain '/api/chat/messages'? ===")
print(f"  registered has /api/chat/messages: {'/api/chat/messages' in registered}")
print(f"  methods: {sorted(registered.get('/api/chat/messages', set()))}")

# Check audit logic for (GET, /api/chat/messages)
target = ("GET", "/api/chat/messages")
print(f"\n=== Audit logic for {target} ===")
method, path = target
found_path = None
for server_path, server_methods in registered.items():
    path_pattern = path.replace("{id}", r"[^/]+")
    if re.match(f"^{path_pattern}$", server_path):
        found_path = server_path
        if method in server_methods:
            found_path = None
            print(f"  Found match: {server_path} with {method}")
            break
        else:
            print(f"  Path match but wrong method: {server_path} has {sorted(server_methods)}")
print(f"  Final found_path: {found_path!r}")
if not found_path and found_path != "":
    print("  >>> REPORTED AS MISSING")
