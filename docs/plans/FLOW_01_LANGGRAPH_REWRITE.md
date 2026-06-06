# Flow 01 LangGraph Rewrite

## Purpose

This rewrite changes `flow_01` from a coarse Manager -> Worker -> Reviewer
sequence into a fine-grained graph workflow. The original Manager step made one
large model call that returned digest, plan, todos, selected task, verification,
and handoff data in one payload. That made the UI appear to pause for a long
time and then emit one large block.

The new graph breaks Manager work into observable nodes:

1. `manager_digest`
2. `manager_plan`
3. `manager_todo`
4. `manager_select_task`
5. `manager_release`
6. `worker_execute`
7. `reviewer_check`

## Existing APIs Reused

The rewrite keeps the current API surface stable:

- `POST /api/workflows/flow_01/start-loop`
- `GET /api/workflows/flow_01/runs`
- `GET /api/workflows/flow_01/runs/{run_id}`
- `POST /api/workflows/flow_01/runs/{run_id}/cancel`
- `GET /api/workflows/flow_01/plan`
- `GET /api/workflows/flow_01/todos`
- `GET /api/workflows/flow_01/suggestion`
- `GET /api/workflows/flow_01/manager-messages`
- `GET /api/workflows/flow_01/reports`

The old `phase` values are preserved for UI compatibility:

- `manager_running`
- `manager_completed`
- `worker_running`
- `reviewer_running`
- `completed`
- cancellation and failure phases

New run output fields add detailed progress:

- `graph_step`
- `graph_step_status`
- `manager_steps`
- `manager_partial`

## Files Changed

- `core/power_teams/agentic_workflows/flow_01/graph.py`
  - New LangGraph-compatible executor.
  - Uses `langgraph.graph.StateGraph`.
  - Raises an explicit runtime error if LangGraph is not installed. No fallback graph is used.

- `core/api/fastapi_server.py`
  - `flow_01` background execution now uses `Flow01GraphExecutor`.
  - Each graph node writes progress into `workflow_runs.output_json`.
  - Manager release persists plan, todos, suggestion, and manager message before Worker starts.

- `ui/web/src/App.tsx`
  - Pipeline Manager step now displays the current `graph_step` when available.

- `requirements.txt` and `pyproject.toml`
  - Added `langgraph>=0.2.60`.

- `core/power_teams/agentic_workflows/flow_01/workflow-test/test_flow_01.py`
  - Added graph executor ordering coverage.
  - Added regression coverage for structured Manager TODO payloads.

## State Management

The graph keeps using the existing `FlowState` dataclass as the canonical
workflow state. `graph.py` converts it to a serializable `Flow01GraphState`
dictionary for LangGraph nodes, then converts it back to `FlowState` for the
existing executors and output builders.

The graph callback writes progress after every node starts and completes:

- `phase`
- `graph_step`
- `graph_step_status`
- `manager_steps`
- `task`
- `todo_update_json`

Manager nodes keep `phase=manager_running` until `manager_release` completes.
At that point the server writes the Manager-visible DB state and changes phase
to `manager_completed`. Worker and Reviewer then use the existing executor
contracts.

## Model Calls

With real executors enabled, Manager now makes separate model calls for:

- digest
- plan
- todo creation
- task selection
- release message

Worker and Reviewer remain one model call each through their existing
executors.

With local/offline executors, LangGraph is still required. The node bodies can
use deterministic local logic, but orchestration must still run through
`StateGraph`.

## Compatibility Strategy

The rewrite does not remove `Flow01Workflow`. It remains available for tests,
offline compatibility, and output conversion. `Flow01GraphExecutor` composes it
for Worker, Reviewer, and `to_output()`.

The frontend can continue to treat `phase` as the high-level state. New UI can
use `graph_step` and `manager_steps` for detailed progress without another API
migration.

## Known Follow-Up Work

- Ensure packaged/runtime environments install `langgraph`; missing LangGraph is
  a hard startup error for `flow_01`.
- Add richer UI rendering for all `manager_steps`, not just the current
  `graph_step`.
- Add node-level retry policy and durable checkpointing once the runtime DB is
  ready to resume from individual graph nodes.
- Consider adjusting `send_to_agent()` so multiple Manager model calls append to
  a single stream instead of clearing the Manager stream at each call.
