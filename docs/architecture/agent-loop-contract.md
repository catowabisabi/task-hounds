# Agent Loop Contract

This document is the full specification of the Task Hounds agent loop: the exact
message contract, data flow, and sequencing between the Human, Manager, Worker,
and Reviewer. For a high-level overview, see the [README](../../README.md).

## Human input contract

| Input | Meaning | Lifecycle |
| --- | --- | --- |
| `HUMAN_DIRECTIVE` | The durable project or session mission. | Copied into each new session in the same project. The agent loop never edits or deletes it; only a human can change it. |
| `HUMAN_NEW_THOUGHT_AND_SUGGESTION` | Direction, questions, product taste, concerns, or ideas. | The Manager digests it, may turn it into todo items, then marks it processed while preserving its history. |
| `HUMAN_SUGGESTED_NEW_TASK_OR_ITEM` | An explicit feature or work item. | The Manager adds it to the plan and todo system when appropriate, then marks it processed while preserving its history. |

## Complete loop

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

## Role and data flow

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

## Time sequence

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

## Hard rules

- `HUMAN_DIRECTIVE` is the stable project or session purpose. The agent loop does not rewrite or delete it.
- `MANAGER_MESSAGE` is shared guidance for the Manager, Worker, and Reviewer.
- The Worker receives the directive, Manager message, todo list, and current task context—not the handoff.
- The Reviewer does not assign work directly. Its structured feedback returns to the Manager.
- `SUGGESTION_CONTENT` and `SUGGESTION_VERIFICATION` support Manager digestion and task tracking.
- `TODO_UPDATE_JSON` is the machine-readable todo source of truth. Missing or invalid JSON must be repaired before work is released.
- Handoff is Manager memory: read at the start of a Manager loop and updated as JSON.

