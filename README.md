<p align="center">
  <a href="README.md"><strong>English</strong></a>
  &nbsp;·&nbsp;
  <a href="README.zh-TW.md">繁體中文</a>
</p>

<p align="center">
  <img src="docs/image/Task%20Hounds%20Logo.png" alt="Task Hounds logo" width="160">
</p>

<h1 align="center">Task Hounds</h1>

<p align="center">
  <strong>Work like a dog. Ship like a pack.</strong><br>
  A local, inspectable multi-agent development workspace powered by OpenCode.
</p>

<p align="center">
  <a href="https://task-hounds.com">Website</a>
  · <a href="https://github.com/catowabisabi/task-hounds">GitHub</a>
  · <a href="https://www.youtube.com/watch?v=pu-Rt8Ye4EQ&t=174s">Demo</a>
  · <a href="https://github.com/catowabisabi/task-hounds/issues">Issues</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-2563eb.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/Python-3.11+-f5c542.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/React-19-61dafb.svg" alt="React 19">
  <img src="https://img.shields.io/badge/Desktop-Electron-47848f.svg" alt="Electron">
  <img src="https://img.shields.io/badge/OpenCode-Powered-111827.svg" alt="Powered by OpenCode">
</p>

<p align="center">
  <img src="docs/image/banner2.png" alt="Task Hounds — multi-agent development workspace" width="92%">
</p>

## What is Task Hounds?

Task Hounds turns one human goal into a visible development loop. Give the pack a **Human Directive**; the Manager plans, the Worker implements, and the Reviewer checks the result before the next task begins.

Unlike a black-box coding assistant, Task Hounds keeps the work inspectable. Directives, plans, todos, reports, agent state, and reusable OpenCode sessions are stored locally, while the dashboard shows what every agent is doing in real time.

It is designed for developers who want agent autonomy **without giving up control or context**.

## The pack

| Role | Responsibility |
| --- | --- |
| **You** | Set the durable project mission, add ideas, and redirect the work at any time. |
| **Manager** | Understand context, maintain the plan, and assign one concrete task at a time. |
| **Worker** | Implement the selected task and report files changed, tests, and known issues. |
| **Reviewer** | Inspect the result for bugs, UX problems, edge cases, and safety risks. |
| **Chat** | Let you discuss the project and interact with the system directly. |

```mermaid
flowchart LR
    H["Human Directive"] --> M["Manager<br>Plan & delegate"]
    M --> W["Worker<br>Implement"]
    W --> R["Reviewer<br>Verify"]
    R --> M
    M --> D["Live dashboard<br>Todos, reports & state"]
    W --> D
    R --> D
```

## Detailed workflow

### Human input contract

| Input | Meaning | Lifecycle |
| --- | --- | --- |
| `HUMAN_DIRECTIVE` | The durable project or session mission. | Copied into each new session in the same project. The agent loop never edits or deletes it; only a human can change it. |
| `HUMAN_NEW_THOUGHT_AND_SUGGESTION` | Direction, questions, product taste, concerns, or ideas. | The Manager digests it, may turn it into todo items, then marks it processed while preserving its history. |
| `HUMAN_SUGGESTED_NEW_TASK_OR_ITEM` | An explicit feature or work item. | The Manager adds it to the plan and todo system when appropriate, then marks it processed while preserving its history. |

### Complete loop

```text
HUMAN_DIRECTIVE
MANAGER_MESSAGE history
HUMAN_NEW_THOUGHT_AND_SUGGESTION
HUMAN_SUGGESTED_NEW_TASK_OR_ITEM
WORKER_REPORT
REVIEWER_FEEDBACK
TODO state
HANDOFF at Manager loop start only
─────────────────────────────────
Manager INPUT_DIGEST
Manager DECISION
Manager MESSAGE
PLAN
TODO_LIST
TODO_UPDATE_JSON
SUGGESTION_CONTENT
SUGGESTION_VERIFICATION
HANDOFF_UPDATE JSON
─────────────────────────────────
Worker executes one task
Worker writes WORKER_REPORT
Worker records files changed, test result, and known issues
─────────────────────────────────
Reviewer checks QA, bugs, UI/UX, possible problems,
stuck states, messy user input, and safety/security risks
─────────────────────────────────
Reviewer feedback returns to Manager
Manager decides whether to fix, continue, stop, or create the next task
```

### Role and data flow

```mermaid
flowchart TD
    H["Human"]

    HD["HUMAN_DIRECTIVE<br/>Stable project/session mission<br/>Copied forward unless the human edits it"]
    HT["HUMAN_NEW_THOUGHT_AND_SUGGESTION<br/>Direction, question, idea, taste, concern"]
    HI["HUMAN_SUGGESTED_NEW_TASK_OR_ITEM<br/>Explicit feature or todo candidate"]

    M0["Manager Loop Start"]
    HO["HANDOFF<br/>Manager memory<br/>Read once at loop start"]
    DB["DB State<br/>Plan, todo JSON, manager messages,<br/>worker reports, reviewer feedback"]

    DIGEST["INPUT_DIGEST<br/>Understand human, worker, reviewer, todo, directive"]
    DECIDE["DECISION<br/>Previous step pass/fail/blocked?<br/>Next action: fix, continue, stop, or new task"]

    MM["MANAGER_MESSAGE<br/>Shared guidance for Manager, Worker, Reviewer"]
    PLAN["PLAN<br/>Strategy"]
    TL["TODO_LIST<br/>Human-readable work list"]
    TJ["TODO_UPDATE_JSON<br/>Machine-readable todo source of truth"]

    SC["SUGGESTION_CONTENT<br/>Manager digestion and task tracking"]
    SV["SUGGESTION_VERIFICATION<br/>Acceptance criteria"]
    HU["HANDOFF_UPDATE JSON<br/>Update Manager memory"]

    W["Worker<br/>Executes one task"]
    WR["WORKER_REPORT"]
    FC["FILES_CHANGED"]
    TR["TEST_RESULT"]
    KI["KNOWN_ISSUES"]

    R["Reviewer<br/>QA, bug, UI/UX, risk review"]
    RF["REVIEWER_FEEDBACK<br/>QA, bugs, UI/UX, risks, next action"]

    H --> HD
    H --> HT
    H --> HI

    HD --> M0
    HT --> M0
    HI --> M0
    HO --> M0
    DB --> M0

    M0 --> DIGEST
    DIGEST --> DECIDE

    DECIDE --> MM
    DECIDE --> PLAN
    PLAN --> TL
    TL --> TJ

    TJ -->|"valid JSON"| SC
    TJ -.->|"missing or invalid: repair before release"| M0

    SC --> SV
    DECIDE --> HU
    HU --> HO

    HD --> W
    MM --> W
    TL --> W
    SC --> W
    SV --> W

    W --> WR
    W --> FC
    W --> TR
    W --> KI

    HD --> R
    MM --> R
    WR --> R
    FC --> R
    TR --> R
    KI --> R
    SV --> R

    R --> RF
    RF --> M0
```

### Time sequence

```mermaid
sequenceDiagram
    participant Human
    participant DB
    participant Manager
    participant Handoff
    participant Worker
    participant Reviewer

    Human->>DB: HUMAN_DIRECTIVE<br/>stable mission, human-edited only
    Human->>DB: HUMAN_NEW_THOUGHT_AND_SUGGESTION
    Human->>DB: HUMAN_SUGGESTED_NEW_TASK_OR_ITEM

    Handoff->>Manager: Read once at Manager loop start
    DB->>Manager: Directive, manager messages, pending human inputs
    DB->>Manager: Current plan/todos, worker report, reviewer feedback

    Manager->>Manager: INPUT_DIGEST
    Manager->>Manager: DECISION
    Manager->>DB: MANAGER_MESSAGE
    Manager->>DB: PLAN
    Manager->>DB: TODO_LIST
    Manager->>DB: TODO_UPDATE_JSON

    alt TODO_UPDATE_JSON valid
        Manager->>DB: Save SUGGESTION_CONTENT and SUGGESTION_VERIFICATION
        Manager->>Handoff: HANDOFF_UPDATE JSON
        DB->>Worker: Directive + manager message + todo list + task context
        Worker->>DB: WORKER_REPORT
        Worker->>DB: FILES_CHANGED
        Worker->>DB: TEST_RESULT
        Worker->>DB: KNOWN_ISSUES
        DB->>Reviewer: Directive + manager message + worker output + acceptance criteria
        Reviewer->>DB: REVIEWER_FEEDBACK / REVIEWER_SUGGESTION
        DB->>Manager: Reviewer feedback returns to next Manager loop
    else TODO_UPDATE_JSON missing or invalid
        Manager->>DB: Save rejection/error message
        Manager->>Manager: Repair JSON before releasing work
    end
```

### Hard rules

- `HUMAN_DIRECTIVE` is the stable project or session purpose. The agent loop does not rewrite or delete it.
- `MANAGER_MESSAGE` is shared guidance for the Manager, Worker, and Reviewer.
- The Worker receives the directive, Manager message, todo list, and current task context—not the handoff.
- The Reviewer does not assign work directly. Its structured feedback returns to the Manager.
- `SUGGESTION_CONTENT` and `SUGGESTION_VERIFICATION` support Manager digestion and task tracking.
- `TODO_UPDATE_JSON` is the machine-readable todo source of truth. Missing or invalid JSON must be repaired before work is released.
- Handoff is Manager memory: read at the start of a Manager loop and updated as JSON.

## Why Task Hounds?

- **Local first** — your workspace, database, runtime state, and logs stay on your machine.
- **Inspectable by design** — follow live thinking, tool activity, todos, reports, and review feedback.
- **Persistent context** — SQLite-backed project state and reusable role sessions survive across loops.
- **Clear responsibilities** — planning, implementation, and review are handled by separate agents.
- **Human steerable** — change direction with durable directives, thoughts, and suggested tasks.
- **Multiple ways to run** — web dashboard, Windows desktop app, Docker, and an experimental Android client.
- **Open source** — MIT licensed and ready to adapt.

## See it in action

<p align="center">
  <a href="https://www.youtube.com/watch?v=pu-Rt8Ye4EQ&t=174s">
    <img src="https://img.youtube.com/vi/pu-Rt8Ye4EQ/maxresdefault.jpg" alt="Watch the Task Hounds demo" width="82%">
  </a>
</p>

<p align="center">
  <img src="docs/image/ui%20(2).png" alt="Task Hounds dashboard" width="88%">
</p>

## Quick start

### Requirements

- Windows (recommended for the managed runtime and desktop build)
- Python 3.11+
- Node.js 20+
- npm

### 1. Clone and install

```powershell
git clone https://github.com/catowabisabi/task-hounds.git
cd task-hounds

.\installation.cmd
pip install -r requirements.txt
pip install .
```

`installation.cmd` installs the pinned, managed OpenCode runtime used by Task Hounds.

### 2. Build the dashboard

```powershell
cd ui/web
npm ci
npm run build
cd ../..
```

### 3. Configure

```powershell
Copy-Item .env.example .env
```

Task Hounds keeps the legacy `POWER_TEAMS_` environment-variable prefix for compatibility. Review `.env.example` before adding provider keys or exposing the API beyond localhost.

### 4. Run

```powershell
$env:PYTHONPATH = "core"
python core\api\server.py --port 8765
```

Open [http://localhost:8765](http://localhost:8765), create or select a workspace, write a Human Directive, then choose **Start Loop** or **Run Once**.

> Task Hounds will not begin autonomous work without a pending Human Directive.

For more detail, see the [Getting Started guide](docs/guides/getting-started.md).

## Other ways to run

### Docker

```bash
docker build -t task-hounds .
docker run --rm -p 8765:8765 -v "$(pwd)/data:/app/data" task-hounds
```

### Windows desktop app

```powershell
.\build_exe.ps1
```

The portable Electron build is written to `ui/desktop/dist/`.

### Android client

The experimental React + Capacitor client is in `ui/mobile/`. It connects to the same backend and shares projects, sessions, todos, chat, and agent state. Private access through [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) is recommended; do not expose the backend directly to the public internet.

See [ui/mobile/README.md](ui/mobile/README.md) for setup instructions.

## Architecture

SQLite is the runtime source of truth for project sessions, directives, todos, reports, suggestions, and agent state. Compatibility files under `core/runtime/` are local runtime mirrors and fallbacks.

```text
task-hounds/
├── core/
│   ├── api/                 # HTTP API and dashboard server
│   ├── db/                  # SQLite schema and migrations
│   ├── power_teams/         # Legacy Python package
│   └── task_hounds_api/     # Current backend and agent flows
├── ui/
│   ├── web/                 # React + Vite dashboard
│   ├── desktop/             # Electron desktop wrapper
│   └── mobile/              # React + Capacitor Android client
├── docs/                    # Guides, architecture, tests, and images
├── Dockerfile
└── .env.example
```

Runtime data, SQLite databases, logs, local `.env` files, personal OpenCode configuration, and build output are intentionally excluded from the repository.

## Development

Backend tests:

```powershell
pytest
```

Web dashboard:

```powershell
cd ui/web
npm run build
```

Contributions, bug reports, and ideas are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) before submitting changes or reporting a vulnerability.

## Support the project

If Task Hounds saves you time—or you simply like the idea of a tiny AI dog pack building software—consider buying me a coffee. It helps fund development, testing, and the occasional real coffee behind the virtual hounds.

<p align="center">
  <a href="https://buymeacoffee.com/catowabisabi?new=1">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me a Coffee" width="210">
  </a>
</p>

## License

Task Hounds is released under the [MIT License](LICENSE).
