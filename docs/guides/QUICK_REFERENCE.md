# Quick Reference Guide

Essential commands and workflows for Task Hounds.

## First Time Setup

```bash
# 1. Navigate to project
cd power-teams

# 2. Set Python path
export PYTHONPATH=src          # Linux/Mac
$env:PYTHONPATH="backend"          # Windows PowerShell

# 3. Initialize database
python -m power_teams.mvp.runner --init-db

# 4. Verify installation
python -m power_teams.cli opencode-status
```

## Running the System

### Basic Mode (Single Cycle)

```bash
python -m power_teams.mvp.runner --once
```

Runs one complete manager → worker → reviewer cycle, then exits.

### Continuous Mode

```bash
python -m power_teams.mvp.runner
```

Runs indefinitely, polling for new tasks. Press `Ctrl+C` to stop.

### Development Mode (Auto-Release)

```bash
python -m power_teams.mvp.runner --auto-release --manager-interval 5 --worker-poll 3
```

- Automatically releases pending suggestions
- Manager checks every 5 seconds
- Worker polls every 3 seconds

### With OpenCode Servers

```bash
# Terminal 1: Start OpenCode servers
python -m power_teams.cli serve-opencode

# Terminal 2: Run agent loop
python -m power_teams.mvp.runner --auto-release
```

Or use the desktop dashboard which starts everything automatically:

```bash
python api/server.py
```

Access dashboard at: `http://localhost:8765`

## Common Workflows

### Submit a Task

1. **Via file** (when runner is running):
   ```bash
   echo "Create a todo list app" > runtime/agent_files/user_input.txt
   ```

2. **Via dashboard**: Use the web UI at `http://localhost:8765`

3. **Via API** (if using desktop server):
   ```bash
   curl -X POST http://localhost:8765/api/directive \
     -H "Content-Type: application/json" \
     -d '{"text": "Create a todo list app"}'
   ```

### Monitor Progress

```bash
# Watch main log
tail -f runtime/logs/runner.log

# Watch manager messages
tail -f runtime/agent_files/manager_stream.txt

# Check worker status
cat runtime/agent_files/work_0001_status.txt

# View database state
sqlite3 data/power_teams.db "SELECT id, status FROM suggestion_queue ORDER BY id DESC LIMIT 5;"
```

### Debug Issues

```bash
# Check OpenCode health
curl http://127.0.0.1:64311/global/health

# View OpenCode logs
cat runtime/logs/opencode/manager.log
cat runtime/logs/opencode/worker.log

# Check process state
cat runtime/processes/opencode_servers.json

# Reset database (WARNING: deletes all data)
rm data/power_teams.db
python -m power_teams.mvp.runner --init-db
```

## Database Queries

```sql
-- View recent suggestions
SELECT id, status, created_at FROM suggestion_queue ORDER BY id DESC LIMIT 10;

-- View agent status
SELECT name, role, state, last_seen FROM agent_registry;

-- View reviewer sessions
SELECT id, suggestion_id, status, completed_at FROM reviewer_sessions ORDER BY id DESC;

-- View handoff records
SELECT version, current_task, known_bugs FROM handoff_records ORDER BY version DESC LIMIT 5;

-- Count by status
SELECT status, COUNT(*) FROM suggestion_queue GROUP BY status;
```

## File Locations

| Purpose | Path |
|---------|------|
| Main log | `runtime/logs/runner.log` |
| Manager log | `runtime/logs/opencode/manager.log` |
| Worker log | `runtime/logs/opencode/worker.log` |
| User input | `runtime/agent_files/user_input.txt` |
| Worker report | `runtime/agent_files/worker_report.md` |
| Database | `data/power_teams.db` |
| Process state | `runtime/processes/opencode_servers.json` |

## Environment Variables

```bash
# Custom database path
export POWER_TEAMS_DB=/path/to/custom.db

# Install the managed OpenCode runtime
./installation.cmd

# Custom ports
python -m power_teams.cli serve-opencode --manager-port 53239 --worker-port 58993
```

## Useful Scripts

```bash
# Check available LLM models
python scripts/check_models.py

# Development supervisor with auto-restart
python scripts/dev_supervisor.py --auto-loop

# Run tests
pytest tests/

# Smoke test dashboard
python api/smoke_test.py
```

## Troubleshooting

### OpenCode Not Found

```bash
# Install Task Hounds' pinned OpenCode runtime
./installation.cmd
```

### Port Already in Use

```bash
# Use different ports
python -m power_teams.cli serve-opencode --manager-port 53240 --worker-port 58994
```

### Database Locked

```bash
# Kill any running processes
pkill -f "python.*runner"
pkill -f "opencode serve"

# Remove lock files
rm runtime/agent_files/*.lock
```

### Agent Not Responding

```bash
# Check if OpenCode servers are running
curl http://127.0.0.1:64311/global/health

# Restart OpenCode servers
pkill -f "opencode serve"
python -m power_teams.cli serve-opencode
```

## Next Steps

- Read [Architecture Overview](architecture/MANAGER_WORKER_ARCHITECTURE.md)
- Learn about [Reviewer Agent](architecture/REVIEWER_AGENT_IMPLEMENTATION.md)
- Check [Project Structure](PROJECT_STRUCTURE.md)
- See [Test Guides](guides/) for examples

