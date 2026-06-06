# Project Structure

This document explains the organization of the Task Hounds codebase.

## Directory Layout

```
power-teams/
│
├── 📄 README.md                   # Main project documentation
├── 📄 pyproject.toml              # Python project configuration
├── 📄 LICENSE                     # MIT License
├── 📄 CONTRIBUTING.md             # Contribution guidelines
├── 📄 .gitignore                  # Git ignore rules
├── 📄 build_electron.ps1          # Electron build script
├── 📄 development/docs/planning/todo.md  # Restructuring plan & record
│
├── 📁 backend/power_teams/        # Core Python application source
│   ├── __init__.py
│   ├── cli.py                     # CLI entry point
│   ├── db.py                      # Database operations & schema
│   │
│   ├── mvp/                       # MVP agent orchestration
│   │   ├── __init__.py
│   │   └── runner.py              # Manager-worker-reviewer loop
│   │
│   ├── integrations/              # External service integrations
│   │   ├── __init__.py
│   │   ├── base_provider.py       # Provider base class
│   │   ├── opencode_provider.py   # OpenCode HTTP/SSE client
│   │   └── opencode_cli_provider.py
│   │
│   └── runtime/                   # Runtime process management
│       ├── __init__.py
│       ├── backend_registry.py    # Backend adapter factory
│       ├── opencode_supervisor.py # OpenCode server supervisor
│       ├── result_schema.py       # Result schema definitions
│       └── backends/              # Backend adapters
│           ├── __init__.py
│           ├── base.py            # Backend adapter base class
│           ├── opencode.py        # OpenCode adapter
│           ├── hermes.py          # Hermes adapter
│           └── openclaw.py        # OpenClaw adapter
│
├── 📁 api/                        # HTTP API server (was apps/desktop/)
│   ├── server.py                  # Python HTTP server (port 8765)
│   ├── smoke_test.py              # API smoke tests
│   └── README.md
│
├── 📁 frontend/                   # React + Vite dashboard (was apps/web/)
│   ├── index.html
│   ├── package.json               # React 19 + TypeScript + Vite
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── tsconfig.json
│   └── src/
│       ├── App.tsx                # Main application component
│       ├── main.tsx               # Entry point
│       ├── components/
│       │   ├── layout/            # Layout components
│       │   └── ui/                # UI components
│       ├── hooks/                 # Custom hooks
│       └── lib/                   # Utilities
│
├── 📁 desktop/                    # Electron desktop app (was apps/electron/)
│   ├── main.js                    # Electron main process
│   ├── preload.js
│   ├── splash.html
│   └── package.json
│
├── 📁 config/                     # Unified configuration directory
│   ├── .env.example
│   ├── settings.example.json
│   └── workspaces.example.json
│
├── 📁 data/                       # Data storage
│   ├── schema.sql                 # Main database schema
│   ├── migrations/                # Database migrations
│   └── power_teams.db             # SQLite database (gitignored)
│
├── 📁 runtime/                    # Runtime files (gitignored, configurable)
│   ├── agent_files/               # File bridge for agent communication
│   ├── logs/                      # Application logs
│   ├── processes/                 # Process state tracking
│   ├── sessions/                  # Session persistence
│   ├── opencode_home/             # OpenCode config home (XDG)
│   └── opencode_config/           # OpenCode project config
│
├── 📁 development/bin/                    # Utility scripts
│   ├── setup.sh                   # Dev environment setup (Linux/macOS)
│   ├── setup.ps1                  # Dev environment setup (Windows)
│   ├── check_models.py            # Check available LLM models
│   ├── dev_supervisor.py          # Development auto-restart supervisor
│   ├── run_ecommerce_test.bat
│   └── run_test.ps1
│
├── 📁 development/tests/                      # Test suite
│   ├── test_backend_layer.py
│   └── test_opencode.py
│
├── 📁 docs/                       # Documentation
│   ├── README.md                  # Documentation index
│   ├── analysis.md                # Full codebase analysis
│   ├── handoff.md                 # Per-session serve implementation
│   │
│   ├── architecture/              # System design documents
│   │   ├── MANAGER_WORKER_ARCHITECTURE.md
│   │   ├── MANAGER_AGENT_IMPROVEMENTS.md
│   │   ├── REVIEWER_AGENT_IMPLEMENTATION.md
│   │   └── IMPLEMENTATION_SUMMARY.md
│   │
│   ├── guides/                    # How-to guides
│   │   ├── PROJECT_STRUCTURE.md   # This file
│   │   ├── QUICK_REFERENCE.md
│   │   ├── QUICK_START.md
│   │   ├── ECOMMERCE_TEST_GUIDE.md
│   │   ├── REVIEWER_AGENT_TEST_GUIDE.md
│   │   └── RUN_VERIFICATION_REPORT.md
│   │
│   ├── api/                       # API documentation
│   │   └── API_BALANCE_ERROR_HANDLING.md
│   │
│   └── archive/                   # Historical reference docs
│       ├── FOLDER_CLEANUP_SUMMARY.md
│       ├── test_idea.md
│       └── user-intentions-todos.md
│
├── 📁 image/                      # Image assets
│   └── powerteams.jpg
│
├── 📁 old_quick_start/            # Legacy quick-start scripts
│
└── 📁 development/analysis/             # Detailed analysis documents
    └── structure-2026-05-22.md
```

## Key Files Explained

### Core Application

| File | Purpose |
|------|---------|
| `backend/power_teams/mvp/runner.py` | Main orchestration loop: manager_cycle(), worker_cycle(), reviewer_session() |
| `backend/power_teams/db.py` | All database operations, schema management, migrations |
| `backend/power_teams/runtime/opencode_supervisor.py` | Manages OpenCode serve processes |
| `backend/power_teams/runtime/backend_registry.py` | Factory for backend adapter selection |
| `api/server.py` | Python HTTP API server (serves frontend + REST API) |

### Configuration

| File | Purpose |
|------|---------|
| `pyproject.toml` | Python project metadata and dependencies |
| `config/.env.example` | Environment variable template |
| `config/settings.example.json` | Settings template |
| `config/workspaces.example.json` | Workspace configuration template |

### Runtime Communication

The system uses a **file-based message queue** in `runtime/agent_files/`:

| File | Used By | Purpose |
|------|---------|---------|
| `user_input.txt` | Manager | Reads human directives |
| `worker_report.md` | Manager | Reads worker completion reports |
| `work_0001_status.txt` | Manager | Checks worker busy/idle status |
| `manager_msg_user.md` | Worker | Reads manager messages |
| `tasks.md` | Worker | Reads assigned tasks |

### Logs

| Log File | Content |
|----------|---------|
| `runtime/logs/runner.log` | Main application log (manager/worker/reviewer cycles) |
| `runtime/logs/opencode_errors.log` | OpenCode provider error log |
| `runtime/logs/desktop-run-cycle.log` | API server run cycle log |

## Entry Points

| Command | File | Purpose |
|---------|------|---------|
| `power-teams` | `backend/power_teams/cli.py` | CLI entry |
| `power-teams-mvp` | `backend/power_teams/mvp/runner.py` | MVP runner |
| `python api/server.py` | `api/server.py` | HTTP API server (port 8765) |
| `cd frontend && npm run dev` | `frontend/src/main.tsx` | Frontend dev server (port 5173) |
| `npm start` (in desktop/) | `desktop/main.js` | Electron desktop app |

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `POWER_TEAMS_DB` | `data/power_teams.db` | Database file path |
| `POWER_TEAMS_RUNTIME_DIR` | `runtime/` | Runtime data directory |
| `PYTHONPATH` | `backend/` | Python module search path |

## Database Schema

Main tables (see `data/schema.sql`):

- **agent_registry**: Registered agents (manager, worker, reviewer)
- **suggestion_queue**: Task queue with states
- **handoff_records**: Structured context transfer between agents
- **reviewer_sessions**: Reviewer execution tracking and feedback
- **manager_messages**: Manager communications to users

## Agent Communication Flow

```
1. User writes to runtime/agent_files/user_input.txt
2. Manager reads input, creates suggestion in DB
3. Manager assigns task → runtime/agent_files/tasks.md
4. Worker polls, executes task, writes report
5. Worker sets status = "worker_done"
6. Reviewer triggered asynchronously
7. Manager QA checks reviewer status, includes feedback
8. Manager marks suggestion as "done"
9. Cycle repeats or waits for new input
```

## Development Workflow

1. **Setup**: `development/bin/setup.sh` or `development/bin/setup.ps1`
2. **Start OpenCode servers**: `python -m power_teams.cli serve-opencode`
3. **Start API server**: `python api/server.py`
4. **Start frontend** (optional): `cd frontend && npm run dev`
5. **Run agent loop**: `python -m power_teams.mvp.runner --auto-release`
6. **Monitor logs**: `runtime/logs/`

For auto-restart during development:
```bash
python development/bin/dev_supervisor.py --auto-loop
```

## Adding New Features

### New Agent Type

1. Add agent registration in `db.py:seed_default_agents()`
2. Create migration if needed in `data/migrations/`
3. Implement cycle function in `mvp/runner.py`

### New Database Table

1. Add CREATE TABLE to `data/schema.sql` (for fresh installs)
2. Create migration in `data/migrations/` (for existing databases)
3. Add helper functions in `db.py`

### New API Endpoint

1. Add route in `api/server.py`
2. Update frontend in `frontend/src/`
3. Document in `docs/api/`

## Directory Rename History (2026-05-22)

| Old Path | New Path |
|----------|----------|
| `src/` | `backend/` |
| `apps/desktop/` | `api/` |
| `apps/electron/` | `desktop/` |
| `apps/web/` | `frontend/` |

