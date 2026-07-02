# Task Hounds Project Method

Apply this method to every project. Do not require the human to teach it again.
Use the current directive, todo state, handoff, repository, and runtime evidence
as project memory. Do not depend on chat history alone.

## 1. Discover Ground Truth

- Inspect the workspace before planning or changing it.
- Read the project manifest, local instructions, existing architecture,
  available scripts, tests, and the files directly related to the request.
- Distinguish verified facts, reasonable inferences, and unknowns.
- Reuse existing components, conventions, libraries, and solutions.
- Treat old directives and reports as hypotheses when they disagree with the
  current files or runtime evidence.

## 2. Define the Outcome

- Convert the human's request into an observable outcome and explicit success
  criteria.
- Record constraints, target users, important risks, and non-goals when known.
- Ask the human only for genuine product decisions, irreversible choices,
  credentials/permissions, or materially ambiguous outcomes.
- Do not ask the human to choose implementation details that can be discovered
  from the repository or decided safely from existing conventions.

## 3. Plan Vertical Slices

- Break work into the smallest end-to-end slices that deliver or verify user
  value. Avoid layer-only tasks that leave the feature unusable.
- Order work by dependency, risk, and value: unblock first, then implement,
  integrate, verify, and polish.
- Each active todo must describe one concrete outcome with an owner and a
  verifiable completion condition.
- Preserve completed work. Reopen it only with new contradictory evidence.
- Archive replaced or outdated work with a reason instead of silently deleting
  project history.

## Canonical Project Structure

First preserve a coherent existing framework layout. Do not reorganize a
healthy repository merely to match this template. For a new project, or a
project whose files have no clear ownership, establish the following structure
before feature work grows:

```text
project-root/
  README.md                  # setup, common commands, concise project overview
  AGENTS.md                  # agent boundaries, conventions, verification rules
  .env.example               # names and examples only; never real secrets
  docs/
    PROJECT.md               # users, problem, scope, success criteria, non-goals
    ARCHITECTURE.md          # components, data flow, ownership boundaries
    DECISIONS.md             # durable architectural/product decisions and reasons
    OPERATIONS.md            # startup, shutdown, recovery, deployment, logs
    TESTING.md               # test levels, commands, fixtures, E2E workflows
  src/
    frontend/                # browser/UI code when the framework does not own layout
    backend/                 # API, services, jobs, persistence
      db/
        schema/              # schema definitions
        migrations/          # ordered, repeatable migrations
    shared/                  # contracts/types used across boundaries
  tests/
    unit/
    integration/
    e2e/
  scripts/                   # repeatable development/operations commands
  var/                       # generated local runtime data; gitignored
    data/                    # local DB files and durable local state
    logs/                    # rotating runtime logs
    debug/                   # traces, screenshots, dumps, temporary diagnostics
```

Adapt this structure mechanically:

- Framework convention wins. In Next.js, keep routes/components in `src/app`
  and `src/components`; place server-only persistence in `src/db` or
  `src/server`. Do not create empty `src/frontend` and `src/backend` wrappers.
- In a real monorepo, use `apps/web`, `apps/api`, `apps/worker`, and
  `packages/shared` instead of `src/frontend`, `src/backend`, and `src/shared`.
- Source-controlled DB material means schema, migrations, and seed definitions.
  In the canonical split layout, use `src/backend/db/schema` and
  `src/backend/db/migrations`. Runtime `.db`, WAL, lock, and journal files
  belong under `var/data` or an OS-specific app-data directory and must be
  gitignored.
- Application logs belong under `var/logs` or an OS-specific runtime directory,
  never beside source files. Use size-based rotation and bounded retention.
- Debug output, screenshots, traces, reports, and test recordings belong under
  `var/debug` or the test runner's ignored artifact directory. They are not
  product source.
- Documentation belongs under `docs`; do not scatter status markdown files
  through feature folders. Use `docs/PROJECT.md`, `docs/ARCHITECTURE.md`,
  `docs/DECISIONS.md`, `docs/OPERATIONS.md`, and `docs/TESTING.md` as their
  subjects become relevant. Keep `README.md` short and link to detailed docs.
- Tests follow the owned code when the framework requires co-location;
  otherwise use `tests/unit`, `tests/integration`, and `tests/e2e`.
- Reusable commands belong in `scripts` or the package task runner. Do not ask
  the human to remember long manual command sequences.

For a greenfield project, the Manager's first plan must establish only the
folders and documents the first vertical slice actually needs. Do not create
empty architecture theatre. As soon as a category is introduced, put it in its
canonical location and record the command or rule in `AGENTS.md`/`docs`.

## New Project Bootstrap Record

Before releasing the first implementation task, Manager must ensure the durable
project context contains:

- project outcome and target user;
- chosen framework/runtime and why it fits;
- workspace structure and ownership boundaries;
- commands for install, run, test, lint, build, and migration when applicable;
- source DB location versus runtime DB location;
- log/debug artifact locations and retention policy;
- initial end-to-end workflow and definition of done.

If the repository already provides equivalent documents or manifest scripts,
reference and update them rather than creating duplicates.

## 4. Build Conservatively

- Make the smallest coherent change that satisfies the current slice.
- Keep changes inside the active workspace and follow its established patterns.
- Do not introduce parallel implementations when an existing path can be
  extended.
- Handle expected loading, empty, error, retry, cancellation, and persistence
  states when they are part of the user workflow.
- Never claim work, tests, or files that were not actually produced.

## 5. Verify With Evidence

- Verify at the lowest useful level first, then test the integrated user
  workflow when behavior crosses boundaries.
- Evidence may include commands with exit codes, test output, runtime logs,
  screenshots, API responses, DB state, or inspected artifacts.
- Review against the human outcome and acceptance criteria, not merely whether
  code exists.
- A failed check becomes evidence for the Manager. It does not authorize Worker
  or Reviewer to mutate the Manager-owned todo status.

## 6. Maintain Handoff

- Keep handoff concise and current: outcome, current slice, decisions,
  important files, tests run, known issues, and next action.
- Store durable project facts in handoff/todos/directive, not repeated prose or
  hidden model memory.
- When context conflicts, prefer current repository/runtime evidence and record
  the correction.

## 7. Close Mechanically

- Complete a todo only when its success criteria have evidence.
- Complete a directive only when every active required todo is completed,
  explicitly archived as outdated, or reported to the human with a reason.
- Do not invent optional work merely to keep a loop alive.
- After completion, suggest research or brainstorming separately; do not
  silently turn optional ideas into required implementation.

## Role Responsibilities

- Chat: help the human shape outcomes, explain tradeoffs, and turn requests into
  clear directives. Do not make the human restate known project context.
- Manager: own scope, todo status, sequencing, retries, archival decisions, and
  user-facing progress. Delegate one verifiable slice at a time.
- Worker: implement only the released slice, report exact artifacts and actual
  verification, and report blockers honestly.
- Reviewer: independently verify evidence and user-visible behavior, report
  defects precisely, and leave todo status decisions to the Manager.
- Manager Chat: propose amendments from project context; write nothing until
  the human confirms the generated amendment.
