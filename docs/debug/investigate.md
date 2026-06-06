# Chat Agent 500 Error — Session vs CWD Investigation

Date: 2026-05-29
Status: Root cause identified

## The Question

When switching power-teams sessions, does the opencode server's CWD change?
How does `project_session_id` relate to the `cwd` used by `opencode serve`?

---

## DB Schema — Where CWD is Stored

### `opencode_server_instances.cwd`
Migration 013 adds:
```sql
ALTER TABLE opencode_server_instances ADD COLUMN cwd TEXT;
ALTER TABLE opencode_server_instances ADD COLUMN project_session_id TEXT;
```
Each opencode server instance has its own `cwd` stored independently.

### `project_sessions.workspace_path`
```sql
CREATE TABLE IF NOT EXISTS project_sessions (
    id                  TEXT PRIMARY KEY,
    workspace_id        TEXT,
    workspace_path      TEXT,       -- the cwd for this project session
    path_missing        INTEGER DEFAULT 0,
    workspace_fingerprint TEXT,
    ...
);
```
The `project_sessions` table holds `workspace_path` per session.

---

## Key Finding: Session Switching Does NOT Change Server CWD

### `_project_session_switch()` in server.py (line ~2516)
```python
def _project_session_switch(self, session_id: str):
    row = get_project_session(session_id, path=DB_PATH)
    new_settings = dict(read_settings())
    new_settings["active_workspace_id"] = ws_id
    new_settings["active_project_session"] = session_id
    new_settings["workspace_path"] = ws_path       -- only updates settings.json
    new_settings["project_session_id"] = session_id
    self._save_settings(new_settings)              -- writes to disk only
```

**Session switch ONLY updates `settings.json`. It does NOT:**
- Restart any opencode servers
- Change any running server's `cwd`
- Call `start_for_session()` or `start_managed_server()`

The file-routing (agent stream paths, runtime files) becomes session-aware via `settings.json`, but the running opencode server processes are NOT restarted or reconfigured.

---

## How opencode serve Gets Its CWD

### 1. Per-session server: `opencode_supervisor.start_for_session()`
```python
def start_for_session(self, power_teams_session_id: str, project_folder: str) -> dict:
    specs = self._server_specs(Path(project_folder).resolve())
    # project_folder becomes the cwd for servers started for this session
```

### 2. Dashboard (shared) server: `OpenCodeSupervisor(cwd=ROOT)`
```python
# Default: OpenCodeSupervisor(cwd=ROOT).start()
# ROOT = power-teams repo root, NOT a user workspace
```

### 3. Lifecycle manager: `start_managed_server()`
```python
def start_managed_server(self, port=None, topology="shared",
                          project_session_id=None, cwd=None) -> dict:
    working_dir = cwd or ROOT     -- defaults to ROOT if not provided
    # cwd passed EXPLICITLY by caller, NOT derived from project_session_id
```

**Critical observation**: `project_session_id` is stored in the DB record but is NOT used to look up `workspace_path`. The `cwd` must be passed in from the caller. The lifecycle manager does NOT query `project_sessions` to derive cwd.

---

## Two Server Patterns

| Pattern | CWD Source | Who Uses It |
|---|---|---|
| Shared "dashboard" server | `ROOT` (power-teams repo) | UI, chat when no session |
| Per-session server | `project_folder` (user workspace) | Manager/worker/reviewer cycles |
| Lifecycle-managed server | `cwd` param passed explicitly | Ad-hoc server starts |

---

## Why Port 18765 Works

Port 18765 was started with a user workspace as cwd (which has `.opencode/agents/` configured). That workspace's custom agents (`build`, `review`, etc.) are PRIMARY agents on that server.

When chat runs:
1. `_resolved_opencode_agent()` returns `None` (chat's opencode_agent="general" is skipped)
2. No `--agent` flag → opencode uses its **default** agent
3. On port 18765, default = `build` (PRIMARY because of .opencode/agents/)
4. `--attach build` → stdout streams 3 JSON lines → success

When the session is switched, the **server keeps running with the original cwd**. It never restarts or re-reads the workspace config. The server's agent configuration is frozen at startup time.

---

## Why Port 8899 Fails (Standalone Server)

Port 8899 was started with `cwd=project-root` (no `.opencode/agents/` config). Only built-in agents (`compaction`, `summary`, `title`) are PRIMARY.

When chat runs:
1. `_resolved_opencode_agent()` returns `None` (skip `--agent`)
2. No `--agent` flag → opencode uses its **default** = `general` (SUBAGENT)
3. `--attach general` → 0 bytes stdout → 500 error

The `_fetch_attached_session_text()` fallback is **never triggered** because:
- It requires `captured_sid` (extracted from stdout JSON events)
- But subagent produces NO stdout events
- So `captured_sid = None` → fallback skipped → empty result → 500

---

## Root Cause Summary

```
chat opencode_agent = "general" (subagent)
  -> _resolved returns None
  -> no --agent flag passed
  -> opencode uses DEFAULT agent

DEFAULT agent depends on server's CWD at startup:
  - CWD has .opencode/agents/ → custom agents are PRIMARY → works
  - CWD has no .opencode/agents/ → only built-ins PRIMARY, "general" = SUBAGENT → 500
```

**The server's CWD at startup determines whether chat works, and it NEVER changes during session switches.**

---

## Files Involved

| File | Role |
|---|---|
| `core/db/schema.sql` | `project_sessions.workspace_path` column |
| `core/db/migrations/013_opencode_lifecycle.sql` | `opencode_server_instances.cwd` column |
| `core/power_teams/runtime/opencode_supervisor.py` | `start_for_session()` uses project_folder as cwd |
| `core/power_teams/runtime/opencode_lifecycle.py` | `start_managed_server()` accepts cwd param, defaults to ROOT |
| `core/api/server.py` | `_project_session_switch()` only updates settings.json |
| `core/power_teams/db.py` | `register_opencode_server_instance()` stores cwd and project_session_id |
| `core/power_teams/agents/base.py` | `_resolved_opencode_agent()` skips --agent for general/default |

---

## Recommendation

To fix the 500 error when server starts without `.opencode/agents/` workspace:

Option A: Always ensure server starts with a workspace that has `.opencode/agents/` configured

Option B: Add explicit `--agent <primary>` when `_resolved` returns None, instead of omitting the flag entirely. Pick the first available PRIMARY agent from the discovered list.

Option C: Use `--agent build` explicitly for chat (since build is PRIMARY on properly configured servers)