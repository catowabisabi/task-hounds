# OpenCode API 余额不足错误处理指南

## 📋 概述

当使用 OpenCode API（或任何 LLM API）时，如果账户余额不足，系统会返回特定的错误响应。了解这些错误的格式和处理方法对于构建健壮的 AI Agent 系统至关重要。

---

## 🔍 错误类型和响应格式

### 1. OpenCode Zen API 余额不足

#### 错误定义位置
`packages/console/app/src/routes/zen/util/error.ts`

```typescript
export class CreditsError extends Error {}
```

#### 触发条件
`packages/console/app/src/routes/zen/util/handler.ts:808`

```typescript
if (billing.balance <= 0) throw new CreditsError(
  t("zen.api.error.insufficientBalance", { billingUrl })
)
```

#### 错误消息（多语言支持）
**英文**: `"Insufficient balance. Manage your billing here: {{billingUrl}}"`
**中文**: `"余额不足。请在此处管理您的计费：{{billingUrl}}"`
**繁体中文**: `"餘額不足。請在此處管理你的帳務：{{billingUrl}}"`

---

### 2. OpenAI 兼容 API 的配额错误

#### 错误代码
根据 `packages/opencode/src/provider/error.ts:133-139`:

```typescript
case "insufficient_quota":
  return {
    type: "api_error",
    message: "Quota exceeded. Check your plan and billing details.",
    isRetryable: false,  // ⚠️ 不可重试
    responseBody,
  }
```

#### 典型响应格式
```json
{
  "error": {
    "message": "You exceeded your current quota, please check your plan and billing details.",
    "type": "insufficient_quota",
    "param": null,
    "code": "insufficient_quota"
  }
}
```

---

### 3. HTTP 402 Payment Required

某些 API 提供商会返回标准的 HTTP 402 状态码：

```
HTTP/1.1 402 Payment Required
Content-Type: application/json

{
  "error": {
    "code": "payment_required",
    "message": "Insufficient funds to complete the request"
  }
}
```

---

## 🎯 Task Hounds 中的当前处理机制

### 现有错误处理流程

查看 `src/power_teams/mvp/runner.py` 中的 `send_to_agent` 函数：

#### 1. CLI 级别的错误捕获 (Line 270-273)

```python
elif etype == "error":
    err_msg = str(ev.get("error", "unknown error"))
    log(f"{agent_name}: session error: {err_msg}")
    append_text(stream_file, f"[error] {err_msg}\n")
```

**问题**: 只记录错误，没有特殊处理余额不足的情况

#### 2. 进程退出错误 (Line 282-286)

```python
if proc.returncode != 0:
    stderr_out = proc.stderr.read()
    raise RuntimeError(
        f"opencode run exited {proc.returncode}: {stderr_out[:300]}"
    )
```

**问题**: 将所有非零退出码视为一般错误

#### 3. 重试机制 (Line 158-331)

```python
for attempt in range(max_retries + 1):
    try:
        # ... execute command ...
    except Exception as exc:
        last_error = exc
        if attempt < max_retries:
            log(f"{agent_name} attempt {attempt + 1} failed: {exc}  retrying")
            time.sleep(3)
        else:
            log(f"{agent_name} all {max_retries + 1} attempts failed")
            raise RuntimeError(...)
```

**问题**: 对余额不足错误进行重试是无效的（`isRetryable: false`）

---

## ⚠️ 余额不足时的实际表现

### 场景 1: OpenCode Zen API

当余额 ≤ 0 时：

1. **请求被拒绝前验证**
   - 在发送请求到 LLM 之前检查余额
   - 立即抛出 `CreditsError`
   - 不会消耗任何 token

2. **错误传播路径**
   ```
   handler.ts (throw CreditsError)
     → SSE error event
       → opencode CLI receives error
         → runner.py captures error event
           → Currently: logs and continues retrying ❌
   ```

3. **用户看到的症状**
   - Manager/Worker 反复尝试连接
   - 日志显示多次失败
   - 最终所有重试耗尽后崩溃

---

### 场景 2: OpenAI 兼容 API

当配额用尽时：

1. **API 响应**
   ```json
   {
     "error": {
       "code": "insufficient_quota",
       "message": "You exceeded your current quota..."
     }
   }
   ```

2. **OpenCode 解析** (`provider/error.ts:133`)
   - 识别为 `api_error`
   - 标记为 `isRetryable: false`
   - 但仍然可能传递给 CLI

3. **Task Hounds 当前行为**
   - ❌ 不识别这是不可重试的错误
   - ❌ 继续尝试 max_retries 次
   - ❌ 浪费时间和资源

---

## 💡 改进建议

### 1. 添加余额不足错误检测

在 `runner.py` 中添加专门的错误检测：

```python
def is_insufficient_balance_error(error_msg: str) -> bool:
    """Detect insufficient balance/quota errors."""
    indicators = [
        "insufficient_balance",
        "insufficient quota",
        "quota exceeded",
        "payment required",
        "402",
        "余额不足",
        "餘額不足",
        "credits error",
        "billing",
    ]
    error_lower = error_msg.lower()
    return any(indicator in error_lower for indicator in indicators)
```

### 2. 修改重试逻辑

```python
for attempt in range(max_retries + 1):
    try:
        # ... execute command ...
    except Exception as exc:
        error_msg = str(exc).lower()

        # ⚠️ NEW: Check for non-retryable errors
        if is_insufficient_balance_error(error_msg):
            log(f"{agent_name}: INSUFFICIENT BALANCE detected. Stopping retries.")
            log(f"{agent_name}: Please add credits at: https://opencode.ai/billing")
            update_agent(agent_name, state="error", last_seen=utc_now())

            # Notify user via manager message
            add_manager_message(
                f"⚠️ API Error: Insufficient balance. "
                f"Please add credits to continue. "
                f"Error: {exc}"
            )

            # Stop the loop entirely
            return  # or raise a specific exception

        if attempt < max_retries:
            log(f"{agent_name} attempt {attempt + 1} failed: {exc}  retrying")
            time.sleep(3)
        else:
            log(f"{agent_name} all {max_retries + 1} attempts failed")
            raise
```

### 3. 添加预检机制

在每次调用 API 前检查余额（如果 API 支持）：

```python
def check_api_balance(provider: str) -> dict:
    """Check API balance before making requests."""
    if provider == "opencode-zen":
        # Call Zen API balance endpoint
        try:
            response = requests.get(
                "https://api.opencode.ai/v1/balance",
                headers={"Authorization": f"Bearer {api_key}"}
            )
            return response.json()
        except Exception:
            return {"balance": None, "error": "Could not check balance"}
    return {"balance": None, "error": "Provider not supported"}
```

### 4. 优雅降级策略

```python
def handle_api_failure(agent_name: str, error: Exception):
    """Handle API failures with graceful degradation."""
    error_msg = str(error)

    if is_insufficient_balance_error(error_msg):
        # Option 1: Switch to fallback model/provider
        log(f"{agent_name}: Switching to fallback provider...")
        return use_fallback_provider(agent_name)

        # Option 2: Queue task for later
        log(f"{agent_name}: Queuing task until balance is restored...")
        queue_task_for_later(agent_name, error)

        # Option 3: Notify and pause
        notify_user_insufficient_balance(error_msg)
        pause_all_agents()

    elif is_rate_limit_error(error_msg):
        # Wait and retry
        wait_time = extract_retry_after(error_msg)
        log(f"{agent_name}: Rate limited. Waiting {wait_time}s...")
        time.sleep(wait_time)
        return retry_with_backoff(agent_name)

    else:
        # General error handling
        raise error
```

---

## 🛠️ 实施步骤

### Phase 1: 基础错误检测（推荐优先实施）

1. ✅ 添加 `is_insufficient_balance_error()` 函数
2. ✅ 修改重试逻辑，检测到余额不足时立即停止
3. ✅ 添加友好的错误提示，告知用户如何充值
4. ✅ 更新 agent 状态为 "error" 而不是继续重试

**预计时间**: 30 分钟
**影响范围**: `runner.py` 的 `send_to_agent()` 函数

---

### Phase 2: 余额预检（可选增强）

1. 实现 `check_api_balance()` 函数
2. 在 `manager_cycle()` 和 `worker_cycle()` 开始时检查余额
3. 如果余额不足，提前通知用户，避免开始任务

**预计时间**: 1-2 小时
**依赖**: API 提供商是否提供余额查询端点

---

### Phase 3: 优雅降级（高级功能）

1. 配置多个 API 提供商作为 fallback
2. 实现自动切换逻辑
3. 添加任务队列机制

**预计时间**: 4-6 小时
**复杂度**: 中等

---

## 📊 错误对比表

| 错误类型 | HTTP 状态码 | 错误代码 | 可重试 | 处理方式 |
|---------|------------|---------|--------|---------|
| 余额不足 | 402 / 400 | `insufficient_quota` | ❌ 否 | 停止重试，通知用户充值 |
| 余额不足 (Zen) | N/A | `CreditsError` | ❌ 否 | 停止重试，提供充值链接 |
| 速率限制 | 429 | `rate_limit_exceeded` | ✅ 是 | 等待后重试 |
| 服务器错误 | 500/502/503 | `server_error` | ✅ 是 | 指数退避重试 |
| 上下文溢出 | 400 | `context_length_exceeded` | ❌ 否 | 减少输入，切换模型 |
| 认证失败 | 401 | `invalid_api_key` | ❌ 否 | 检查 API key |

---

## 🎬 实际示例

### 示例 1: 当前行为（有问题）

```
[2026-05-08T19:46:02] manager attempt 1 failed: POST /session/... failed
[2026-05-08T19:46:05] manager attempt 2 failed: POST /session/... failed
[2026-05-08T19:46:08] manager attempt 3 failed: POST /session/... failed
[2026-05-08T19:46:08] manager all 3 attempts failed
[2026-05-08T19:46:08] ERROR in manager_cycle: manager failed after 3 attempts
```

**问题**: 
- 浪费了 3 次重试机会
- 每次重试都等待 3 秒
- 总共浪费 ~9 秒
- 最终仍然失败

---

### 示例 2: 改进后的行为

```
[2026-05-08T19:46:02] manager: session error: Insufficient balance. Manage your billing here: https://opencode.ai/workspace/.../billing
[2026-05-08T19:46:02] ⚠️ INSUFFICIENT BALANCE detected. Stopping retries immediately.
[2026-05-08T19:46:02] 💡 Please add credits at: https://opencode.ai/billing
[2026-05-08T19:46:02] Manager message saved: "⚠️ API Error: Insufficient balance..."
[2026-05-08T19:46:02] Worker state set to: error
```

**优势**:
- ✅ 立即检测到问题
- ✅ 不进行无效重试
- ✅ 清晰告知用户如何解决
- ✅ 节省时间和资源

---

## 🔗 相关资源

### OpenCode 源码参考
- 错误定义: `packages/console/app/src/routes/zen/util/error.ts`
- 余额检查: `packages/console/app/src/routes/zen/util/handler.ts:802-808`
- 错误解析: `packages/opencode/src/provider/error.ts:133-139`
- 国际化消息: `packages/console/app/src/i18n/en.ts:361`

### API 文档
- OpenAI 错误码: https://platform.openai.com/docs/guides/error-codes
- Anthropic 错误处理: https://docs.anthropic.com/en/api/errors
- OpenCode Zen API: https://opencode.ai/docs/api

---

## ✅ 总结

### 关键发现

1. **OpenCode 有完善的余额检查机制**
   - 在请求发送前验证
   - 抛出明确的 `CreditsError`
   - 提供多语言错误消息

2. **Task Hounds 当前未充分利用这些信息**
   - 不识别余额不足错误
   - 对不可重试的错误进行重试
   - 缺乏友好的用户提示

3. **改进空间很大**
   - 添加简单的错误检测即可显著改善体验
   - 可以进一步实现余额预检和优雅降级

### 推荐行动

**立即实施** (Phase 1):
- 添加余额不足错误检测
- 修改重试逻辑
- 提供清晰的充值指引

**后续优化** (Phase 2 & 3):
- 实现余额预检
- 配置 fallback 提供商
- 添加任务队列机制

---

*最后更新: 2026-05-08*
*基于 OpenCode 源码分析和 Task Hounds 架构研究*
