# Manager-Worker Autonomous Collaboration Architecture

## Core Philosophy

This is NOT a simple task delegation system. This is an **autonomous software development team** that operates continuously, even when humans are not present.

### The Manager Agent: Your Autonomous Technical Lead

The Manager is not just a task dispatcher. It is a **proactive technical leader** with these responsibilities:

#### 1. Understanding Human Intent (Not Just Commands)
- Analyze `user_input.txt` to understand the REAL intent behind user requests
- Ask clarifying questions if needed (via manager_msg_user.md)
- Break down high-level goals into concrete, actionable tasks

#### 2. Task Orchestration
- Create and maintain `tasks.md` as a prioritized work queue
- Assign tasks to Worker based on priority and dependencies
- Track task progress: queued → in_progress → completed

#### 3. Quality Assurance & Verification (Critical!)
When Worker completes work, Manager performs:
- **Smoke Test**: Does the basic functionality work?
- **UI Test**: Does the interface match expectations?
- **Workflow Logic**: Is the business logic correct?
- **Code Review**: Code quality, style, documentation, edge cases
- **Acceptance Criteria**: Did Worker meet all requirements?

If QA fails → Provide specific feedback, Worker revises
If QA passes → Mark task complete, move to next task

#### 4. Proactive Project Evolution (The Key Differentiator!)
When no user input exists and Worker is idle:
- **Analyze project structure**: What is this project's purpose?
- **Identify gaps**: What features are missing but should exist?
- **Generate suggestions**: Create `feature_suggestions/feat_01.md`, `feat_02.md`, etc.
- **Prototype experiments**: Write experimental code in suggestion folders
- **Present to human**: Summarize suggestions when user returns
- **Execute approved features**: If human approves, instruct Worker to merge

This makes the system **self-improving** - it doesn't just wait for instructions, it actively proposes improvements.

#### 5. Continuous Monitoring (Why Every 10 Minutes?)
The Manager checks every 10 minutes because:
- New user input might arrive
- Worker might have completed a task needing review
- Project state might have changed requiring new suggestions
- System should be responsive even when human is away

### The Worker Agent: Your Autonomous Developer

The Worker is a skilled developer that:
- Executes assigned tasks from `tasks.md`
- Writes code, tests, documentation
- Reports progress in `worker_report.md`
- Revises work based on Manager feedback
- Merges approved feature suggestions

---

## File-Bridge Protocol

### Communication Files

| File | Purpose | Written By | Read By |
|------|---------|------------|---------|
| `user_input.txt` | Human requests | Human | Manager |
| `tasks.md` | Task queue with status | Manager | Worker |
| `worker_report.md` | Work progress report | Worker | Manager |
| `manager_feedback.md` | Revision instructions | Manager | Worker |
| `manager_msg_user.md` | Final response to human | Manager | Human |
| `work_0001_status.txt` | Worker current state | Worker | Manager |
| `feature_suggestions/feat_XX.md` | Proactive improvement ideas | Manager | Human/Worker |

### State Management

| File | Values | Purpose |
|------|--------|---------|
| `work_0001_status.txt` | "idle" / "busy" | Manager knows when to assign work |

---

## Operational Modes

### Mode 1: Human-Initiated Work
```
Human writes user_input.txt
    ↓
Manager (within 10 min): Analyzes intent, creates tasks
    ↓
Manager: Assigns task to Worker via manager_feedback.md
    ↓
Worker: Executes task, writes worker_report.md
    ↓
Manager (within 10 min): QA review
    ↓
If FAIL → Manager provides feedback, Worker revises
If PASS → Manager marks complete, responds to human
```

### Mode 2: Autonomous Improvement (Human Away)
```
No user_input.txt, Worker idle
    ↓
Manager (every 10 min): Analyzes project structure
    ↓
Manager: Identifies missing features/improvements
    ↓
Manager: Creates feature_suggestions/feat_01.md, feat_02.md...
    ↓
Manager: May prototype experimental code
    ↓
Human returns: Reviews suggestions
    ↓
If approved → Manager instructs Worker to implement
```

### Mode 3: Continuous Development Loop
```
Multiple tasks in queue
    ↓
Worker completes Task 1 → Report
    ↓
Manager QA → Pass
    ↓
Manager assigns Task 2 → Feedback
    ↓
Worker completes Task 2 → Report
    ↓
Manager QA → Fail → Feedback for revision
    ↓
Worker revises → New Report
    ↓
Manager QA → Pass
    ↓
Continue until queue empty...
```

---

## Why This Architecture Works

### 1. Separation of Concerns
- **Manager**: Planning, verification, strategy
- **Worker**: Implementation, coding, testing
- Neither does the other's job

### 2. Quality Control
- Manager acts as gatekeeper
- No code merges without QA approval
- Iterative refinement until quality met

### 3. Autonomy
- System runs 24/7 without human presence
- Proactively identifies opportunities
- Self-improving through feature suggestions

### 4. Responsiveness
- 10-minute check interval balances responsiveness and resource usage
- Human gets timely responses
- System reacts to changes quickly

### 5. Scalability
- Can add more Workers in future
- Manager orchestrates multiple workers
- Task queue enables parallel work

---

## Implementation Details

### Runner Cycle (`src/power_teams/mvp/runner.py`)

```python
def run_loop(once=False, manager_interval=600, ...):
    while True:
        # Manager checks every 10 minutes (600 seconds)
        if now - last_manager >= manager_interval:
            manager_cycle()  # Understand intent, QA, suggest features
            last_manager = now
        
        # Worker checks for feedback/tasks
        feedback = read_text(MANAGER_FEEDBACK)
        if feedback:
            time.sleep(worker_idle_delay)  # 59 seconds
            worker_cycle()  # Execute or revise
        elif now - last_worker_empty >= worker_empty_interval:
            worker_cycle()  # Check tasks.md every 5 minutes
        
        time.sleep(5)  # Polling interval
```

### Timeout & Retry Strategy
- Timeout: 600 seconds (10 minutes) per agent call
- Retries: 2 attempts with 5-second delays
- Logging: All attempts tracked in `runtime/logs/runner.log`

---

## Future Enhancements

1. **Multi-Worker Support**: Manager orchestrates multiple specialized workers
2. **Automated Testing**: Manager runs actual smoke/UI tests via scripts
3. **Feature Suggestion UI**: Desktop app displays suggestions for human review
4. **Learning from Feedback**: Manager improves task breakdown based on past success/failure
5. **Priority Adjustment**: Dynamic task reprioritization based on urgency

---

## Autonomous TODOs (Cron-Job Executable)

These TODOs are tracked in the database (`todos` table) and can be completed autonomously by cron jobs without human intervention.

### P0 - Critical

1. **Manager Cycle Flag Stuck Detection + Kickstart**
   - Cron job checks `_manager_cycle_running` flag every 30 minutes
   - If stuck > 30 min, force reset flag
   - UI shows persistent warning banner: "⚠️ Manager Cycle Blocked — last run: [timestamp]"
   - Manual reset button writes `force_reset_manager=true` to settings

2. **Port Health Check Skip in TMUX Mode**
   - Detect `RUNNER_MODE=tmux` env var
   - When TMUX mode active, skip `_ping_opencode_port()` entirely
   - Each tmux session is self-contained, no port health needed

### P1 - Important

3. **TMUX MCP Integration (Replace subprocess)**
   - Replace `subprocess.run(["tmux", ...])` with tmux MCP tools
   - `POWER_TEAMS_USE_TMUX_MCP=1` enables MCP mode
   - Benefits: native error handling, consistent with Hermes Manager

4. **Settings Fallback System**
   - `get_settings()` falls back to `runtime/settings-default.json` on JSON error
   - Template stored in git, user settings in `.gitignore`
   - Default settings include: `runner_mode: tmux`, `use_tmux_idle: true`

5. **3x Silence Timeout Recovery**
   - After 3 consecutive silence kills, task marked FAILED
   - Recovery strategies:
     - Option A: Reduce `SILENCE_TIMEOUT` and retry
     - Option B: Send "what's happening?" probe to tmux session
     - Option C: Escalate to Manager for human decision
   - Add `on_silence_escalation` callback hook

### P2 - Enhancement

6. **OpenCode Version Check + Breaking API Detection**
   - At startup: check opencode version
   - If API contract changed, log warning + skip auto-update
   - Flag: `OPENCODE_API_VERSION_CHECK=1`

7. **tmux Version Pinning**
   - Treat tmux as bundled/external dependency with version pin
   - Check at startup: `tmux -V` matches expected version

---

## Cron Job Implementation

Each P0/P1 TODO should have a corresponding cron job that:
1. Reads the TODO from `todos` table
2. Executes the fix autonomously
3. Updates TODO status to `completed` or `blocked`
4. Reports progress to designated Telegram topic

Example cron job pattern:
```
每30分鐘:
1. SELECT * FROM todos WHERE status='pending' ORDER BY priority
2. Execute TODO fix based on type
3. UPDATE todos SET status='completed' WHERE id=?
4. Log result to runtime/logs/todos.log
```

---

## Cron Job Priority Waterfall（執行順序）

**⚠️ 嚴格按照以下順序執行！**

### 1️⃣ 緊急檢查（每次必做）
- tmux session 是否存在
- runner 是否運行

### 2️⃣ 思考功能改進（最重要！最多時間）
如果系統正常運行，**必須思考**：
- **UI/UX**：有什麼可以優化？痛點在哪？
- **Backend/API**：有什麼可以重構？新 API？
- **安全性**：漏洞？鑒權？加密？
- **重構**：簡化代碼？提取重複？
- **文檔**：docs/ 是否完整？
- **代碼質量**：測試覆蓋？Playwright？

**⚠️ 每30分鐘至少想出一個新功能建議！**

### 3️⃣ 生成 TODO（如有建議）
- 檢查是否已有類似 TODO
- 創建新 TODO 記錄功能建議

### 4️⃣ 實際測試（無新功能建議時才做）
- Playwright Test（UI 測試）
- Workflow Test Backend（runner.py 功能測試）
- Workflow Test Frontend（Electron UI flow）

### 5️⃣ 文檔（API/Backend 改動時）
- API 說明文件
- 安裝指南
- 測試指南（教用戶如何測試）

### 6️⃣ 監控（最後才做）
- 標準 TMUX 狀態檢查
- 這是**最低優先級**！

---

## Summary

This is not a chatbot. This is an **autonomous software development team** that:
- Understands human intent
- Plans and executes work
- Verifies quality
- Proposes improvements
- Runs continuously
- Evolves the project proactively

The Manager is your 24/7 technical lead. The Worker is your diligent developer. Together, they keep your project moving forward even when you're asleep.
