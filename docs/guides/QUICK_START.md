# Quick Start

This guide starts Task Hounds locally for development.

## Requirements

- Python 3.11+
- Node.js 20+
- npm
- OpenCode CLI installed globally through npm and on `PATH`

Install or update OpenCode with npm:

```powershell
npm install -g opencode-ai
where opencode
```

On Windows, configure Task Hounds to use the npm package binary:

```text
C:\Users\<you>\AppData\Roaming\npm\node_modules\opencode-ai\bin\opencode.exe
```

`C:\Users\<you>\AppData\Roaming\npm\opencode.cmd` is acceptable as a fallback. Do not configure Task Hounds to use `C:\Users\<you>\.opencode\bin\opencode.exe`, because that standalone binary can use a different OpenCode runtime/session store.

## Install

```bash
git clone https://github.com/catowabisabi/task-hounds.git
cd task-hounds
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

```bash
PYTHONPATH=core python core/api/server.py --port 8765
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "core"
python core\api\server.py --port 8765
```

Open http://localhost:8765.

## Start an Agent Loop

1. Create or select a workspace.
2. Write a Human Directive.
3. Press Start Loop or Run Once.

Task Hounds will not start autonomous work without a pending Human Directive.

## Logs

Runtime logs are local-only and ignored by git:

```bash
tail -f core/runtime/logs/runner.log
```

Agent streams are stored under the active runtime session in `core/runtime/`.
