"""Smoke test workflow layer — Manager flow with DB-as-whiteboard.

Simulates: start with directive, run through digest/plan/todo/select/release,
verify each step reads and writes DB. We use dummy agent calls (no real opencode).
"""
import os
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core")

# Use a fresh test DB to avoid clobbering real state
test_db = Path(tempfile.gettempdir()) / "workflow_smoke.db"
if test_db.exists():
    test_db.unlink()
os.environ["POWER_TEAMS_DB"] = str(test_db)

from task_hounds_api.db import init_db, connect
from task_hounds_api.db.ops import project as db_project
from task_hounds_api.db.ops import workflow as db_wf
from task_hounds_api.workflow import executor as ex
from task_hounds_api.workflow import models as M

init_db()
print("=== DB initialized at", test_db)

# Create a session
sid = "ps_smoke_01"
db_project.create_session(sid, "C:/tmp", name="smoke")
db_project.activate_session(sid)
print("Created session", sid)

# Build FlowInput
fi = M.FlowInput(
    power_team_project_id="pt_smoke",
    project_session_id=sid,
    human_directive="Build a TODO list app with a clean UI.",
    human_suggested_new_task_or_item="Create the new-todo form",
    todo_items=["Create form", "Add submit handler", "Style with Tailwind"],
)
li = M.FlowLoopInput(loop_index=0)
M.validate_flow_input(fi)
print("Validated FlowInput")

# Run the Manager steps
print("\n=== Manager flow ===")
state = ex.state_from_db(fi, li)
print(f"Initial state: existing plan = {state.plan[:50]!r}")

state = ex.manager_digest(state)
print(f"After digest: {state.input_digest[:80]!r}...")

state = ex.manager_plan(state)
print(f"After plan: {state.plan[:80]!r}...")

state = ex.manager_todo(state)
print(f"After todo: {len(state.todo_list)} items")

state = ex.manager_select_task(state)
print(f"After select: suggestion = {state.suggestion_content!r}")

state = ex.manager_release(state)
print(f"After release: manager_message = {state.manager_message[:80]!r}...")

# Verify DB writes
print("\n=== Verify DB writes ===")
plan = db_wf.get_plan(sid)
print(f"DB plan: {plan.get('content', '')[:80] if plan else 'None'!r}...")

todos = db_project.get_session(sid)  # not todos
from task_hounds_api.db.ops import todo as db_todo
todos = db_todo.list_todos(sid)
print(f"DB todos: {len(todos)} items")
for t in todos[:3]:
    print(f"  - {t['content']}")

msgs = db_wf.list_manager_messages(sid, limit=5)
print(f"DB manager_messages: {len(msgs)} entries")
for m in msgs[:3]:
    print(f"  - {m['content'][:80]}")

handoff = db_wf.get_handoff(sid)
print(f"DB handoff current_task: {handoff.get('current_task', '') if handoff else 'None'!r}")

# Run again (second loop) — should detect existing data
print("\n=== Second loop (should detect existing data) ===")
li2 = M.FlowLoopInput(loop_index=1)
state2 = ex.state_from_db(fi, li2)
state2 = ex.manager_digest(state2)
print(f"Second digest prefix: {state2.input_digest[:30]!r}")

# Cleanup
test_db.unlink()
print("\n=== Smoke test PASSED ===")
