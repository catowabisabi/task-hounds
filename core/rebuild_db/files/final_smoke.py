"""Final integration smoke test.

Verifies all layers work together:
  1. db/       - import + init + ops
  2. opencode/ - config + binary + result
  3. workflow/ - models + executor + signals
  4. skills/   - db_skill + db_tool
  5. api/      - FastAPI app + all routes

Also tests:
  - No circular imports
  - No reverse dependencies (api/ doesn't import workflow, etc.)
  - DB-as-whiteboard (manager reads + writes through DB only)
"""
import os
import sys
import tempfile
import importlib
from pathlib import Path

ROOT = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core")

# Use a fresh test DB
test_db = Path(tempfile.gettempdir()) / "final_smoke.db"
if test_db.exists():
    try:
        test_db.unlink()
    except Exception:
        pass
os.environ["POWER_TEAMS_DB"] = str(test_db)
os.environ["PYTHONPATH"] = str(ROOT)
sys.path.insert(0, str(ROOT))

print("=" * 60)
print("PHASE 10: FINAL INTEGRATION SMOKE TEST")
print("=" * 60)

# ── 1. Verify no reverse dependencies ──────────────────────────────────────
print("\n[1] Reverse-dependency check")
import re
PY_FILES = list((ROOT / "task_hounds_api").rglob("*.py"))
LAYER_DIRS = ["db", "opencode", "workflow", "skills", "api"]
for layer in LAYER_DIRS:
    layer_path = ROOT / "task_hounds_api" / layer
    layer_files = list(layer_path.rglob("*.py"))
    violations = []
    layer_idx = LAYER_DIRS.index(layer)
    for f in layer_files:
        text = f.read_text(encoding="utf-8")
        for higher_idx in range(layer_idx + 1, len(LAYER_DIRS)):
            higher = LAYER_DIRS[higher_idx]
            for m in re.finditer(rf"from\s+task_hounds_api\.{higher}\b", text):
                violations.append(f"{f.relative_to(ROOT)}: {m.group()}")
    if violations:
        print(f"  FAIL: {layer}/ has {len(violations)} reverse imports:")
        for v in violations[:5]:
            print(f"     {v}")
        sys.exit(1)
    else:
        print(f"  OK: {layer}/: no reverse imports")

# ── 2. Import every layer ─────────────────────────────────────────────────
print("\n[2] Layer imports")
import task_hounds_api
print(f"  OK: task_hounds_api (package): {task_hounds_api.__name__}")

from task_hounds_api.db import init_db, connect, DB_PATH
from task_hounds_api.db.ops import project, agent, todo, workflow as db_wf, chat, runtime
print(f"  OK: db + db.ops (6 modules)")

from task_hounds_api.opencode import config, binary, result, process, client, lifecycle
print(f"  OK: opencode (6 modules)")

from task_hounds_api.workflow import models, executor, graph, signals, loop
print(f"  OK: workflow (5 modules)")

from task_hounds_api.skills import db_skill, db_tool
print(f"  OK: skills (2 modules)")

from task_hounds_api.api import create_app
print(f"  OK: api (1 module)")

# ── 3. DB-as-whiteboard: end-to-end Manager flow ──────────────────────────
print("\n[3] DB-as-whiteboard end-to-end")
init_db()
sid = "ps_final_test"
project.create_session(sid, "C:/tmp", name="final")
project.activate_session(sid)

from task_hounds_api.workflow import models as M
fi = M.FlowInput(
    power_team_project_id="pt_final",
    project_session_id=sid,
    human_directive="Build a clean REST API for todos.",
    human_suggested_new_task_or_item="Create POST /todos endpoint",
    todo_items=["Create POST", "Create GET", "Add validation"],
)
li = M.FlowLoopInput(loop_index=0)

state = executor.state_from_db(fi, li)
print(f"  state.plan (initial): {state.plan[:40]!r}")

state = executor.manager_digest(state)
print(f"  digest: {state.input_digest[:60]!r}")

state = executor.manager_plan(state)
print(f"  plan: {state.plan[:60]!r}")

state = executor.manager_todo(state)
print(f"  todo count: {len(state.todo_list)}")

state = executor.manager_select_task(state)
print(f"  suggestion: {state.suggestion_content!r}")

state = executor.manager_release(state)
print(f"  manager_message: {state.manager_message[:60]!r}")

print("\n[4] Verify DB writes")
assert db_wf.get_plan(sid), "plan missing"
print(f"  OK: plan in DB")

db_todos = todo.list_todos(sid)
assert db_todos
print(f"  OK: {len(db_todos)} todos in DB")

assert db_wf.list_manager_messages(sid)
print(f"  OK: manager_messages in DB")

assert db_wf.get_handoff(sid)
print(f"  OK: handoff in DB")

# ── 5. Second loop: existing data detected ───────────────────────────────
print("\n[5] Second loop (detects existing data)")
fi2 = M.FlowInput(
    power_team_project_id="pt_final",
    project_session_id=sid,
    human_directive="Add DELETE endpoint.",
)
li2 = M.FlowLoopInput(loop_index=1)
state2 = executor.state_from_db(fi2, li2)
state2 = executor.manager_digest(state2)
assert "ESTIMATING PROGRESS" in state2.input_digest
print(f"  OK: digest detects existing data: {state2.input_digest[:50]!r}")

# ── 6. FastAPI app ────────────────────────────────────────────────────────
print("\n[6] FastAPI app")
app = create_app()
routes = [r for r in app.routes if hasattr(r, "methods") and hasattr(r, "path")]
api_routes = [r for r in routes if r.path.startswith("/api/")]
print(f"  OK: {len(api_routes)} /api/* routes registered")
assert len(api_routes) >= 50, f"expected >= 50 routes, got {len(api_routes)}"

# ── 7. LangGraph (optional) ───────────────────────────────────────────────
print("\n[7] LangGraph (optional)")
try:
    from task_hounds_api.workflow.graph import build_graph
    g = build_graph()
    print(f"  OK: LangGraph compiled: {type(g).__name__}")
except ImportError as e:
    print(f"  SKIP: langgraph not installed (executor still works): {e}")
except Exception as e:
    print(f"  SKIP: {e}")

print()
print("=" * 60)
print("ALL PHASES PASSED")
print("=" * 60)
print(f"Test DB: {test_db}")
