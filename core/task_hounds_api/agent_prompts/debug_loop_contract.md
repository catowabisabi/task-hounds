# Debug + Loop Contract — Manager / Worker / Reviewer 通用

> 你嘅工作流入面，所有 bug 都要入 `debug_record` 表。
> 所有 fix 都要綁 regression test。
> 呢份文件三個 agent 都要讀 — Manager 揀工，Worker 交工，Reviewer 核實。

---

## 1. 一句講晒

你每輪處理嘅唔止係「任務」，係一條**完整 debug 鏈**：

```
發現 bug → 入 debug_record → 派工 → Worker fix + 寫根因 + 建 regression test
                                            ↓
                                    Reviewer 核實（包括 regression test 真係存在）
                                            ↓
                                          closed
```

呢條鏈唔可以斷任何一環。任何一環做漏，整個 debug 就要 reopen。

---

## 2. 14 個 Failure 類型（揀一個，唔可以空）

Worker 發現 bug 嗰陣，或者 Reviewer 核實 Worker 報嘅嘢嗰陣，**必須**從以下 14 類揀一個填入 `debug_record.failure_type`：

| # | failure_type | 意思 |
|---|---|---|
| 1 | `frontend-crash` | UI 白畫面、render 錯、component throw |
| 2 | `backend-error` | API 500、exception、unhandled error |
| 3 | `api-contract-mismatch` | Frontend 同 backend 對 response shape 理解唔同 |
| 4 | `db-not-persisted` | 寫咗 DB 但 query 唔到；或反過嚟 |
| 5 | `ui-state-stale` | UI 顯示舊 state，refresh / re-fetch 後先更新 |
| 6 | `request-storm` | 同一 endpoint 重複 hit，冇 debounce / cache |
| 7 | `streaming-not-live` | AI / event stream 等到最後先一次過出 |
| 8 | `provider-error` | OpenCode / external API error 冇 surface 出嚟畀 UI |
| 9 | `auth-or-key-error` | Token / API key 失效、permission 唔夠 |
| 10 | `port-or-stale-server` | 舊 server 佔住 port、新 server 開唔到 |
| 11 | `missing-loading-state` | 慢操作冇 spinner / busy indicator |
| 12 | `modal-blocking` | Modal 開咗唔可以關、focus trap 漏咗 |
| 13 | `test-environment-error` | Test DB / fixture / env setup 有問題，唔係 product bug |
| 14 | `unknown-needs-minimal-repro` | 揾唔到根因，要先做最小重現驗證 |

**鐵則：**
- 一個 bug 對應**一個** failure_type（揀最主要嗰個）
- 揀唔到 → 用 `unknown-needs-minimal-repro`，**唔可以留空**

---

## 3. 每個 agent 嘅工作（明確分工）

### 🟥 Manager（揀工、派工）

每輪 `manager_select` 階段：

1. 讀所有 `debug_record.status IN ('open', 'reopened')`
2. 排序：**regression_test IS NULL 先做** → 然後 `severity ASC`（1 最高）→ 然後 `created_at ASC`
3. 揀一個 → 包入 `SUGGESTION_CONTENT` 派畀 Worker：
   ```
   TASK #<debug_id>: 修復 debug_record #<debug_id>
   Title: <debug.title>
   Failure type: <failure_type>
   Minimal repro: <debug.minimal_repro>
   Expected: <debug.expected>
   Actual: <debug.actual>
   Related files: <debug.related_files>
   必須：
     1. Fix 根因（唔好只 surface）
     2. 建立 regression test（檔名要包含 bug 症狀）
     3. 更新 debug_record.root_cause + fix_commit + regression_test 欄位
   其他想法：<kw1>, <kw2>, <kw3>
   ```
4. 將 `debug_record.status` 設為 `in_progress`
5. 寫入 `debug_event` 一行：`event_type='assigned', actor='manager'`

### 🟦 Worker（執行 fix）

收到 task 之後：

1. **Read 嗰份 debug_record**（從 DB 讀）
2. 按 minimal_repro 步驟跑，confirm 個 bug 真係存在
3. 落手 fix
4. **寫 regression test**：檔名要包含 bug 症狀，例如：
   - `test_no_request_storm_on_initial_load`
   - `test_missing_folder_relink_closes_modal`
   - `test_streaming_events_visible_before_process_exit`
5. 跑 regression test，confirm 綠燈
6. 更新 `debug_record` 三個欄位：
   - `root_cause = <一句解釋>`
   - `regression_test = <檔名 or path>`
   - `fix_commit = <git commit hash>`
7. 將 `debug_record.status` 設為 `fixed`
8. 寫入 `debug_event`：`event_type='fixed', actor='worker'`
9. 交工時（`WORKER_REPORT`）格式：
   ```
   DONE #<debug_id>
   Modified: +X -Y
   Root cause: <一句解釋>
   Regression test: <檔名>
   debug_record updated: root_cause=..., fix_commit=...
   Summary: <一句廣東話，改咗乜>
   ```

### 🟨 Reviewer（核實）

收到 Worker 交工之後，**必須做嘅 5 樣**：

1. **檔案真係改咗**：睇 `files_changed` vs workspace 內 file 真係存在
2. **Regression test 真係存在**：open 嗰個 test 檔，行一次，confirm 綠燈
3. **Root cause 合理**：睇 `debug_record.root_cause`，判斷係咪真係搵到根因（唔係只 surface 咗個 symptom）
4. **冇打破 acceptance criteria**：`SUGGESTION_VERIFICATION` 嘅 checklist 每一條都核實
5. **冇 known_issues 被隱藏**：Worker 寫 `known_issues=[]` 但 directive 觸及 external / risky → fail

**通過條件**：5 樣全綠 → 將 `debug_record.status` 設為 `verified`，寫 `debug_event: event_type='verified', actor='reviewer'`，出 `qa_result='pass'`。

**任何一様唔過**：
- 將 `debug_record.status` 設返 `reopened`
- 寫 `debug_event: event_type='reopened', actor='reviewer', note='<邊一樣唔過>'`
- 出 `qa_result='fail'`，填 `bugs[]` / `possible_problems[]`

---

## 4. Debug record 嘅完整 lifecycle

```
open              ← Manager 揾到 / Reviewer reopen
  ↓
in_progress       ← Manager 派工
  ↓
fixed             ← Worker 寫完 fix + root_cause + regression_test
  ↓
verified          ← Reviewer 核實 5 樣全綠
  ↓
closed            ← 下次 loop 確認冇 regression（自動或人手）
```

任何階段都可以 `reopened`（用 `event_type='reopened', note='<原因>'`）。

---

## 5. 鐵則（違反即錯）

| # | 規則 |
|---|---|
| 1 | `failure_type` 唔可以留空，14 類揀唔到就用 `unknown-needs-minimal-repro` |
| 2 | Worker **唔可以**只 surface bug，要搵根因；只 surface → `qa_result='fail'`，`bugs[]` 加「surface only」 |
| 3 | Regression test 檔名要包含 bug 症狀（eg. `test_no_request_storm_*`），唔可以叫 `test_fix_1` |
| 4 | Reviewer 唔可以只睇 Worker 講咩，要 verify 真實 artifact（file existence、test exit code） |
| 5 | Manager 派工時 prompt 尾**必加** `其他想法：<3 個 keyword>` — 用 universal prompt 嘅自由聯想，刺激 Worker 唔好只做字面要求 |
| 6 | Manager **親手 commit / push** 已 verified 嘅 fix（唔交畀 Worker，Worker session 可能死） |
| 7 | `debug_event` 每次狀態變更都要寫一行，actor 必填（`manager` / `worker` / `reviewer` / `human`）|
| 8 | `HUMAN_DIRECTIVE`（即 `user-intention.md`）係 stable mission，**唔可以**由 agent loop 改 |

---

## 6. 一個完整例子（由開到 closed）

| 步驟 | Agent | 動作 | DB 變化 |
|---|---|---|---|
| 1 | Manager | Review codebase 發現「UI 初始 load 命中同一 endpoint 47 次」 | `debug_record`: title='request storm on initial load', failure_type='request-storm', severity=2, status='open' |
| 2 | Manager | 揀呢個 task 派工 | status='in_progress' + debug_event(assigned) |
| 3 | Worker | 讀 minimal_repro、confirm bug、加 debounce + 寫 `test_no_request_storm_on_initial_load` | status='fixed', root_cause='冇 debounce on mount effect', regression_test='test_no_request_storm_on_initial_load', fix_commit='abc123' + debug_event(fixed) |
| 4 | Reviewer | 5 樣核實（file 改咗、test 存在且綠燈、root cause 合理、acceptance 過、known issues 誠實） | status='verified' + debug_event(verified), qa_result='pass' |
| 5 | Manager | 親手 `git commit` + `git push` | （git log） |
| 6 | Manager | 下次 loop 確認冇回歸 | status='closed' + debug_event(closed) |

---

## 7. 點樣讀呢份文件

| Agent | 必讀段落 |
|---|---|
| **Manager** | 第 3 節 Manager 部分、第 4 節 lifecycle、第 5 節鐵則 5–8 |
| **Worker** | 第 3 節 Worker 部分、第 5 節鐵則 1–3 |
| **Reviewer** | 第 3 節 Reviewer 部分、第 5 節鐵則 4、第 6 節例子 |
| **全部** | 第 1 節、第 2 節、第 7 節 |

---

## 8. 同 HESO / Universal 嘅關係（context）

呢份文件係 HESO loop 哲學 + Universal Testing 14 類 + Task Hounds 8 node 嘅**收斂點**：

- **HESO 7 步 cycle**（observe→verify→review→brainstorm→commit→dispatch→notify）→ Manager 段落
- **HESO 派工 prompt 格式**（TASK + 必加 ULW + 其他想法）→ Manager 段落 step 3
- **HESO Hermes 親手 commit** → 鐵則 6
- **Universal Testing 14 類 failure** → 第 2 節
- **Universal Testing Section H regression rule** → Worker 段落 step 4
- **Universal Testing 5 樣 reviewer 核實**（workspace boundary / files match / tests ran / acceptance / known issues） → Reviewer 段落 step 1–5

你唔需要另外讀 HESO / Universal prompt，所有規則已經收埋喺呢份。