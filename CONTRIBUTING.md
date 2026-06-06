# Contributing to Task Hounds

Thanks for helping improve Task Hounds.

## Development Setup

```bash
git clone https://github.com/catowabisabi/task-hounds.git
cd task-hounds
python -m venv .venv
```

Activate the virtual environment, then install dependencies:

```bash
pip install -r requirements.txt
pip install .
```

Build the dashboard:

```bash
cd ui/web
npm ci
npm run build
cd ../..
```

Run the API server:

```bash
PYTHONPATH=core python -m api.fastapi_server --port 8765
```

On Windows PowerShell:

```powershell
$env:PYTHONPATH = "core"
python -m api.fastapi_server --port 8765
```

## Checks

Backend syntax check:

```bash
python -m py_compile core/api/fastapi_server.py core/api/server.py core/power_teams/db.py core/power_teams/agents/base.py core/power_teams/agents/manager.py core/power_teams/agents/worker.py core/power_teams/agents/reviewer.py core/power_teams/mvp/runner.py
```

Frontend build:

```bash
cd ui/web
npm run build
```

## Pull Requests

- Keep runtime files, SQLite databases, logs, local OpenCode config, and secrets out of commits.
- Prefer focused commits with conventional commit names such as `fix:...`, `feat:...`, or `docs:...`.
- Update README or docs when changing setup, Docker, Electron packaging, or runtime behavior.
- Include screenshots or short videos for UI changes when useful.

## Runtime Artifacts

Do not commit:

- `.env` files
- `core/runtime/` contents
- `runtime/`
- `core/db/*.db*`
- `.hermes/`
- `.claude/`
- local test/debug folders

These are intentionally excluded from the open-source repo.
