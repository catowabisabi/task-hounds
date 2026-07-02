# Quick Start

This guide starts Task Hounds locally for development.

## Requirements

- Python 3.11+
- Node.js 20+
- npm
- Task Hounds managed OpenCode runtime

Install the pinned OpenCode runtime and plugins:

```powershell
.\installation.cmd
```

Task Hounds stores the managed binary path in `core/runtime/settings.json` and does not use external OpenCode binary overrides.

```text
core/runtime/opencode_runtime/node_modules/opencode-ai/bin/opencode.exe
```

## Install

```bash
git clone https://github.com/catowabisabi/task-hounds-public.git
cd task-hounds-public
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

## Run

```powershell
$env:PYTHONPATH = "core"
python -m task_hounds_api.supervisor --host 127.0.0.1 --port 8766
```

Open http://localhost:8766.

## Start an Agent Loop

1. Create or select a workspace.
2. Write a Human Directive.
3. Press Start Loop or Run Once.

Task Hounds will not start autonomous work without a pending Human Directive.

## Logs

Runtime logs are local-only and ignored by git:

```bash
tail -f core/runtime/logs/workflow/runner.log
```

Agent streams are stored under the active runtime session in `core/runtime/`.
