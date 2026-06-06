# Subagent Exit-0 不觸發 Fallback 問題調查

**日期**: 2026-05-29  
**Commit**: ea76dff → 60efcd0  
**Port**: 8899 (standalone `opencode serve`)

## 問題現象

使用 Chat Agent 時，選擇 `general`、`build`、`explore` 等 agent 會返回 **500 錯誤**。  
觀察到的現象：

```
EXIT: 0
STDOUT: 0 bytes
STDERR: "agent 'general' is a subagent, not a primary agent"
```

## 根因分析

### 1. 環境差異

| 環境 | Server Port | 自訂 Agent 註冊模式 | 結果 |
|------|-------------|---------------------|------|
| EXE-managed (production) | 18765 | **primary** | 正常輸出 |
| Standalone (dev/test) | 8899 | **subagent** | 0 stdout + exit 0 |

**原因**: EXE 啟動 server 時 `cwd=workspace`，workspace 有 `.opencode/agents/` 目錄，所以自訂 agent 註冊為 primary。Standalone server 啟動於 `core/runtime/opencode_config`，沒有 `.opencode/agents/`，所以自訂 agent 註冊為內建 subagent。

### 2. Port 8899 上的 Agent 模式

```
TOTAL: 17  PRIMARY: 6  SUBAGENT: 10

--- PRIMARY ---
  compaction
  summary
  title

--- SUBAGENT ---
  Metis - Plan Consultant
  Momus - Plan Critic
  Sisyphus-Junior
  build
  explore
  general
  librarian
  multimodal-looker
  oracle
  plan
```

### 3. Subagent 行為

`opencode run --agent <subagent>` 會：
- 立即退出 (exit 0)
- stdout: 0 bytes
- stderr: `agent X is a subagent, not a primary agent. Falling back to default agent`
- **不產生任何輸出**

## 兩個 Bug

### Bug 1 — `_resolve_opencode_agent` fallback 條件錯誤

**位置**: `base.py:803`

```python
# 原本：只要 agent 名字在 map 裡就接受，不管是 primary 還是 subagent
if not agents_map or mode == "primary" or fallback in agents_map:

# 修復：只接受 primary
if not agents_map or mode == "primary":
```

**問題**: `fallback in agents_map` 只檢查名字是否存在，不檢查 mode。所以 subagent 會被當作有效選項返回，然後 exit 0。

### Bug 2 — Exit 0 時不讀 stderr

**位置**: `base.py:1108-1176`

原本只在 `returncode != 0` 時才檢查 stderr 並觸發 agent fallback chain。  
Subagent 會 exit 0（不是錯誤），所以：
- 不進 fallback chain
- stdout 為空 → 重試 → 還是空 → **無限迴圈**

**修復**: 在 exit 0 + empty stdout 時，讀取 stderr，偵測 "subagent" 關鍵字後觸發 fallback chain。

```python
if not result and proc.returncode == 0:
    try:
        _stderr_text = proc.stderr.read()
    except Exception:
        _stderr_text = ""
    if any(kw in _stderr_text.lower() for kw in ("subagent", "not a primary agent")):
        # 觸發 agent/model fallback chain
        ...
```

## 測試執行

### 測試環境

1. 啟動 standalone server:
   ```powershell
   python opencode-test/start_serve.py
   ```

2. 執行測試:
   ```powershell
   python opencode-test/test_subagent_fallback.py
   ```

### 預期結果

| 測試 | 預期 |
|------|------|
| Agent list (primary vs subagent) | 顯示 primary/subagent 分類 |
| `--agent build` (subagent) | 0 stdout → fallback → primary agent → 正常輸出 |
| `--agent compaction` (primary) | 正常輸出 |
| HTTP fallback path | exit 0 + 無 stdout + 無 stderr → HTTP fallback → 正常輸出 |
| All subagents | 觸發 stderr 偵測 → fallback chain |

## 修復效果

### 修復前

```
Chat → agent=build → subagent exit 0 → 不觸發 fallback → 無限重試 → 500 error
```

### 修復後

```
Chat → agent=build → subagent exit 0 → stderr 偵測 → fallback chain → compaction → 正常輸出
```

## 測試腳本

完整的測試腳本見：`docs/scripts/test_subagent_fallback.py`

執行方式：

```powershell
# 確保 standalone server 在 port 8899 運行
python opencode-test/start_serve.py

# 執行測試
python docs/scripts/test_subagent_fallback.py
```

## 相關文件

- `base.py:788-810` — `_resolve_opencode_agent` fallback loop
- `base.py:1196-1231` — exit-0 subagent 偵測 + fallback chain
- `base.py:1195` — HTTP fallback path (`_fetch_attached_session_text`)
