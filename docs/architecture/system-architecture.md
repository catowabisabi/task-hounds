# Task Hounds Architecture

## Runtime Source Of Truth

Task Hounds uses `project_sessions.id` as the project-scoped session key. Older code may refer to the same concept as `powerteams session id`, `session id`, or `Task Hounds session id`.

Agent runtime state is stored in SQLite:

- `project_sessions` stores the active project/session, workspace path, title, and role session ids.
- `agent_registry` stores agent state, model binding, OpenCode role, host, port, last error, and current session id.
- `agent_runtime_bindings` stores OpenCode server bindings per role.
- `user_directives` stores human-origin directives. AI code must not delete or rewrite these records.
- `worker_reports`, `manager_messages`, `suggestion_queue`, `session_plan`, and `session_todos` store collaborative work state.

Legacy text files under `core/runtime` are compatibility mirrors and fallbacks. New runtime control should read and write DB records first.

## Session Resolution

Role session ids are resolved from `project_sessions.id` and role-specific session fields, then mirrored into `agent_registry.session_id` for agent execution.

Checkpoint snapshots can restore historical state, but they are not the primary source for active role session ids. The DB relationship from active project session to role session id is authoritative.

## Loop Locking

Start Loop and Run Once require a pending human directive for the active project session. Manager messages and suggestions are context, not permission to start autonomous work.

The manager consumes pending directives from `user_directives`, marks them processed, and clears only the active runtime input mirror.

## OpenCode Timeout Recovery

OpenCode silence and hard timeouts are configurable through settings. On timeout, the agent is marked `error` in `agent_registry`, `last_error` is stored for the UI, and OpenCode abort is attempted before killing the process.

The UI polls `/api/agents` and displays DB-backed state and errors. Runtime status must not depend on text files as the control plane.

## Suggestions And Todos

Manager suggestions are synchronized into `session_todos` so accepted work has a visible planning object. Completed todos are hidden by default in the UI but remain stored.

Active suggestions with `session_id IS NULL` remain visible in the UI for manual cleanup because older runs may have created unscoped rows.
