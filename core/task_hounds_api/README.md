# task_hounds_api

The new clean Task Hounds codebase. Target structure for the rebuild.

## Layers

```
api/          HTTP layer (FastAPI). 8 route files, each <=150 lines.
db/           SQLite layer. Split by domain: project, agent, todo, workflow, chat, runtime.
opencode/     OpenCode CLI integration. 5 files, no fallbacks, no registry.
workflow/     LangGraph core engine. The Manager/Worker/Reviewer workflow.
agent_prompts/  All system prompts as .md files (no prompt strings in code).
skills/       Standalone tools (DB MCP).
docs/testing/tests/  Test files.
```

## What goes where

| From (old)                                       | To (new)                                          |
|--------------------------------------------------|---------------------------------------------------|
| `core/api/fastapi_server.py`                     | `api/routes/*.py` + `api/main.py`                 |
| `core/api/server_legacy.py`, `server.py`, `services/legacy.py` | DELETE                                    |
| `core/api/model_validation.py`                   | `opencode/config.py`                              |
| `core/power_teams/db.py`                         | `db/ops/*.py` (split by domain)                   |
| `core/power_teams/agents/base.py`                | `workflow/executor.py` (without prompts)          |
| `core/power_teams/agents/manager.py`             | REPLACED by `workflow/graph.py`                   |
| `core/power_teams/agents/worker.py`              | `workflow/executor.py` (OpenCodeWorkerExecutor)   |
| `core/power_teams/agents/reviewer.py`            | `workflow/executor.py` (OpenCodeReviewerExecutor) |
| `core/power_teams/agentic_workflows/flow_01/*.py` | `workflow/*.py`                                  |
| `core/power_teams/runtime/opencode_*.py`         | `opencode/*.py`                                   |
| `core/power_teams/runtime/backends/*`            | DELETE (only OpenCode used)                       |
| `core/power_teams/integrations/*`                | DELETE                                            |
| `core/power_teams/mvp/runner.py`                 | `workflow/loop.py`                                |
| Prompt strings in `agents/*.py`, `flow_01/*.py`, `fastapi_server.py` | `agent_prompts/*.md`             |

## Status

See `../rebuild_db/files/inventory.json` for per-file tracking.
