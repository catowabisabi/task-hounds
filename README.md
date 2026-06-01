# Task Hounds - Work like a dog

Task Hounds is a local multi-agent development workspace. You give it a human directive, and it coordinates a Manager, Worker, Reviewer, and Chat agent around one project session.

The project is designed for people who want a visible, controllable agent loop instead of a black-box coding assistant. State is stored in SQLite, agent sessions are tracked per project, and the dashboard shows what the agents are doing in real time.

[Website](https://task-hounds.com) · [Repository](https://github.com/catowabisabi/task-hounds) · [Demo video](https://www.youtube.com/watch?v=pu-Rt8Ye4EQ&t=174s)

## Highlights

- Manager / Worker / Reviewer / Chat roles for autonomous development loops
- OpenCode-backed execution with reusable role sessions
- SQLite-backed project sessions, directives, todos, reports, and agent state
- React dashboard for live streams, settings, suggestions, todos, and chat
- Electron desktop build for Windows portable `.exe`
- Docker build for server-style deployment
- MIT licensed

## Demo

Watch the demo: https://www.youtube.com/watch?v=pu-Rt8Ye4EQ&t=174s

## Quick Start

### 1. Install requirements

You need:

- Python 3.11 or 3.12
- Node.js 20+
- npm
- Task Hounds managed OpenCode runtime

Install the pinned OpenCode runtime and plugins:

```powershell
.\installation.cmd
```

Task Hounds writes the managed binary path into `core/runtime/settings.json` and does not use external OpenCode binary overrides.

```text
core/runtime/opencode_runtime/node_modules/opencode-ai/bin/opencode.exe
```

Install Python dependencies:

```powershell
pip install -r requirements.txt
pip install .
```

Install and build the web UI:

```powershell
cd ui/web
npm ci
npm run build
cd ../..
```

### 2. Configure environment

Copy the example file:

```powershell
Copy-Item .env.example .env
```

The environment variable prefix is still `POWER_TEAMS_` for compatibility. You do not need to rename existing local settings.

Useful settings:

```env
POWER_TEAMS_DB=core/db/power_teams.db
POWER_TEAMS_REUSE_OPENCODE_SESSIONS=true
POWER_TEAMS_SILENCE_TIMEOUT=480
POWER_TEAMS_HARD_TIMEOUT=1200
```

For web/API debugging on Windows, you can make managed `opencode serve` open a visible console with debug logs:

```env
POWER_TEAMS_OPENCODE_DEBUG_CONSOLE=1
```

This is intended for development. Normal desktop/EXE usage should leave it unset so serve runs in the background.

### 3. Run the server

```powershell
$env:PYTHONPATH = "core"
python -m api.fastapi_server --port 8765
```

Open http://localhost:8765.

### 4. Start work

In the dashboard:

1. Pick or create a project workspace.
2. Write a Human Directive.
3. Press Start Loop or Run Once.

Task Hounds requires a pending Human Directive before it starts autonomous work. Suggestions and manager messages are context, not permission to run.

## Docker

Build the image:

```bash
docker build -t task-hounds .
```

Run it:

```bash
docker run --rm -p 8765:8765 -v "$(pwd)/data:/app/data" task-hounds
```

Then open http://localhost:8765.

The Docker image does not include local runtime data, SQLite databases, logs, OpenCode config, or desktop build artifacts.

## Windows EXE

The desktop app is built with Electron. The portable executable version is `1.0.0`.

Build it on Windows:

```powershell
.\build_exe.ps1
```

Output is written to:

```text
ui/desktop/dist/
```

The EXE package includes source resources needed by the app and the built web UI. It does not package local runtime folders, SQLite databases, logs, or personal OpenCode config.

## Architecture

Task Hounds uses SQLite as the runtime source of truth.

Important tables include:

- `project_sessions` for workspaces and per-role OpenCode session IDs
- `agent_registry` for agent state, model, role binding, and last errors
- `user_directives` for human-origin work directives
- `session_todos` for visible project work items
- `worker_reports` and `manager_messages` for agent reports and feedback
- `suggestion_queue` for manager-proposed next steps

Compatibility text files under `core/runtime` are treated as runtime mirrors and fallbacks. New control flow should prefer the DB.

See [ARCHITECTURE.md](ARCHITECTURE.md) for more detail.

## Project Structure

```text
task-hounds/
  core/
    api/                 # HTTP API and dashboard server
    db/                  # SQLite schema and migrations
    power_teams/          # Python package (legacy module name)
      agents/             # Manager, Worker, Reviewer, shared agent utilities
      mvp/                # Runner loop
      runtime/            # OpenCode lifecycle and backend adapters
      skills/             # DB skill helpers
  ui/
    web/                  # React + Vite dashboard
    desktop/              # Electron desktop wrapper
  docs/
    guides/               # User guides
    architecture/          # Design notes
    image/                # Public README and release images
  Dockerfile
  .env.example
```

## What Is Not Committed

These are intentionally excluded from the public repo:

- Runtime folders and logs
- SQLite database files
- OpenCode config/home folders
- Local `.env` files
- Electron and frontend build output
- Internal debug logs
- Local test-chat and OpenCode experiment folders
- `.hermes` workspace files

## Development Checks

Backend syntax check:

```powershell
python -m py_compile core/api/fastapi_server.py core/api/server.py core/power_teams/db.py core/power_teams/agents/base.py core/power_teams/agents/manager.py core/power_teams/agents/worker.py core/power_teams/agents/reviewer.py core/power_teams/mvp/runner.py
```

Frontend build:

```powershell
cd ui/web
npm run build
```

## Contributing

Issues and pull requests are welcome at https://github.com/catowabisabi/task-hounds.

Please keep runtime artifacts, database files, local OpenCode config, logs, and secrets out of commits.

## License

MIT. See [LICENSE](LICENSE).
