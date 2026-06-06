# UI Smoke Tests

Playwright-based smoke test suite for the Task Hounds web app.

## Files

- `playwright.config.ts` — Playwright configuration (port 18765, single chromium worker, manual web server lifecycle)
- `tests/ui-smoke.spec.ts` — All smoke tests + click sequences
- `package.json` — Updated with `test:ui` and `test:ui:headed` scripts

## Prerequisites

1. Build the UI:

   ```bash
   cd ui/web && npm run build
   ```

2. Install Playwright browsers:

   ```bash
   cd ui/web && npx playwright install chromium --with-deps
   ```

## Running Tests

```bash
cd ui/web

npm run test:ui            # headless
npm run test:ui:headed     # headed (visible browser)
```

Or directly:

```bash
npx playwright test --project=chromium
```

## Test Structure

### Smoke Tests (6)

| # | Test | What it checks |
|---|------|---------------|
| 1 | Page loads with Task Hounds branding | Header "⚡ Task Hounds" visible |
| 2 | Header controls present | Start Loop, Run Once, Auto Release, New Session buttons visible |
| 3 | Left rail shows Projects | "PROJECTS" header and Add Project button visible |
| 4 | Right rail Chat Agent disabled | Input placeholder, disabled Send button, no-opencode message |
| 5 | Runtime panel buttons | + Checkpoint, ■ Stop All, ◇ Discover, Managed/External counts |
| 6 | Health endpoint | `/api/health` returns `opencode_enabled: false` |

### Click Sequences (3)

| # | Sequence | What it checks |
|---|----------|---------------|
| A | Workspace activation | Clicking a workspace row activates it with amber highlight |
| B | New Session button | Button click triggers session reload without crash |
| C | Runtime Discover | Button triggers external server discovery without crash |

## Architecture

Each test starts its own Python backend with an isolated temp DB:

- `POWER_TEAMS_DB` = fresh temp file (deleted after test)
- `python server.py --no-opencode --port 18765` spawned per test
- Backend is killed in `afterAll` cleanup
- No production DB touched

## Backend Requirements

- Python http.server on `127.0.0.1:18765`
- Serves both API (`/api/*`) and static UI (`ui/web/dist`)
- `--no-opencode` flag disables live chat and agent lifecycle
- `init_db()` + `seed_default_agents()` called before each test