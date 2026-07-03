<p align="center">
  <a href="README.md">English</a>
  &nbsp;·&nbsp;
  <a href="README.zh-TW.md"><strong>繁體中文</strong></a>
</p>

<p align="center">
  <img src="docs/image/Task%20Hounds%20Logo.png" alt="Task Hounds 標誌" width="160">
</p>

<h1 align="center">Task Hounds</h1>

<p align="center">
  <strong>Work like a dog. Ship like a pack.</strong><br>
  由 OpenCode 驅動、本機優先且過程透明的多代理開發工作空間。
</p>

<p align="center">
  <a href="https://task-hounds.com">官方網站</a>
  · <a href="https://github.com/catowabisabi/task-hounds">GitHub</a>
  · <a href="https://www.youtube.com/watch?v=pu-Rt8Ye4EQ&t=174s">示範影片</a>
  · <a href="https://github.com/catowabisabi/task-hounds/issues">問題回報</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-2563eb.svg" alt="MIT 授權"></a>
  <img src="https://img.shields.io/badge/Python-3.11+-f5c542.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/React-19-61dafb.svg" alt="React 19">
  <img src="https://img.shields.io/badge/Desktop-Electron-47848f.svg" alt="Electron">
  <img src="https://img.shields.io/badge/OpenCode-Powered-111827.svg" alt="由 OpenCode 驅動">
</p>

<p align="center">
  <img src="docs/image/banner2.png" alt="Task Hounds 多代理開發工作空間" width="92%">
</p>

## Task Hounds 是什麼？

Task Hounds 把你的一個目標，轉化為清楚可見的軟體開發循環。你只需要給團隊一個 **Human Directive（人類指令）**：Manager 負責規劃、Worker 動手實作、Reviewer 檢查成果，再開始下一項工作。

它不是一個看不見內部狀態的黑盒助理。指令、計畫、待辦事項、工作報告、代理狀態與可重用的 OpenCode 對話都會儲存在本機；Dashboard 則會即時呈現每個代理正在做什麼。

它適合想要運用代理自主開發，同時仍然掌握方向、脈絡與品質的開發者。

## 認識你的狗狗團隊

| 角色 | 職責 |
| --- | --- |
| **你（Human）** | 設定長期專案目標，隨時加入想法或調整方向。 |
| **Manager** | 理解完整脈絡、維護計畫，每次指派一項明確工作。 |
| **Worker** | 實作指定工作，回報修改檔案、測試結果與已知問題。 |
| **Reviewer** | 檢查錯誤、使用體驗、邊界情況與安全風險。 |
| **Chat** | 讓你直接討論專案並與整個系統互動。 |

```mermaid
flowchart LR
    H["Human Directive<br>人類指令"] --> M["Manager<br>規劃與分工"]
    M --> W["Worker<br>動手實作"]
    W --> R["Reviewer<br>審查驗證"]
    R --> M
    M --> D["即時 Dashboard<br>待辦、報告與狀態"]
    W --> D
    R --> D
```

## 為什麼選擇 Task Hounds？

- **本機優先**：工作空間、資料庫、執行狀態與紀錄都保留在你的電腦。
- **過程透明**：即時查看思考過程、工具活動、待辦、報告與審查意見。
- **脈絡可延續**：以 SQLite 保存專案狀態，並重用各角色的 OpenCode 對話。
- **角色分工清楚**：規劃、實作與審查交給不同代理，減少自說自話。
- **人類隨時掌舵**：透過長期指令、新想法與建議任務調整方向。
- **多種執行方式**：支援網頁 Dashboard、Windows 桌面程式、Docker 與實驗性 Android 用戶端。
- **自由開源**：採用 MIT License，可自由修改與延伸。

## 實際畫面

<p align="center">
  <a href="https://www.youtube.com/watch?v=pu-Rt8Ye4EQ&t=174s">
    <img src="https://img.youtube.com/vi/pu-Rt8Ye4EQ/maxresdefault.jpg" alt="觀看 Task Hounds 示範影片" width="82%">
  </a>
</p>

<p align="center">
  <img src="docs/image/ui%20(2).png" alt="Task Hounds Dashboard" width="88%">
</p>

## 快速開始

### 系統需求

- Windows（最適合使用受管理的執行環境與桌面版）
- Python 3.11+
- Node.js 20+
- npm

### 1. 下載並安裝

```powershell
git clone https://github.com/catowabisabi/task-hounds.git
cd task-hounds

.\installation.cmd
pip install -r requirements.txt
pip install .
```

`installation.cmd` 會安裝 Task Hounds 指定版本、由專案管理的 OpenCode 執行環境。

### 2. 建置 Dashboard

```powershell
cd ui/web
npm ci
npm run build
cd ../..
```

### 3. 設定環境

```powershell
Copy-Item .env.example .env
```

為了向下相容，環境變數仍使用 `POWER_TEAMS_` 前綴。加入模型供應商金鑰或將 API 開放至 localhost 以外的位置前，請先閱讀 `.env.example`。

### 4. 啟動

```powershell
$env:PYTHONPATH = "core"
python core\api\server.py --port 8765
```

開啟 [http://localhost:8765](http://localhost:8765)，建立或選擇工作空間、輸入 Human Directive，然後按下 **Start Loop** 或 **Run Once**。

> 沒有待處理的 Human Directive 時，Task Hounds 不會自行啟動代理開發循環。

完整說明請參考[快速入門指南](docs/guides/getting-started.md)。

## 其他執行方式

### Docker

```bash
docker build -t task-hounds .
docker run --rm -p 8765:8765 -v "$(pwd)/data:/app/data" task-hounds
```

### Windows 桌面版

```powershell
.\build_exe.ps1
```

Electron 可攜版程式會輸出至 `ui/desktop/dist/`。

### Android 用戶端

實驗性的 React + Capacitor 用戶端位於 `ui/mobile/`。它會連接相同的後端，共用專案、對話、待辦、Chat 與代理狀態。建議透過 [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) 私密連線，請勿將 Task Hounds 後端直接暴露在公開網路。

設定方式請參考 [ui/mobile/README.md](ui/mobile/README.md)。

## 系統架構

SQLite 是執行階段的資料來源，保存專案對話、指令、待辦、報告、建議與代理狀態。`core/runtime/` 內的相容性檔案只作為本機執行映像與備援。

```text
task-hounds/
├── core/
│   ├── api/                 # HTTP API 與 Dashboard 伺服器
│   ├── db/                  # SQLite schema 與 migrations
│   ├── power_teams/         # 舊版 Python package 名稱
│   └── task_hounds_api/     # 現行後端與代理流程
├── ui/
│   ├── web/                 # React + Vite Dashboard
│   ├── desktop/             # Electron 桌面程式
│   └── mobile/              # React + Capacitor Android 用戶端
├── docs/                    # 指南、架構、測試與圖片
├── Dockerfile
└── .env.example
```

執行資料、SQLite 資料庫、紀錄、本機 `.env`、個人 OpenCode 設定與建置產物都不會提交至公開 repository。

## 參與開發

後端測試：

```powershell
pytest
```

Web Dashboard：

```powershell
cd ui/web
npm run build
```

歡迎提交功能想法、錯誤回報與 Pull Request。開始前請閱讀 [CONTRIBUTING.md](CONTRIBUTING.md)；若要回報安全問題，請參考 [SECURITY.md](SECURITY.md)。

## 支持這個專案

如果 Task Hounds 幫你節省了時間，或你也喜歡「一小群 AI 狗狗一起寫軟體」這個主意，歡迎請我喝杯咖啡。你的支持會用於持續開發、測試，以及餵飽虛擬狗狗背後那位需要真咖啡的人。

<p align="center">
  <a href="https://buymeacoffee.com/catowabisabi?new=1">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="請我喝杯咖啡" width="210">
  </a>
</p>

## 授權

Task Hounds 採用 [MIT License](LICENSE) 發布。
