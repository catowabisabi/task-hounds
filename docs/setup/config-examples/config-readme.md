# Task Hounds 配置說明

## 📁 config/ 目錄結構

```
config/
├── settings.example.json    # 桌面應用（Electron）初始設定範本
├── workspaces.example.json  # 桌面應用（Electron）工作區範本
├── .env.example            # 環境變量模板（重要，需手動填寫）
└── powerteams.yaml         # 主要設定（需自行創建）
```

---

## ⚠️ 重要：哪些是動態的，哪些是靜態的？

| 檔案 | 類型 | 誰改 |
|------|------|------|
| `settings.example.json` | **動態** - 應用程式自動更新 | 應用程式 |
| `workspaces.example.json` | **動態** - 應用程式自動更新 | 應用程式 |
| `.env` | **靜態** - 用戶一次性填入 | 用戶 |
| `powerteams.yaml` | **靜態** - 用戶配置 | 用戶 |

**不要手動編輯動態檔案**（settings.example.json、workspaces.example.json），它們由應用程式在運行時自動更新。

---

## 1. settings.example.json（動態設定）

桌面應用（Electron）在運行時自動管理，用戶不需手動修改。

| 欄位 | 預設值 | 說明 |
|------|--------|------|
| `language` | `"en"` | 介面語言（`en` / `zh` / `ja`） |
| `force_planning` | `true` | 是否強制先做規劃再執行 |
| `force_todo` | `true` | 是否強制使用 todo 追蹤進度 |
| `force_thinking_language` | `true` | 是否強制代理顯示思考過程 |
| `custom_languages` | `[]` | 自訂語言（進階） |
| `active_workspace_id` | `"default"` | 目前使用的工作區 ID |
| `active_project_session` | `null` | 目前專案 session |
| `agent_sessions` | 見下 | 各代理的 session 狀態 |

**此檔案由應用程式自動管理，請勿手動編輯。**

---

## 2. workspaces.example.json（動態工作區）

定義桌面應用可以訪問的資料夾目錄。

| 欄位 | 說明 | 範例 |
|------|------|------|
| `id` | 工作區唯一 ID | `"default"` |
| `path` | 工作區路徑 | `"C:\\Users\\<your-username>\\Desktop"` |
| `label` | 顯示名稱 | `"My Workspace"` |
| `active` | 是否啟用 | `true` / `false` |

**此檔案由應用程式自動管理，請勿手動編輯。**

---

## 3. .env.example / .env（靜態設定）

**⚠️ 這個最重要！** 包含敏感資訊和 API key。

| 變量 | 說明 |
|------|------|
| `OPENCODE_API_KEY` | OpenCode API key |
| `HERMES_API_KEY` | Hermes API key |
| `OPENCLAW_API_KEY` | OpenClaw API key |
| `POWER_TEAMS_OPENCODE_TOPOLOGY` | OpenCode runtime topology. Default is `shared`; use `per_role` to give manager/worker/reviewer/chat separate serve processes. |
| `POWER_TEAMS_OPENCODE_PORT` | Shared OpenCode port when using `shared` topology. |

**使用方式：**
```bash
# 1. 複製範本
cp config/.env.example config/.env

# 2. 編輯 config/.env，填入你的 API key
OPENCODE_API_KEY=sk-xxxxx
HERMES_API_KEY=xxx
POWER_TEAMS_OPENCODE_TOPOLOGY=per_role
```

---

## 4. powerteams.yaml（主要設定）

這是主要的設定檔，定義專案、代理、資料庫等。

| 區塊 | 欄位 | 說明 |
|------|------|------|
| `project.name` | 專案名稱 | `"my-project"` |
| `project.repo` | Git 倉庫 URL | `"https://github.com/user/repo"` |
| `agents.manager.model` | 管理器模型 | `"claude-3-opus"` |
| `agents.manager.max_iterations` | 最大迭代次數 | `10` |
| `agents.worker.model` | 工作器模型 | `"claude-3-sonnet"` |
| `agents.worker.parallel_tasks` | 並行任務數 | `3` |
| `agents.reviewer.model` | 審查器模型 | `"gpt-4"` |
| `agents.reviewer.quality_threshold` | 品質閾值 | `85` |
| `backend.type` | 後端類型 | `"opencode"` / `"hermes"` / `"openclaw"` |
| `backend.api_key` | API key（可用 `${OPENCODE_API_KEY}`） | `"${OPENCODE_API_KEY}"` |
| `database.path` | 資料庫路徑 | `"data/power_teams.db"` |
| `dashboard.enabled` | 是否啟用儀表板 | `true` |
| `dashboard.port` | 儀表板端口 | `5173` |

**使用方式：**
```bash
# 創建設定檔
cp config/powerteams.yaml.example config/powerteams.yaml

# 編輯 config/powerteams.yaml
```

---

## 🚀 快速開始

```bash
# 1. 複製環境變量範本並填入 API key
cp config/.env.example config/.env

# 2. 創建主要設定（可選，有預設值）
cp config/powerteams.yaml.example config/powerteams.yaml

# 3. 運行
python -m power_teams run --task "你的任務"
```
