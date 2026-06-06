# Task Hounds — Per-Session OpenCode Serve Implementation

## Problem Solved

Opencode serve processes were running in power-teams software root (`ROOT`), polluting the software's own files with project work and causing instability.

Now each project session gets its own pair of opencode serve processes (manager + worker) running in the **project folder**, not the software root. Session-aware serve management ensures clean startup and teardown.

---

## Files Changed

### New Files

- `data/migrations/007_add_opencode_server_instances.sql`
  - Creates `opencode_server_instances` table
  - Tracks: power_teams_session_id, agent_role, host, port, opencode_session_id, project_folder, pid, started_at

- `data/migrations/008_add_project_folder_location.sql`
  - Adds `project_folder_location` column to `project_handoff` table

### Modified Files

#### `src/power_teams/db.py`

- `_HANDOFF_FIELDS` list added `"project_folder_location"` (line ~97)
- Added 3 new helper functions:
  - `register_opencode_server()` — registers a serve instance for a session
  - `get_opencode_servers_for_session()` — retrieves all serve instances for a session
  - `unregister_opencode_servers_for_session()` — removes registrations (does NOT kill processes)

#### `src/power_teams/runtime/opencode_supervisor.py`

- Added `start_for_session(power_teams_session_id, project_folder)` method
  - Spins up manager + worker opencode serve processes with `cwd=project_folder`
  - Registers each server in `opencode_server_instances` table
  - Updates `agent_registry` with host/port
  - Returns dict mapping role -> (port, pid)

- Added `stop_for_session(power_teams_session_id)` method
  - Kills OS processes via `stop_process_tree()`
  - Calls `unregister_opencode_servers_for_session()`
  - Clears `self.servers` list and writes "stopped" state

#### `src/power_teams/mvp/runner.py`

- `run_loop()` now tracks `_last_session_id`
  - On session change detection: calls `supervisor.stop_for_session(old)` then `supervisor.start_for_session(new, project_folder)`
  - Project folder sourced from `project_handoff.project_folder_location` (parsed from JSON)
  - Falls back to `project_sessions.name` or `ROOT` if not set

- `handoff_summary()` now includes `_field("Project Folder", "project_folder_location")`

---

## Database Schema

### New Table: `opencode_server_instances`

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | autoincrement |
| power_teams_session_id | TEXT | FK to project_sessions.id |
| agent_role | TEXT | 'manager' or 'worker' |
| host | TEXT | default '127.0.0.1' |
| port | INTEGER | opencode serve HTTP port |
| opencode_session_id | TEXT | optional, opencode internal root session |
| project_folder | TEXT | cwd of the serve process |
| pid | INTEGER | OS process ID |
| started_at | TIMESTAMP | |

Indexes: `(power_teams_session_id)`, `(agent_role)`

### New Column: `project_handoff.project_folder_location`

| Column | Type | Notes |
|--------|------|-------|
| project_folder_location | TEXT | absolute path to project root (stable layer) |

---

## How Session Switching Works

```
run_loop()
  ├─ current_sid = get_active_session_id()
  ├─ if current_sid != _last_session_id:
  │    ├─ stop_for_session(_last_session_id)  [kill processes + unregister]
  │    └─ start_for_session(current_sid, project_folder)  [spawn + register]
  └─ _last_session_id = current_sid
```

`project_folder` is read from `project_handoff.project_folder_location` (JSON-parsed). Falls back to `project_sessions.name` or `ROOT`.

---

## Backward Compatibility

- `agent_registry` still stores host/port per agent — unchanged
- `OpenCodeSupervisor.start()` (global legacy startup) still works as before
- Migrations use `IF NOT EXISTS` and `ADD COLUMN IF NOT EXISTS` patterns
- Existing `send_to_agent()` / provider chain unchanged — they just read updated host/port from `agent_registry`

---

## What Needs Integration (Pending Work)

The `start_for_session` / `stop_for_session` infrastructure is in place, but the **session switching trigger** lives in `run_loop()` which depends on `get_active_session_id()` from `settings.json`. Ensure that when a user switches project sessions in the UI, the settings file is updated so `run_loop()` detects the change.

Also verify that when a session is **archived** (via `sessions_arch` / `restore_session_arch`), `stop_for_session` is called for that session_id.

---

## Migration Order

Run in numeric order:
1. `007_add_opencode_server_instances.sql`
2. `008_add_project_folder_location.sql`

Apply via `init_db()` which reads and executes all migrations in `data/migrations/` sorted by filename.