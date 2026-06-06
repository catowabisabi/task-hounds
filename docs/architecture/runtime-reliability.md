# Runtime Reliability Policy

> Stabilization checkpoint — references the runtime work landed in
> commits `9af61d3` (fix) + `ce0184d` (refactor) + `1402493` (tests)
> on 2026-06-04. Verified end-to-end by `tools/runtime_e2e_smoke.py`.

This document is the single source of truth for runtime invariants
that other agents (and humans) must not silently break. If a change
touches any of the behaviour described here, update this doc in the
same commit and add a regression test under `tests/`.

---

## 1. Directive lifecycle

**Table**: `user_directives`

| Column    | Type    | Notes |
|-----------|---------|-------|
| `id`      | INTEGER | autoincrement PK |
| `session_id` | TEXT  | NOT NULL — the project session |
| `directive` | TEXT  | NOT NULL — the human's instruction text |
| `status`  | TEXT    | `pending` (default) / `running` / `processed` / `failed` |
| `error`   | TEXT    | nullable; populated on `failed` |
| `created_at` / `updated_at` | TIMESTAMP | auto |

**State machine** (enforced by `db/ops/chat.py`):

```
          create_directive()
                  │
                  ▼
               pending ─── claim_pending_directive() ──► running
                                                       │
                                       ┌───────────────┴───────────────┐
                                       ▼                               ▼
                                  processed                       failed(error=...)
                          (graph.run_loop returns)      (graph.run_loop raises)
```

### The atomic-claim contract

`claim_pending_directive(session_id)` is the **only** entry point for
picking up a directive. It is implemented as a single SQLite statement:

```sql
UPDATE user_directives
   SET status='running', updated_at=CURRENT_TIMESTAMP
 WHERE id = (
     SELECT id FROM user_directives
      WHERE session_id = ? AND status = 'pending'
      ORDER BY id ASC LIMIT 1
   )
   AND status = 'pending';
```

The trailing `AND status = 'pending'` is the atomic guard: two
threads that both pass the SELECT will race on the UPDATE; only one
gets `rowcount == 1`, the other gets 0 and returns `None`.

If `rowcount == 0`, the function returns `None` (no claim). The
caller MUST treat `None` as "nothing to do" — never invent a row.

`mark_directive_status(id, status, error=None)` is the only writer
for status. The legacy `mark_directive_processed(id)` is kept as a
back-compat alias that calls `mark_directive_status(id, "processed")`.

### What "failed" means

`failed` is a terminal state. A `failed` row stays in the DB so the
UI can show "your last directive failed because X". `failed`
directives are **never re-picked** by `claim_pending_directive()`
because the SELECT filter requires `status = 'pending'`.

---

## 2. OpenCode timeout policy

Timeouts are enforced per call site, not globally.

| Call site | Timeout | Why | File:line |
|-----------|---------|-----|-----------|
| `worker_execute` (oc_client.run) | **900s** (15 min) | Worker runs a real coding task; this is the upper bound for a single concrete change. | `workflow/executor.py:296-303` |
| `reviewer_check` (oc_client.run) | **300s** (5 min) | Reviewer reads the worker's report and writes structured feedback. | `workflow/executor.py:358-365` |
| Manager steps (digest/plan/todo/select/release) | n/a | Manager steps do **not** call `oc_client.run` — they read/write the DB only. The 5-min "manager budget" is enforced at the outer loop level by `BackgroundLoop._run`'s overall health, not by per-call timeout. | n/a |

The `timeout` argument is forwarded from `BackgroundLoop` →
`executor.<role>` → `oc_client.run` → `_run_cmd`. The mechanism:

- A reader thread drains `proc.stdout` into a `queue.Queue`.
- The main thread polls the queue with `timeout=0.05s`; each poll
  checks `time.monotonic() - start >= timeout`.
- On expiry: `_kill_proc(proc)` (calls `proc.kill()`, i.e. SIGKILL /
  `TerminateProcess`), then `raise TimeoutError(...)`.
- The exception bubbles up to `run()`'s try/except, which returns
  `rs.err(..., error_type="TimeoutError", message=...)`.

### Timeout test

`tests/test_opencode_timeout.py::test_run_cmd_kills_on_timeout` is
the regression guard. It mocks `subprocess.Popen` to return a process
whose `stdout` blocks on `threading.Event`, calls `run(timeout=1)`,
and asserts:

- the call returns within 5s (thread watchdog)
- `proc.kill` was called
- result `ok` is `False` and `error.type` is `"TimeoutError"`

The test also patches `is_reachable` to return `True` so the test
does not depend on an actual OpenCode server being up.

---

## 3. Stop semantics

`BackgroundLoop.stop()` is the single kill switch. It returns the
following shape (the contract the API endpoint
`POST /api/workflow/stop-loop` exposes to the UI):

```python
{
    "stopping": True,                       # we set the _stop Event
    "current_run_cancel_requested": True,   # we asked OpenCode to die
    "current_run_killed": bool,             # whether there was a live run
}
```

What it does, in order:

1. `self._stop.set()` — blocks the next polling tick.
2. `registry.kill_all_runs()` — iterates the module-level
   `_RUN_REGISTRY` (a `dict[str, subprocess.Popen]` guarded by a
   `threading.Lock`); for each entry, if Windows, runs
   `taskkill /PID <pid> /T /F` (kills process tree); otherwise calls
   `proc.kill()`. Returns the number actually killed.
3. Returns the dict above.

### What stop does NOT do

- It does **not** rollback the DB. If the manager already wrote a
  plan, the plan stays. A `failed` row stays. UI should treat the
  stop as a "best effort interrupt" — not a transaction rollback.
- It does **not** terminate the BackgroundLoop thread synchronously.
  The thread exits on its next `_stop.is_set()` check, which is at
  the top of the `while` loop. If the thread is currently blocked
  in `_run()`'s `lc.ensure_running()` (which can take up to 30s for
  the OpenCode serve health check), the stop won't take effect
  until that returns. The `is_running()` method has been fixed to
  return `False` once `_stop` is set, so the UI sees a stopped loop
  immediately even though the thread may take a moment to exit.

### Test

`tests/test_stop_semantics.py::test_stop_loop_returns_cancel_requested`
asserts the response shape. `tools/runtime_e2e_smoke.py` exercises
the live API and confirms `current_run_killed` is a real bool.

---

## 4. Loop singleton policy

There is **exactly one** `BackgroundLoop` in the running process,
held as a module-level singleton:

```python
# core/task_hounds_api/api/routes/workflow.py
_bg = BackgroundLoop()
```

All workflow loop control routes delegate to `_bg`:

| Route                              | Delegation                            |
|------------------------------------|---------------------------------------|
| `POST /api/workflow/start-loop`    | `_bg.start()`                          |
| `POST /api/workflow/stop-loop`     | `_bg.stop()` → new shape               |
| `GET  /api/workflow/status`        | `_bg.is_running()`                     |
| `POST /api/workflow/directive`     | `db_chat.create_directive(...)`        |
| `GET  /api/workflow/directives`    | `db_chat.list_directives(...)`         |

There is intentionally no other loop controller. The historical
duplicate (`_loop_thread` / `_loop_stop` / `_runner` / `_tick`) in
`api/routes/workflow.py` was deleted in commit `ce0184d`. Do not
re-introduce it.

`tests/test_loop_consolidation.py::test_workflow_routes_delegate_to_background_loop`
inspects the source of `api/routes/workflow.py` and asserts the
absence of `_loop_thread` / `_loop_stop` / `_runner` plus the
presence of `BackgroundLoop` in the imports.

---

## 5. Graph retry budget

`workflow/models.py::FlowState` carries a `__digest_retry__: int = 0`
field. It is incremented inside each manager node that returns
`{"__route__": "manager_digest", ...}` (plan, todo, select, and the
guard branches in release). When the counter reaches
`MAX_DIGEST_RETRIES = 3` (defined in `workflow/graph.py`),
`_route_after` returns `"__give_up__"` instead of routing back to
`manager_digest`. The `__give_up__` edge is wired to `END` in all
four conditional edge maps.

On give-up, `_bump_retry` also sets `state.status = "failed"`. The
caller (`BackgroundLoop._tick`) catches the graph exception (or
inspects the final state) and calls
`db_chat.mark_directive_status(did, "failed", error="graph gave up after 3 retries")`.

This prevents the historical bug where a misbehaving manager
function (returning empty plan / todo / selection forever) would
loop the graph indefinitely.

`tests/test_graph_max_retry.py::test_digest_loop_gives_up_after_3`
mocks all manager steps to return empty and asserts the graph
terminates within 5s with `state.status == "failed"` and
`__digest_retry__ >= 3`.

---

## 6. Configuration

| Env var                       | Default | Used in | Effect |
|-------------------------------|---------|---------|--------|
| `TASK_HOUNDS_OPENCODE_PORT`   | `18765` | `workflow/loop.py::_resolve_opencode_port` | Port the `BackgroundLoop` and `OpenCodeLifecycle` use to talk to the `opencode serve` instance. |
| `POWER_TEAMS_DB`              | `core/db/power_teams.db` | `db/connect.py::DB_PATH` | SQLite file path. Tests override with a tempfile. |
| `TASK_HOUNDS_PORT`            | `8766`  | `ui/desktop/main.js` | Desktop app's preferred FastAPI port. Has fallback chain `8766 → 8765 → 18951-19000` (per commit `42191e8`). |

The desktop app's `chooseServerPort` already uses `TASK_HOUNDS_PORT`
with the documented fallback chain; the Python side now matches
that policy with `TASK_HOUNDS_OPENCODE_PORT` (separate from the
backend port — `opencode serve` is a separate process).

---

## 7. Known limitations (not yet addressed)

### 7.1 Loop dies silently if OpenCode is unreachable

`BackgroundLoop._run()` calls `lc.ensure_running()`. If the OpenCode
binary is not installed (e.g. `installation.cmd` was not run on the
target machine), `find(required=True)` raises `FileNotFoundError`. The
except clause logs `"cannot start opencode"` and the thread exits
silently.

**Symptom**: `POST /api/workflow/start-loop` returns `{started: True}`,
`GET /api/workflow/status` immediately reports `loop_running: False`,
and pending directives stay `pending` forever.

**Workaround** for now: run `installation.cmd` before starting the
desktop app. A proper fix would surface the failure in the UI
(an error toast) and offer a "Retry / Install OpenCode" button.
Tracked as a P1 follow-up.

### 7.2 Point 4 product decision — packaged exe + OpenCode runtime

The `ui/desktop/main.js` boot sequence blocks on
`hasOpenCodeCommand()` — if the managed binary at
`core/runtime/opencode_runtime/node_modules/opencode-ai/bin/opencode.exe`
is missing, the desktop app shows a "Backend Not Found" dialog and
quits. This means a clean install on a new machine requires running
`installation.cmd` *before* the desktop app.

Two product paths exist:

| Path | Tradeoff |
|------|----------|
| **Bundle** the OpenCode runtime into the portable .exe via `extraResources` | Self-contained, one-click install. Increases .exe size by ~200MB. x64-only unless we ship per-arch. |
| **Don't block** — let the UI open with a "Runtime not installed" state and offer an in-app installer | Smaller .exe, friendlier dev experience. Adds UI work and a downloader. |

This is a **product decision**, not a coding one. Track in
`docs/plans/` once decided.

### 7.3 `intentional compat layer bloat`

`core/task_hounds_api/api/routes/compat.py` is 1,065 lines and
provides legacy endpoints for the old UI. Five of its routes call
`db_chat.get_latest_directive` (read-only). All of them are
deliberate compat shims; do not remove until the UI migrates.

---

## 8. How to verify (the regression check list)

After any change touching the workflow / loop / opencode layers,
run all three:

```powershell
$env:PYTHONPATH = "core"
python -m pytest tests/ -v
python tests\runtime_shape_smoke.py
python tools\runtime_e2e_smoke.py
```

Expected:

| Test suite | Count | What it catches |
|------------|-------|------------------|
| `pytest tests/` | 12/12 | lifecycle states, atomic claim, timeout kill, retry budget, stop shape, loop consolidation |
| `runtime_shape_smoke.py` | 31/31 | API endpoint contract (every UI call returns the expected shape) |
| `runtime_e2e_smoke.py` | 8/8 | end-to-end real-server flow including the new stop response shape |

All three currently pass. Treat any of them going red as a
**regression of the runtime reliability work** and respond
accordingly.
