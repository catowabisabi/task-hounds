# OpenCode Lifecycle Architecture

## Overview

The OpenCode lifecycle system manages the lifecycle of opencode server instances — starting, monitoring, restarting, and gracefully shutting them down based on runtime policy. It distinguishes between **managed** (internally spawned), **external** (pre-existing or user-attached), and **unknown** (discovered on the network) opencode processes.

---

## Server Ownership Types

### 1. Managed (`owner=power_teams`)

Servers spawned by `OpenCodeSupervisor` or `OpenCodeLifecycleManager` via `start_managed_server()`. These are:

- Tracked in `opencode_server_instances` table with `owner='power_teams'`
- Monitored for health (port reachability checked every poll interval)
- Automatically restarted on crash (if `on_opencode_crash` policy is `"restart"`)
- Stopped when `stop_managed_server()` or `stop_all_managed()` is called
- Bound to a specific `project_session_id`

```
Lifecycle: start → running → (health check loop) → stop
                                    ↓
                              restart on crash
```

### 2. External (`owner=external`)

Servers the backend discovers on the network via `discover_external()` or that are explicitly attached via `attach_external_server()`. These are:

- Tracked with `owner='external'`
- NOT managed — no auto-restart, no lifecycle control
- Still shown in the Runtime panel for visibility
- Can be "promoted" to managed by calling `attach_external_server()` which re-registers them

```
Lifecycle: discovered → attached → visible in panel → detached
```

### 3. Unknown (`owner=unknown`)

Servers found listening on ports but not registered in the `opencode_server_instances` table. The system discovers them via port scanning (checking common opencode ports) but does not track or manage them.

---

## Runtime Policy

Runtime behavior is controlled by `runtime_policies` table. Key settings:

| Field | Values | Description |
|---|---|---|
| `close_behavior` | `ask`, `stop`, `restart`, `ignore` | What to do when backend receives close signal |
| `background_mode_enabled` | `true`/`false` | Whether managed opencode servers stay alive when backend exits |
| `on_backend_exit` | `stop_managed_opencode`, `ignore`, `keep_running` | Action when this backend process exits |
| `on_backend_crash_recovery` | `ask`, `restart_backend`, `ignore` | What to do if the backend crashes and restarts |
| `on_opencode_crash` | `restart`, `mark_error`, `ignore` | What to do when managed opencode process crashes |
| `max_managed_opencode_servers` | integer | Maximum number of managed servers (default: 1) |
| `default_topology` | `shared`, `per_role` | Default topology for new managed servers |
| `default_shared_port` | port number | Default port for shared topology (default: 18765) |
| `allow_external_attach` | `true`/`false` | Allow attaching externally discovered servers |
| `allow_unknown_attach` | `true`/`false` | Allow attaching unknown/port-scanned servers |

---

## Checkpoint System

### Purpose

Checkpoints preserve the full state of the agent configuration (agent bindings, session state, opencode instances) so that after a backend restart or compaction, the system can restore agent → opencode server bindings without requiring the user to re-establish them manually.

### When Checkpoints Are Created

1. **Manual**: User clicks "Create Checkpoint" in the Runtime panel → calls `POST /api/runtime/checkpoint`
2. **Automatic on session switch**: When switching project sessions, a checkpoint is implicitly created for the previous session
3. **Before backend exit**: If `on_backend_exit = "stop_managed_opencode"`, a checkpoint is created before shutting down managed servers

### Checkpoint Data

Stored in `run_checkpoints` table:

```sql
CREATE TABLE run_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_session_id TEXT,
    workspace_id TEXT,
    reason TEXT,              -- 'manual', 'session_switch', 'backend_exit', 'compaction'
    notes TEXT,
    status TEXT DEFAULT 'active',  -- 'active', 'archived', 'error'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

A checkpoint captures:
- `project_session_id` and `workspace_id` for context
- `reason` for audit trail
- Current agent bindings (manager, worker, reviewer, chat → opencode server instances)
- Runtime policy at time of checkpoint

### Restore from Checkpoint

`GET /api/runtime/checkpoints/{id}/resume` retrieves the checkpoint data and re-establishes:
1. Agent → opencode server bindings via `upsert_agent_binding()`
2. Settings (active workspace, project session)
3. Managed opencode server status

---

## Close Behavior Flow

```
User clicks "Stop Loop" / backend receives close signal
         ↓
check `close_behavior` policy:
  ┌─ "ignore"    → do nothing, continue running
  ├─ "stop"      → stop all managed opencode servers immediately
  ├─ "restart"   → restart stopped servers after brief delay
  └─ "ask"       → trigger confirmation modal in UI
                       ↓
              User confirms → stop all managed servers
              User cancels → continue running
         ↓
if `background_mode_enabled` = false:
    stop all managed opencode servers
else:
    keep servers running (background mode)
```

---

## Recovery Scenarios

### 1. Backend crash + restart

```
before crash: checkpoint created automatically
    ↓
backend restarts
    ↓
OpenCodeLifecycleManager.__init__() checks for:
  - Existing opencode_server_instances (managed = power_teams)
  - Last checkpoint with active bindings
    ↓
if `on_backend_crash_recovery = "restart_backend"`:
    restore bindings from checkpoint
    reconnect to existing managed servers
```

### 2. Compaction (session archive/cleanup)

```
before compaction: agent state + bindings captured in checkpoint
    ↓
compaction process runs (archive sessions, clear streams)
    ↓
after compaction:
    checkpoint data preserved
    agent bindings restored from checkpoint
    opencode servers keep running (background mode if enabled)
```

### 3. OpenCode process crash

```
health check detects: port unreachable
    ↓
update `status` to 'error' in opencode_server_instances
    ↓
check `on_opencode_crash` policy:
  - "restart" → stop crashed instance, start new managed server
  - "mark_error" → set agent state to 'error', await manual intervention
  - "ignore" → leave status as 'error', no action taken
```

---

## No-Opencode Mode (`--no-opencode`)

When the backend is started with `--no-opencode` flag:

1. `OpenCodeSupervisor` is NOT started
2. `_opencode_enabled` is set to `False`
3. `ensure_opencode_servers()` raises `RuntimeError("opencode_disabled")` if called
4. The `Handler` class serves API endpoints only (no opencode lifecycle management)
5. Existing opencode servers (if any) are left running externally

```python
# server.py main()
_main__(
    port=8765,
    start_opencode=not args.no_opencode,  # False if --no-opencode passed
    ...
)
```

This is useful for:
- Lightweight API-only mode (no agent execution)
- Debugging or testing without full opencode stack
- Running the backend in a configuration where opencode is managed externally

---

## Key APIs

| Endpoint | Method | Description |
|---|---|---|
| `/api/runtime/status` | GET | Runtime health: counts, bindings, policy, last checkpoint |
| `/api/runtime/opencode` | GET | All server instances (managed + external + unknown) |
| `/api/runtime/opencode/start` | POST | Start a new managed server |
| `/api/runtime/opencode/{id}/stop` | POST | Stop a specific managed server |
| `/api/runtime/opencode/{id}/restart` | POST | Restart a specific managed server |
| `/api/runtime/opencode/discover` | GET | Port-scan and discover external/unknown servers |
| `/api/runtime/opencode/attach` | POST | Attach an external server (re-register with owner=external) |
| `/api/runtime/stop-all` | POST | Stop all managed servers |
| `/api/runtime/checkpoint` | POST | Create a checkpoint |
| `/api/runtime/checkpoints` | GET | List checkpoint history |
| `/api/runtime/checkpoints/{id}/resume` | GET | Restore from checkpoint |
| `/api/runtime/checkpoints/{id}/archive` | POST | Archive a checkpoint |
| `/api/runtime/policy` | GET/PUT | Get or update runtime policy |

---

## Database Tables

### `opencode_server_instances`

```sql
CREATE TABLE opencode_server_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    pid INTEGER,
    owner TEXT NOT NULL,          -- 'power_teams', 'external', 'unknown'
    status TEXT DEFAULT 'running', -- 'running', 'stopped', 'error', 'unknown'
    topology TEXT,                 -- 'shared', 'per_role'
    project_session_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### `agent_bindings`

```sql
CREATE TABLE agent_bindings (
    role TEXT PRIMARY KEY,        -- 'manager', 'worker', 'reviewer', 'chat'
    agent_id INTEGER,
    session_id TEXT,
    bound_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### `runtime_policies`

```sql
CREATE TABLE runtime_policies (
    name TEXT PRIMARY KEY,         -- 'default' (only one row usually)
    close_behavior TEXT DEFAULT 'ask',
    background_mode_enabled INTEGER DEFAULT 0,
    on_backend_exit TEXT DEFAULT 'stop_managed_opencode',
    on_backend_crash_recovery TEXT DEFAULT 'ask',
    on_opencode_crash TEXT DEFAULT 'mark_error',
    max_managed_opencode_servers INTEGER DEFAULT 1,
    default_topology TEXT DEFAULT 'shared',
    default_shared_port INTEGER DEFAULT 18765,
    allow_external_attach INTEGER DEFAULT 1,
    allow_unknown_attach INTEGER DEFAULT 0
);
```

### `run_checkpoints`

```sql
CREATE TABLE run_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_session_id TEXT,
    workspace_id TEXT,
    reason TEXT,
    notes TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```