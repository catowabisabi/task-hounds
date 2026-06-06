# Reviewer Agent 测试指南

## ✅ 已完成的实施

### 1. 余额不足错误处理
- ✅ `is_insufficient_balance_error()` 函数
- ✅ 检测到余额不足时立即停止重试
- ✅ 提供清晰的错误提示和充值链接

### 2. Reviewer Agent 核心功能
- ✅ `reviewer_sessions` 数据库表
- ✅ 7个 reviewer 相关数据库函数
- ✅ `run_reviewer_session()` - 完整的 reviewer 执行逻辑
- ✅ `_trigger_reviewer_async()` - 异步触发机制

### 3. Worker Cycle 修改
- ✅ Worker 完成后自动触发 Reviewer
- ✅ 后台线程运行，不阻塞主流程

### 4. Manager Cycle 修改
- ✅ QA 前检查 Reviewer 状态
- ✅ 5分钟超时等待机制
- ✅ 整合 Reviewer 反馈到 QA prompt

---

## 🚀 如何测试

### 步骤 1: 初始化数据库

```bash
cd C:\Users\<your-username>\path\to\power-teams
python -m power_teams.mvp.runner --init-db
```

这会：
- 创建所有数据库表（包括 `reviewer_sessions`）
- 应用 migration
- 注册 3 个 agents (manager, worker, reviewer)

### 步骤 2: 准备测试项目

确保 `user_input.txt` 包含一个前端项目需求，例如：

```
创建一个简单的待办事项列表应用，使用 HTML/CSS/JavaScript。
放在 C:\Users\<your-username>\Desktop\my-project

要求：
- 添加任务
- 删除任务
- 标记完成
- 本地存储
```

### 步骤 3: 运行测试

```bash
python -m power_teams.mvp.runner --auto-release --manager-interval 5 --worker-poll 3
```

### 步骤 4: 观察日志

打开新的终端窗口，实时监控：

```bash
# 查看完整日志
tail -f runtime/logs/runner.log

# 或实时查看 manager stream
tail -f runtime/agent_files/manager_stream.txt
```

---

## 🔍 预期的行为流程

### 正常流程（Reviewer 在 5 分钟内完成）

```
1. Manager receives directive
   ↓
2. Manager creates suggestion #1
   ↓
3. Worker executes task
   ↓
4. Worker completes → status = "worker_done"
   ↓
5. ⭐ Reviewer triggered (async, background thread)
   ↓
6. Manager QA checks reviewer status
   ↓
7. Reviewer completes (within 5 min)
   ↓
8. Manager includes reviewer feedback in QA
   ↓
9. Manager creates next suggestion or marks complete
```

**日志示例**:
```
[timestamp] Worker: finished suggestion #1, report written
[timestamp] ✅ Reviewer triggered for suggestion #1
[timestamp] Reviewer started in background thread for suggestion #1
[timestamp] Reviewer session #1 started for suggestion #1
[timestamp] Reviewer: Analyzing suggestion #1
[timestamp] Manager: QA on suggestion #1
[timestamp] ⏳ Reviewer still running. Waiting up to 5 minutes...
[timestamp] ✅ Reviewer completed after waiting. Including feedback.
[timestamp] Suggestion #1 marked done. QA=PASS
```

---

### 超时流程（Reviewer 超过 5 分钟）

```
1-4. Same as normal flow
   ↓
5. Reviewer triggered but takes too long
   ↓
6. Manager waits up to 5 minutes
   ↓
7. Timeout reached
   ↓
8. Manager proceeds WITHOUT reviewer feedback
   ↓
9. QA continues normally
```

**日志示例**:
```
[timestamp] Worker: finished suggestion #1, report written
[timestamp] ✅ Reviewer triggered for suggestion #1
[timestamp] Manager: QA on suggestion #1
[timestamp] ⏳ Reviewer still running. Waiting up to 5 minutes...
[timestamp] ⚠️ Reviewer timed out after 5 minutes. Proceeding without feedback.
[timestamp] Suggestion #1 marked done. QA=PASS
```

---

### Reviewer 失败流程

```
1-4. Same as normal flow
   ↓
5. Reviewer encounters error
   ↓
6. Reviewer status = "failed"
   ↓
7. Manager detects failure
   ↓
8. Manager proceeds WITHOUT reviewer feedback
   ↓
9. QA continues normally
```

**日志示例**:
```
[timestamp] Worker: finished suggestion #1, report written
[timestamp] ✅ Reviewer triggered for suggestion #1
[timestamp] Manager: QA on suggestion #1
[timestamp] ⚠️ Reviewer status: failed. Proceeding without feedback.
[timestamp] Suggestion #1 marked done. QA=PASS
```

---

## 📊 验证检查清单

### 数据库验证

```sql
-- Check reviewer sessions table exists
SELECT name FROM sqlite_master WHERE type='table' AND name='reviewer_sessions';

-- Check reviewer agent registered
SELECT * FROM agent_registry WHERE name='reviewer';

-- View reviewer sessions
SELECT id, suggestion_id, status, started_at, completed_at, timeout_at
FROM reviewer_sessions
ORDER BY id DESC
LIMIT 10;

-- View completed reviews with feedback
SELECT rs.id, rs.suggestion_id, rs.status,
       rs.review_notes, rs.usability_issues, rs.style_feedback
FROM reviewer_sessions rs
WHERE rs.status = 'completed'
ORDER BY rs.id DESC;
```

### 日志验证

检查以下关键日志消息：

- ✅ `✅ Reviewer triggered for suggestion #X`
- ✅ `Reviewer session #X started for suggestion #X`
- ✅ `Reviewer: Analyzing suggestion #X`
- ✅ `✅ Reviewer completed. Including feedback in QA.`
- OR `⚠️ Reviewer timed out after 5 minutes`
- OR `⚠️ Reviewer status: failed`

### 文件验证

检查这些文件是否创建：

```
runtime/reviewer_screenshots/
  └── (screenshot files if Playwright is integrated)

runtime/agent_files/
  ├── reviewer_stream.txt  (reviewer's thinking process)
  └── ...
```

---

## 🐛 常见问题排查

### 问题 1: Reviewer 没有启动

**症状**: 日志中没有 "Reviewer triggered" 消息

**检查**:
```bash
# Check if reviewer agent is registered
sqlite3 data/power_teams.db "SELECT * FROM agent_registry WHERE name='reviewer';"

# Check for errors in runner.log
grep -i "reviewer" runtime/logs/runner.log
```

**解决**:
- 确保运行了 `--init-db`
- 检查 `seed_default_agents()` 是否正确添加了 reviewer

---

### 问题 2: Reviewer 一直超时

**症状**: 每次都是 "Reviewer timed out after 5 minutes"

**可能原因**:
1. Reviewer agent 配置错误
2. OpenCode 服务未运行
3. API 余额不足

**检查**:
```bash
# Check reviewer session status
sqlite3 data/power_teams.db "SELECT id, status, started_at, timeout_at FROM reviewer_sessions ORDER BY id DESC LIMIT 5;"

# Check if reviewer Stream file has content
cat runtime/agent_files/reviewer_stream.txt
```

**解决**:
- 确保 opencode 服务在端口 64311 运行
- 检查 API 余额
- 查看 `reviewer_stream.txt` 了解具体错误

---

### 问题 3: Manager 没有等待 Reviewer

**症状**: Manager QA 立即进行，没有 "Waiting up to 5 minutes" 日志

**可能原因**:
- Reviewer session 创建失败
- Reviewer 已经完成或失败

**检查**:
```bash
# Check timing of reviewer session vs manager QA
sqlite3 data/power_teams.db "
  SELECT rs.id, rs.status, rs.started_at, rs.completed_at
  FROM reviewer_sessions rs
  ORDER BY rs.id DESC LIMIT 5;
"
```

---

### 问题 4: Reviewer 反馈没有出现在 QA 中

**症状**: Manager QA prompt 不包含 reviewer feedback

**检查**:
```bash
# Check if reviewer feedback was retrieved
grep "Including feedback" runtime/logs/runner.log

# Check reviewer session status
sqlite3 data/power_teams.db "
  SELECT id, status, review_notes IS NOT NULL as has_notes
  FROM reviewer_sessions
  ORDER BY id DESC LIMIT 5;
"
```

---

## 🎯 成功标准

### 必须满足

- [ ] `reviewer_sessions` 表创建成功
- [ ] Reviewer agent 注册在数据库中
- [ ] Worker 完成后触发 Reviewer（日志中有 "Reviewer triggered"）
- [ ] Reviewer session 状态正确更新（pending → running → completed/failed/timeout）
- [ ] Manager QA 检查 Reviewer 状态
- [ ] 5分钟超时机制工作正常
- [ ] Reviewer 反馈（如果有）包含在 Manager QA prompt 中

### 理想情况

- [ ] Reviewer 在 5 分钟内完成
- [ ] Reviewer 提供有价值的 UI/UX 反馈
- [ ] Reviewer 发现可用性问题并创建 follow-up suggestion
- [ ] 完整的流程无卡顿、无卡死

---

## 📝 测试报告模板

```markdown
## Reviewer Agent Test Report

**Test Date**: YYYY-MM-DD
**Test Duration**: X hours

### Results

| Metric | Value |
|--------|-------|
| Total suggestions processed | X |
| Reviewer sessions created | X |
| Reviewer completed | X |
| Reviewer timed out | X |
| Reviewer failed | X |
| Avg reviewer completion time | X seconds |

### Observations

1. [What worked well]
2. [What needs improvement]
3. [Any issues encountered]

### Recommendations

1. [Suggestion 1]
2. [Suggestion 2]

### Conclusion

[Overall assessment of the reviewer agent implementation]
```

---

## 🚦 下一步

测试完成后：

1. **如果一切正常**:
   - 考虑添加 Playwright 截图功能
   - 优化 Reviewer prompt 以获得更详细的反馈
   - 调整超时时间（如果需要）

2. **如果有问题**:
   - 根据上述排查步骤定位问题
   - 检查日志和数据库状态
   - 调整代码并重试

3. **长期优化**:
   - 集成真实的 Playwright 截图
   - 添加更智能的 UI/UX 分析
   - 实现自动化可用性测试

---

*祝测试顺利！* 🎉
