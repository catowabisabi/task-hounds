# 电子商务项目测试指南

## 概述

这是一个测试场景，用于验证改进后的 Manager Agent 的主动性和人性化交互能力。Manager 和 Worker 将协作在 `C:\Users\<your-username>\Desktop\my-project` 目录下创建一个完整的电子商务网站。

## 启动方式

### 方法 1: 使用批处理文件（推荐）
```bash
cd C:\Users\<your-username>\path\to\power-teams
.\run_ecommerce_test.bat
```

### 方法 2: 直接运行 Python
```bash
cd C:\Users\<your-username>\path\to\power-teams
python -m power_teams.mvp.runner --auto-release --manager-interval 5 --worker-poll 3
```

## 参数说明

- `--auto-release`: 自动释放 pending 的建议，无需人工审批（测试模式）
- `--manager-interval 5`: Manager 每 5 秒检查一次（加快测试速度）
- `--worker-poll 3`: Worker 每 3 秒轮询一次任务

## 预期行为

### Manager Agent 应该：

1. **接收新指令** - 读取 `user_input.txt` 中的电商项目需求
2. **制定计划** - 分析需求，创建开发计划
3. **分解任务** - 将大项目分解成小的原子任务
4. **主动询问** - 完成任务后主动提出改进建议
5. **人性化交互** - 使用热情、对话式的语言

### Worker Agent 应该：

1. **执行任务** - 根据 Manager 的指示创建文件和代码
2. **报告进度** - 详细记录完成的工作
3. **验收标准** - 验证所有要求都已满足

## 监控进度

### 查看实时日志
```bash
tail -f runtime/logs/runner.log
```

### 查看 Manager 消息
```bash
sqlite3 data/power_teams.db "SELECT content, created_at FROM manager_messages ORDER BY created_at DESC LIMIT 10;"
```

### 查看当前建议
```bash
sqlite3 data/power_teams.db "SELECT id, status, content FROM suggestion_queue ORDER BY created_at DESC LIMIT 5;"
```

### 查看手递信息
```bash
sqlite3 data/power_teams.db "SELECT version, current_task, macro_flow FROM project_handoff ORDER BY version DESC LIMIT 1;"
```

## 文件位置

- **用户输入**: `runtime/agent_files/user_input.txt`
- **Worker 报告**: `runtime/agent_files/worker_report.md`
- **Worker 状态**: `runtime/agent_files/work_0001_status.txt`
- **Manager 流**: `runtime/agent_files/manager_stream.txt`
- **Worker 流**: `runtime/agent_files/worker_stream.txt`
- **运行日志**: `runtime/logs/runner.log`

## 停止循环

按 `Ctrl+C` 终止进程。

## 重新开始

如果要重新开始：

1. 清空 user_input.txt
2. 重置 worker_report.md
3. 重置 work_0001_status.txt 为 "idle"
4. 清空 session_state.json
5. 写入新的 user_input.txt

或者运行：
```bash
python -m power_teams.mvp.runner --init-db
```

## 观察要点

### Manager 主动性测试
- [ ] Manager 是否在任务完成后主动提出改进建议？
- [ ] Manager 是否使用热情的语言（"Great!" / "Excellent!"）？
- [ ] Manager 是否询问用户意见（"Should I...?" / "Want me to...?"）？
- [ ] Manager 是否提供多个选项？

### 创意性测试
- [ ] Manager 是否提出超出基本需求的建议？
- [ ] Manager 是否考虑 UX、性能、测试等方面？
- [ ] Manager 是否引用最佳实践？

### 人性化测试
- [ ] Manager 的消息是否自然流畅？
- [ ] Manager 是否解释推理过程？
- [ ] Manager 是否展现个性？

## 示例输出

### 理想的 Manager 消息
```
Excellent! The product listing page is looking great with all the basic features.

I've been thinking about what would make the shopping experience even better:

1. **Product Filtering** - Add filters for price range, category, and rating. This helps users find exactly what they want faster.

2. **Sort Options** - Let users sort by price (low/high), newest, or popularity. Standard feature in e-commerce.

3. **Quick View Modal** - Show product details without leaving the page. Improves browsing speed.

Which enhancement should I work on first? Or do you have something else in mind?
```

### 不理想的 Manager 消息（改进前）
```
Task complete. Product listing page created.
<DIRECTIVE_COMPLETE/>
```

## 故障排除

### Manager 没有响应
- 检查 opencode 服务是否运行
- 查看 runner.log 是否有错误
- 确认 agent 配置正确

### Worker 没有执行任务
- 检查 suggestion_queue 中是否有 released 状态的任务
- 查看 worker_stream.txt 了解执行情况
- 确认 worker agent 可以访问

### 循环停止
- 检查是否触发了 DIRECTIVE_COMPLETE
- 查看最新的 manager_message
- 如果需要继续，添加新的 user_input

## 下一步

测试完成后，您可以：
1. 根据观察结果调整 Manager prompt
2. 优化任务分解策略
3. 添加更多主动性规则
4. 测试其他项目类型

---

*祝测试顺利！期待看到 Manager Agent 的精彩表现！*
