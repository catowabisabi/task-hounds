# Task Hounds Backend Refactor Plan

This plan records the safe, small-step path for turning the current backend
function modules into clearer classes and components without rewriting the
workflow in one risky pass.

## Current Backend Control Files

The backend is currently controlled by several large layers:

- `core/api/fastapi_server.py` - FastAPI dashboard/API entry point.
- `core/api/server_legacy.py` - legacy HTTP server and helper implementation.
- `core/api/server.py` - compatibility shim for legacy imports and script usage.
- `core/api/services/legacy.py` - thin class facades over legacy helpers.
- `core/power_teams/mvp/runner.py` - manager/worker loop scheduler.
- `core/power_teams/agents/manager.py` - manager workflow and response parsing.
- `core/power_teams/agents/worker.py` - worker task execution cycle.
- `core/power_teams/agents/reviewer.py` - reviewer QA/UIUX feedback cycle.
- `core/power_teams/agents/base.py` - shared agent utilities, OpenCode calls,
  stream writing, parsing, todo sync, handoff, settings, and file helpers.
- `core/power_teams/db.py` - SQLite schema and DB helper functions.
- `core/power_teams/runtime/opencode_lifecycle.py` - OpenCode runtime discovery,
  binding, restart, checkpoint, and cleanup.
- `core/power_teams/runtime/opencode_supervisor.py` - managed `opencode serve`
  process startup.

## Refactor Principle

Do not rewrite the backend all at once.

Each step should:

- keep behavior the same;
- move one responsibility only;
- preserve old entry points until callers are migrated;
- add compile/import checks before commit;
- keep diffs small enough to review and revert.

The first refactor has already been done:

- `server.py` was moved to `server_legacy.py`;
- `server.py` became a compatibility shim;
- `core/api/services/legacy.py` now exposes class facades over legacy helpers;
- `fastapi_server.py` imports the service facade instead of importing a long
  list directly from `api.server`.

## Target Component Shape

Backend responsibilities should gradually become:

```text
api/
  fastapi_server.py          # route declarations only, thin HTTP layer
  services/
    settings.py              # settings and active project/session
    runtime_files.py         # runtime mirror files
    streams.py               # stream paths and chat stream rendering
    chat.py                  # chat state and chat messages
    agents.py                # agent registry/status updates
    suggestions.py           # suggestion queue operations
    handoff.py               # handoff read/write/versioning
    loop.py                  # start/stop/run manager-worker loop
    opencode.py              # API-facing OpenCode runtime helpers
    text.py                  # response extraction/repair helpers

power_teams/
  agents/
    manager.py               # public manager_cycle entry point
    manager_flow.py          # ManagerCycle / ManagerFlow orchestration
    manager_parser.py        # XML section and JSON output parsing
    manager_repair.py        # focused missing-section repair prompts
    worker.py                # public worker_cycle entry point
    reviewer.py              # public reviewer entry point
  services/
    todo_sync.py             # TODO_LIST/TODO_UPDATE_JSON DB sync
    suggestion_service.py    # create/replace/release suggestions
    handoff_service.py       # apply handoff JSON updates
    directive_service.py     # stable human directive handling
```

This target is directional. Create files only when a responsibility is stable
enough to move.

## Recommended Commit Sequence

### 1. Keep Service Facades, Migrate Routes Slowly

Goal: make `fastapi_server.py` call service objects directly.

Do this one route group at a time:

1. settings routes -> `services.settings`
2. stream routes -> `services.streams`
3. chat routes -> `services.chat`
4. handoff routes -> `services.handoff`
5. suggestion routes -> `services.suggestions`
6. agent routes -> `services.agents`
7. loop routes -> `services.loop`
8. OpenCode/runtime routes -> `services.opencode`

Keep legacy aliases while migrating. Remove each alias only after its route
group no longer uses it.

### 2. Extract Pure Manager Parsing

Start with pure functions because they are low risk and easy to test.

Move these from agent shared/manager code into a parser component:

- `_extract_section`
- `_parse_todo_block`
- `_parse_todo_update_json`
- `_is_valid_handoff_json`
- `repair_mojibake` if it remains manager-response specific

Target:

```python
class ManagerResponseParser:
    def section(self, text: str, name: str) -> str: ...
    def todo_list(self, text: str) -> list[dict]: ...
    def todo_update_json(self, text: str) -> list[dict]: ...
    def valid_handoff_json(self, text: str) -> bool: ...
```

Keep wrapper functions during migration if many call sites still expect them.

### 3. Extract Manager Repair Prompts

Move focused repair logic into a small component:

```python
class ManagerRepairService:
    def repair_section(self, response: str, name: str, instructions: str) -> str: ...
    def repair_todo_json(self, response: str, todo_block: str) -> str: ...
    def repair_handoff_json(self, response: str) -> str: ...
```

This makes the manager flow easier to read without changing behavior.

### 4. Extract DB-Backed Workflow Services

Move state changes one service at a time:

```python
class TodoSyncService:
    def persist_from_manager_response(self, response: str, owner: str) -> None: ...

class SuggestionService:
    def create_or_replace_worker_task(...): ...
    def ensure_todo_for_suggestion(...): ...

class HandoffService:
    def apply_manager_update(self, response: str, updated_by: str) -> int | None: ...

class DirectiveService:
    def current_directive(self) -> str: ...
    def mark_processed_if_manager_accepted(...): ...
```

Rules:

- `TODO_UPDATE_JSON` remains the machine-readable source of truth.
- If manager JSON is invalid, do not release work.
- Do not mark human inputs or worker_done suggestions complete until manager
  output passes validation.

### 5. Wrap Manager Cycle Without Changing Scenarios

Keep the public function:

```python
def manager_cycle() -> None:
    ManagerCycle().run()
```

Then move one scenario at a time:

```python
class ManagerCycle:
    def run(self) -> None: ...
    def handle_user_directive(self) -> bool: ...
    def handle_human_manager_messages(self) -> bool: ...
    def handle_worker_done(self) -> bool: ...
    def handle_pending_suggestion(self) -> bool: ...
    def handle_proactive_planning(self) -> bool: ...
```

Do not introduce a plugin system here yet.

### 6. Introduce Flow Steps After ManagerCycle Is Stable

Only after the scenarios are clear, introduce a small flow-step interface:

```python
class FlowStep:
    name: str
    def can_run(self, context) -> bool: ...
    def run(self, context) -> StepResult: ...
```

Then the manager can run:

```python
steps = [
    HumanDirectiveStep(),
    HumanThoughtStep(),
    WorkerDoneReviewStep(),
    PendingSuggestionStep(),
    ProactivePlanningStep(),
]
```

This enables different workflows later without rewriting the backend:

- coding flow
- QA-only flow
- documentation flow
- release flow
- product-planning flow

## Workflow Contracts To Preserve

### Human Directive

`HUMAN_DIRECTIVE` is the stable project/session mission.

- It follows the project.
- New sessions copy the previous directive in the same project.
- The loop never edits or deletes it automatically.
- Only explicit human manual changes update it.

### Human Thoughts And Suggestions

Human thoughts, questions, concerns, and ideas are not automatically todos.
The Manager digests them and may turn part of them into todo items or worker
tasks. Once digested, they can be marked processed, but should remain in
history.

### Manager Message

`MANAGER_MESSAGE` is shared guidance.

It is:

- shown to the human;
- fed back into the Manager;
- available to the Worker;
- available to the Reviewer.

### Worker Input

The Worker should stay narrow.

Worker input should include:

- human directive;
- manager message / task context;
- current todo list from DB;
- current task context and acceptance criteria.

The Worker does not need handoff as broad memory.

### Reviewer Input

Reviewer input should include:

- human directive;
- manager message;
- worker report;
- files changed;
- test result;
- known issues;
- acceptance criteria.

Reviewer output returns to Manager as feedback/suggestion. Reviewer does not
assign work directly.

### Handoff

Handoff is Manager memory.

- It is read at the start of the Manager loop.
- It is updated as JSON.
- It is not broad Worker context.

## Verification Commands

Run these after each backend refactor step:

```powershell
python -m py_compile core/api/server.py core/api/server_legacy.py core/api/fastapi_server.py core/api/services/legacy.py
python -m py_compile core/power_teams/agents/base.py core/power_teams/agents/manager.py core/power_teams/agents/worker.py core/power_teams/agents/reviewer.py core/power_teams/mvp/runner.py
python -c "import sys; sys.path.insert(0, 'core'); import api.fastapi_server as f; import api.server as s; from api.services.legacy import services; print('ok', bool(f.app), hasattr(s, 'main'), bool(services.settings.read))"
git diff --check
```

For UI-related changes, also run:

```powershell
cd ui/web
npm run build
```

## Stop Conditions

Pause and re-evaluate if any step:

- changes manager/worker/reviewer behavior unexpectedly;
- changes OpenCode process startup or binding;
- changes stream file paths;
- marks tasks done before JSON validation;
- causes FastAPI import/startup failures;
- requires touching more than one responsibility at the same time.

