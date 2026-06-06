"""Audit: extract all unique API endpoints called by the React UI.

Then check each against the registered FastAPI routes.
"""
import re
import sys
from pathlib import Path
from collections import defaultdict

ROOT_UI = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\ui\web\src")
ROOT_CORE = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core")
sys.path.insert(0, str(ROOT_CORE))

# 1. Extract all unique API calls from the UI
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
            # Strip query string and template-literal ${} for fair matching
            path = path.split("?")[0].split("`")[0]
            path_norm = re.sub(r"\$\{[^}]+\}", "{id}", path)
            api_calls[(method, path_norm)].add(str(f.relative_to(ROOT_UI.parent.parent)))

# 2. Extract registered routes from FastAPI
from task_hounds_api.api import create_app
app = create_app()
registered = defaultdict(set)
for r in app.routes:
    if hasattr(r, "methods") and hasattr(r, "path"):
        for m in r.methods - {"HEAD", "OPTIONS"}:
            registered[r.path].add(m)

print(f"UI calls {len(api_calls)} unique API endpoints")
print(f"Server has {sum(len(v) for v in registered.values())} registered route+method combos\n")

print("=" * 80)
print("PROBLEMS")
print("=" * 80)

problems = []
for (method, path), sources in sorted(api_calls.items()):
    found_path = None
    matched = False
    for server_path, server_methods in registered.items():
        path_pattern = path.replace("{id}", r"[^/]+")
        if re.match(f"^{path_pattern}$", server_path):
            found_path = server_path
            if method in server_methods:
                matched = True
                break
    if not matched and not found_path:
        # Path doesn't exist
        problems.append((method, path, None, "missing", sources))
    elif found_path and not matched and method not in registered.get(found_path, set()):
        problems.append((method, path, found_path, "wrong_method", sources))

for method, path, found_path, issue, sources in problems:
    arrow = "  ? " if issue == "missing" else "  ~ "
    print(f"{arrow}{method:6s} {path}")
    if found_path and issue == "wrong_method":
        print(f"        Path exists: {found_path}")
        print(f"        Server allows: {sorted(registered[found_path])}")
    for s in sorted(sources)[:3]:
        print(f"        in: {s}")
    print()

if not problems:
    print("  All UI endpoints are registered correctly!")
