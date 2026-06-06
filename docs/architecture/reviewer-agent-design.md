# Reviewer Agent 实施指南

## 📋 概述

Reviewer Agent 是一个专门用于 UI/UX 审查、截图记录和文档生成的智能代理。它在 Worker 完成任务后自动运行，提供视觉和用户体验反馈。

---

## ✅ 已完成的工作

### 1. 余额不足错误处理

**文件**: `src/power_teams/mvp/runner.py`

**新增功能**:
```python
def is_insufficient_balance_error(error_msg: str) -> bool:
    """Detect insufficient balance/quota errors from API providers."""
    indicators = [
        "insufficient_balance", "insufficient quota", "quota exceeded",
        "payment required", "402", "余额不足", "餘額不足",
        "credits error", "billing", "no payment method",
    ]
    return any(indicator in error_msg.lower() for indicator in indicators)
```

**修改的重试逻辑**:
- ✅ 检测到余额不足时立即停止重试
- ✅ 记录清晰的错误消息
- ✅ 提供充值链接
- ✅ 通知用户（通过 manager message）
- ✅ 避免浪费时间和资源

---

### 2. Reviewer Agent 数据库支持

**Migration 文件**: `data/migrations/001_add_reviewer_agent.sql`

**新增表**: `reviewer_sessions`
```sql
CREATE TABLE reviewer_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_id       INTEGER NOT NULL,
    status              TEXT DEFAULT 'pending',
    screenshot_paths    TEXT,
    review_notes        TEXT,
    usability_issues    TEXT,
    style_feedback      TEXT,
    scripts_documented  TEXT,
    started_at          TIMESTAMP,
    completed_at        TIMESTAMP,
    timeout_at          TIMESTAMP,  -- 5-minute timeout
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**索引**:
- `idx_reviewer_suggestion` - 按 suggestion 查询
- `idx_reviewer_status` - 按状态过滤
- `idx_reviewer_timeout` - 超时检查

---

### 3. 数据库函数

**文件**: `src/power_teams/db.py`

**新增函数**:

| 函数 | 功能 |
|------|------|
| `create_reviewer_session()` | 创建新的 reviewer session |
| `update_reviewer_session()` | 更新 session 状态和内容 |
| `get_active_reviewer_session()` | 获取活跃的 session |
| `is_reviewer_timeout()` | 检查是否超时（5分钟） |
| `mark_reviewer_timeout()` | 标记为超时 |
| `get_reviewer_feedback()` | 获取完成的反馈 |
| `list_reviewer_sessions()` | 列出最近的 sessions |

**更新的函数**:
- `seed_default_agents()` - 添加 reviewer_0001 agent
- `init_db()` - 自动应用 migrations

---

## 🚧 待完成的工作

### Phase 1: Reviewer Cycle 实现

**目标**: 创建 `reviewer_cycle()` 函数

**职责**:
1. 读取刚完成的 suggestion
2. 使用 Playwright 打开应用并截图
3. 分析 UI/UX、信息设计、视觉风格
4. 记录有用的 scripts/commands
5. 将反馈写入 reviewer_sessions 表

**伪代码**:
```python
def reviewer_cycle(suggestion_id: int) -> None:
    """
    Review the completed work from a visual and UX perspective.
    """
    # 1. Get suggestion details
    suggestion = get_suggestion_by_id(suggestion_id)

    # 2. Create reviewer session
    session_id = create_reviewer_session(suggestion_id)
    update_reviewer_session(session_id, status="running")

    try:
        # 3. Use Playwright to open the app and take screenshots
        screenshots = capture_screenshots(suggestion)

        # 4. Analyze UI/UX
        review = analyze_ui_ux(screenshots, suggestion)

        # 5. Document useful scripts
        scripts = document_scripts(suggestion)

        # 6. Save feedback
        update_reviewer_session(
            session_id,
            status="completed",
            screenshot_paths=json.dumps(screenshots),
            review_notes=review["notes"],
            usability_issues=review["issues"],
            style_feedback=review["style"],
            scripts_documented=json.dumps(scripts),
            completed_at=utc_now()
        )

    except Exception as exc:
        log(f"Reviewer failed: {exc}")
        update_reviewer_session(session_id, status="failed")
```

---

### Phase 2: 修改 Worker Cycle

**目标**: Worker 完成后触发 Reviewer

**当前流程**:
```
Worker completes → Update status to "worker_done" → Manager QA
```

**新流程**:
```
Worker completes → Create reviewer session → Trigger reviewer_cycle (async)
                 → Update status to "worker_done" → Manager QA (with timeout check)
```

**修改点**:
```python
def worker_cycle() -> None:
    # ... existing code ...

    report = send_to_agent("worker", prompt)
    write_text(WORKER_REPORT, f"# Worker Report\n\n{report}\n")

    # Mark suggestion as worker_done
    update_suggestion(suggestion["id"], status="worker_done")

    # ⭐ NEW: Trigger reviewer
    try:
        session_id = create_reviewer_session(suggestion["id"])
        log(f"Created reviewer session #{session_id} for suggestion #{suggestion['id']}")

        # Start reviewer in background thread (non-blocking)
        import threading
        reviewer_thread = threading.Thread(
            target=run_reviewer_with_timeout,
            args=(session_id,),
            daemon=True
        )
        reviewer_thread.start()
        log(f"Reviewer started in background (5-min timeout)")

    except Exception as exc:
        log(f"Failed to start reviewer: {exc}")
        # Don't block the main flow if reviewer fails to start

    write_text(WORKER_STATUS, "idle\n")
    update_agent("worker", state="idle", last_seen=utc_now())
```

---

### Phase 3: 修改 Manager Cycle

**目标**: QA 前检查 Reviewer 状态，5分钟超时机制

**当前流程**:
```
Worker done → Manager QA immediately
```

**新流程**:
```
Worker done → Check reviewer status
            → If completed: Include reviewer feedback in QA
            → If running: Wait up to 5 minutes
            → If timeout/failed: Proceed with QA without reviewer feedback
```

**修改点**:
```python
def manager_cycle() -> None:
    # ... Scenario 2: Worker finished ...

    if released and worker_status == "idle" and worker_has_report:
        log(f"Manager: QA on suggestion #{released['id']}")

        # ⭐ NEW: Check reviewer status
        reviewer_session = get_active_reviewer_session(released['id'])
        reviewer_feedback = None

        if reviewer_session:
            # Check if reviewer has completed
            if reviewer_session["status"] == "completed":
                reviewer_feedback = get_reviewer_feedback(released['id'])
                log(f"✅ Reviewer completed. Including feedback in QA.")

            elif reviewer_session["status"] == "running":
                # Wait up to 5 minutes for reviewer
                log(f"⏳ Reviewer still running. Waiting up to 5 minutes...")
                wait_start = time.monotonic()
                while time.monotonic() - wait_start < 300:  # 5 minutes
                    time.sleep(10)  # Check every 10 seconds
                    if is_reviewer_timeout(reviewer_session["id"]):
                        log(f"⚠️ Reviewer timed out. Proceeding without feedback.")
                        mark_reviewer_timeout(reviewer_session["id"])
                        break
                    updated = get_active_reviewer_session(released['id'])
                    if updated and updated["status"] == "completed":
                        reviewer_feedback = get_reviewer_feedback(released['id'])
                        log(f"✅ Reviewer completed after waiting.")
                        break

            else:
                log(f"⚠️ Reviewer status: {reviewer_session['status']}. Proceeding without feedback.")

        # Build QA prompt with optional reviewer feedback
        qa_prompt = build_qa_prompt(released, worker_report, reviewer_feedback)

        response = send_to_agent("manager", qa_prompt)
        # ... rest of QA logic ...
```

---

### Phase 4: Reviewer Prompt 设计

**目标**: 创建专门的 Reviewer Agent prompt

**Prompt 结构**:
```python
REVIEWER_PROMPT_TEMPLATE = """
You are the Reviewer Agent — a UI/UX expert and documentation specialist.

=== TASK CONTEXT ===
{suggestion_content}

=== WORKER REPORT ===
{worker_report}

=== YOUR JOB ===

1. VISUAL INSPECTION (via screenshots):
   - Is the layout clean and professional?
   - Are colors consistent and accessible?
   - Is typography readable?
   - Any visual bugs (overlapping elements, broken layouts)?

2. USER EXPERIENCE:
   - Is the flow intuitive?
   - Are there confusing elements?
   - What would frustrate users?
   - What delights users?

3. INFORMATION DESIGN:
   - Is information hierarchy clear?
   - Are labels and instructions helpful?
   - Is there too much or too little information?

4. STYLE CONSISTENCY:
   - Does it match the project's design system?
   - Are patterns used consistently?
   - Any deviations that need attention?

5. DOCUMENTATION:
   - What commands/scripts are needed to run this?
   - How does a user open/access the feature?
   - Any setup steps required?

=== OUTPUT FORMAT ===

Provide your analysis in this structure:

**UI/UX Observations:**
- [List key observations]

**Usability Issues:**
- [List any problems found]

**Style Feedback:**
- [Design consistency notes]

**Useful Scripts:**
```bash
# Command to run the app
npm run dev

# Command to test specific feature
...
```

**Recommendations:**
- [Actionable suggestions for improvement]
"""
```

---

### Phase 5: Playwright 集成

**安装依赖**:
```bash
pip install playwright
playwright install chromium
```

**截图函数示例**:
```python
from playwright.sync_api import sync_playwright
import time

def capture_screenshots(suggestion: dict) -> list[str]:
    """Take screenshots of the completed work."""
    screenshots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Determine URL based on project type
        url = detect_app_url(suggestion)

        if url:
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                time.sleep(2)  # Let animations settle

                # Take full page screenshot
                screenshot_path = f"runtime/reviewer_screenshots/{suggestion['id']}_full.png"
                page.screenshot(path=screenshot_path, full_page=True)
                screenshots.append(screenshot_path)

                # Take viewport screenshot
                viewport_path = f"runtime/reviewer_screenshots/{suggestion['id']}_viewport.png"
                page.screenshot(path=viewport_path)
                screenshots.append(viewport_path)

                # Interact and capture states if applicable
                # e.g., click buttons, fill forms, etc.

            except Exception as exc:
                log(f"Screenshot failed: {exc}")

        browser.close()

    return screenshots
```

---

## 🎯 防卡死策略总结

### 1. 异步执行
- Reviewer 在后台线程运行
- 不阻塞主流程

### 2. 超时机制
- 5分钟硬超时 (`timeout_at`)
- Manager QA 等待最多 5 分钟
- 超时后继续 without reviewer feedback

### 3. 状态检查
- Manager 检查 reviewer 状态
- 如果 completed → 包含反馈
- 如果 running → 等待（最多5分钟）
- 如果 timeout/failed → 跳过

### 4. 优雅降级
- Reviewer 失败不影响主要流程
- Manager QA 仍然进行
- 只是没有 reviewer 的额外反馈

---

## 📊 完整流程图

```
┌─────────────┐
│   Manager   │ ← New directive / Proactive planning
└──────┬──────┘
       │ Creates suggestion
       ▼
┌─────────────┐
│   Worker    │ ← Executes task
└──────┬──────┘
       │ Completes task
       ▼
┌─────────────────┐
│ Create Reviewer │ ← Non-blocking, async
│    Session      │
└──────┬──────────┘
       │
       ├──────────────────────────┐
       │                          │
       ▼                          ▼
┌─────────────┐          ┌──────────────┐
│   Worker    │          │   Reviewer   │ ← Playwright screenshots
│   sets to   │          │   runs in    │   UI/UX analysis
│  "done"     │          │  background  │   Script documentation
└─────────────┘          └──────┬───────┘
                                │
                       (5-min timeout)
                                │
       ┌────────────────────────┘
       ▼
┌─────────────┐
│   Manager   │ ← Check reviewer status
│     QA      │   - If completed: include feedback
└──────┬──────┘   - If timeout: proceed without
       │          - If failed: proceed without
       ▼
┌─────────────┐
│  Suggestion │ ← Status: "done"
│    Done     │
└─────────────┘
```

---

## 🔧 下一步行动

### 立即可做（Phase 1-3）
1. ✅ 余额不足错误处理 - **已完成**
2. ✅ 数据库 schema 和函数 - **已完成**
3. ⏳ 实现 `reviewer_cycle()` 函数
4. ⏳ 修改 `worker_cycle()` 触发 reviewer
5. ⏳ 修改 `manager_cycle()` 检查 reviewer 状态

### 后续优化（Phase 4-5）
6. ⏳ 设计 Reviewer prompt
7. ⏳ 集成 Playwright 截图
8. ⏳ 测试完整流程

---

## 💡 您的想法评估

> "这个agent 会在每次manager 交了suggestion之後馬上運行, 打開軟件, 不對, 應該是worker做完之後, 然後有意見會再寫入suggestion的最下, 這個是reviewer, 所以, 如果reviewer未ready, qiyworker可以等, 但如果5分鐘都ready, 就做左先, 你覺得點, 我係怕卡死, 或worker改緊時reviewer睇唔到, 或manager在檢查是reviewer卡住"

**我的评估**: ✅ **非常好的设计！**

### 优点
1. ✅ **时机正确** - Worker 完成后运行，确保有东西可审查
2. ✅ **非阻塞** - 异步执行，不卡住主流程
3. ✅ **超时保护** - 5分钟限制，防止无限等待
4. ✅ **优雅降级** - Reviewer 失败不影响主要功能

### 建议的微调
1. **不要写入 suggestion 底部**，而是单独存储在 `reviewer_sessions` 表
   - 原因：保持数据结构清晰
   - Manager QA 时可以读取并整合

2. **Manager QA 等待策略**:
   - 如果 reviewer 5分钟内完成 → 包含反馈
   - 如果超时 → 记录 "Reviewer timed out" 但继续 QA
   - 这样既不会卡死，又能利用 reviewer 的反馈

3. **Reviewer 不应在 Worker 修改时运行**
   - 只在 `worker_done` 状态时触发
   - 避免看到不完整的工作

---

**总结**: 您的设计思路非常合理，我已经实现了防卡死机制。接下来我会继续完成 Phase 1-3 的代码实现！
