# Task Hounds 1.0.0 Release And Promotion Copy

## GitHub Release

Title:

```text
Task Hounds v1.0.0 - Work like a dog
```

Release notes:

```markdown
Task Hounds v1.0.0 is the first open-source release of a local multi-agent development workspace powered by OpenCode.

You write a Human Directive, and Task Hounds coordinates Manager, Worker, Reviewer, and Chat agents around one project session. The app keeps runtime state in SQLite, restores OpenCode role sessions, and exposes agent activity through a React dashboard.

Highlights:

- Manager / Worker / Reviewer / Chat agent roles
- DB-backed project sessions, todos, suggestions, reports, and agent state
- Reusable OpenCode sessions per project role
- Human Directive lock before autonomous work starts
- React dashboard for live streams, todos, suggestions, settings, and chat
- Configurable OpenCode silence and hard timeouts
- Docker build for server deployment
- Windows Electron portable EXE
- MIT licensed

Links:

- Website: https://task-hounds.com
- Repo: https://github.com/catowabisabi/task-hounds
- Demo: https://www.youtube.com/watch?v=pu-Rt8Ye4EQ&t=174s

Notes:

- Environment variables still use the `POWER_TEAMS_` prefix for compatibility.
- Runtime folders, logs, local SQLite DBs, and OpenCode config are not included in the release.
```

## Reddit Post

Suggested title:

```text
I open-sourced Task Hounds, a local multi-agent coding workspace powered by OpenCode
```

Post:

```markdown
Hi everyone,

I just open-sourced Task Hounds: a local multi-agent development workspace that coordinates a Manager, Worker, Reviewer, and Chat agent around one project.

The idea is simple: instead of asking a coding assistant for one-off changes, you give Task Hounds a Human Directive. The Manager turns it into work, the Worker implements, the Reviewer checks, and the dashboard lets you inspect what is happening.

What it does:

- Runs locally
- Uses OpenCode for agent execution
- Tracks project sessions and role session IDs in SQLite
- Stores directives, todos, suggestions, reports, and agent state in DB
- Shows live agent streams in a React dashboard
- Builds as Docker or a Windows Electron portable EXE

Demo: https://www.youtube.com/watch?v=pu-Rt8Ye4EQ&t=174s
GitHub: https://github.com/catowabisabi/task-hounds
Website: https://task-hounds.com

It is MIT licensed. I would love feedback from people building dev tools, agent orchestration systems, or local AI workflows.

In particular, I am curious:

- Would you trust a local multi-agent loop if every directive/todo/report is visible?
- Is SQLite enough for the first open-source version, or should I prioritize Postgres?
- What would make this easier to try in your own repo?
```

Recommended subreddits to consider:

- r/opensource
- r/programming
- r/Python
- r/reactjs if you focus on the dashboard
- r/SideProject
- r/selfhosted if Docker/local deployment is emphasized
- r/LocalLLaMA only if the post is framed around local agent workflows and follows their rules

Do not cross-post the same text everywhere. Read each subreddit rules page first, participate in comments, disclose that you are the maker, and make the post useful even without the link.

## Hacker News

Use Show HN.

Title:

```text
Show HN: Task Hounds - local multi-agent coding workspace powered by OpenCode
```

Text:

```text
Hi HN,

I built Task Hounds, a local multi-agent coding workspace. It coordinates Manager, Worker, Reviewer, and Chat agents around a project session, with SQLite as the source of truth for directives, todos, reports, suggestions, and OpenCode role sessions.

The goal is to make autonomous coding loops inspectable: you can see the directive, todo list, worker report, manager feedback, agent states, and chat in the dashboard.

Demo: https://www.youtube.com/watch?v=pu-Rt8Ye4EQ&t=174s
Repo: https://github.com/catowabisabi/task-hounds

It is MIT licensed. I would love feedback on the architecture, especially the DB-first state model and session restoration design.
```

## DEV.to Article Draft

Front matter:

```markdown
---
title: I open-sourced Task Hounds, a local multi-agent coding workspace
published: false
tags: opensource, ai, python, react
canonical_url: https://task-hounds.com
---
```

Article outline:

```markdown
I have been building Task Hounds, a local multi-agent development workspace powered by OpenCode.

Most coding assistants feel like a chat box. I wanted something closer to a visible dev team:

- Manager plans
- Worker implements
- Reviewer checks
- Chat explains and helps route human intent

The important design choice is that runtime state is not hidden inside prompts. Task Hounds stores project sessions, role session IDs, directives, todos, suggestions, reports, and agent state in SQLite.

Why I built it:

- I wanted autonomous loops, but with inspectable state.
- I wanted Human Directives to be explicit.
- I wanted agent sessions to survive restarts and compaction.
- I wanted a dashboard that shows what the system is doing.

Architecture:

- Python API server
- SQLite runtime DB
- OpenCode role sessions
- React dashboard
- Electron desktop app
- Docker server build

Demo: https://www.youtube.com/watch?v=pu-Rt8Ye4EQ&t=174s
GitHub: https://github.com/catowabisabi/task-hounds

I would love feedback from people building agent tools or local dev automation.
```

## Product Hunt

Tagline:

```text
A local multi-agent coding workspace powered by OpenCode
```

Description:

```text
Task Hounds coordinates Manager, Worker, Reviewer, and Chat agents around your project. It keeps directives, todos, reports, suggestions, and OpenCode sessions in SQLite, with a live dashboard so autonomous coding work stays inspectable.
```

Maker comment:

```markdown
Hi Product Hunt,

I built Task Hounds because I wanted autonomous coding agents that felt inspectable rather than magical.

You give it a Human Directive. A Manager plans, a Worker builds, a Reviewer checks, and Chat helps you understand or steer the session. The dashboard shows agent streams, todos, suggestions, settings, and chat history.

The first open-source version is MIT licensed and runs locally with OpenCode. It supports Docker and a Windows Electron portable EXE.

I would love feedback on whether this workflow feels useful, especially from developers who have tried agent loops but wanted more visibility and control.
```
