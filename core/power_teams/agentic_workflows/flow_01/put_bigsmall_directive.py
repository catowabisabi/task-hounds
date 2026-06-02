from __future__ import annotations

import json
from urllib.request import Request, urlopen

from constants import DEFAULT_BIGSMALL_WORKSPACE, DEFAULT_FLOW01_BASE_URL

BASE_URL = DEFAULT_FLOW01_BASE_URL
WORKSPACE = str(DEFAULT_BIGSMALL_WORKSPACE)

DIRECTIVE = """# 買大細遊戲 App 規格文檔

## 1. 項目概述

**項目名稱**: 買大細 (BigSmall)
**項目類型**: 本地優先的網頁棋牌遊戲
**核心功能**: 20人同桌的買大細賭遊戲幣遊戲，玩家可登入、創建房間/加入房間、下注、結算
**目標用戶**: 小圈子朋友之間的休閒賭博遊戲（遊戲幣，無需兌換現金）

## 2. 技術架構

### 前端
- **技術棧**: HTML5 + Vanilla JavaScript (ES6+)
- **樣式**: 原生 CSS3（或 Tailwind CDN）
- **部署**: 本地靜態文件服務

### 後端
- **框架**: FastAPI (Python)
- **數據庫**: SQLite（本地文件）
- **認證**: JWT Token（本地生成與驗證）

## 3. 功能需求

### 3.1 用戶系統
| 功能 | 描述 |
|------|------|
| 註冊 | 用戶名、密碼（bcrypt hash） |
| 登入 | 用戶名 + 密碼 → JWT Token |
| 遊戲幣 | 每個用戶有遊戲幣餘額，起始 1000 |

### 3.2 房間系統
| 功能 | 描述 |
|------|------|
| 創建房間 | 輸入房間名稱，生成房間 ID |
| 加入房間 | 通過房間 ID 加入 |
| 房間人數 | 每房間最多 20 人 |

### 3.3 遊戲邏輯
| 功能 | 描述 |
|------|------|
| 下注 | 玩家選擇「大」或「細」，輸入金額 |
| 最小下注 | 10 遊戲幣 |
| 抽水 | 莊家每次勝出抽取 1% |
| 開獎 | 系統隨機生成 1-6 點，>=4 為大 |

### 3.4 顯示功能
- 房間內顯示所有玩家 username
- 結算後顯示誰贏、誰輸

## 4. API 端點

- `POST /api/auth/register` - 註冊
- `POST /api/auth/login` - 登入
- `GET /api/user/me` - 獲取用戶信息
- `POST /api/room/create` - 創建房間
- `GET /api/room/list` - 房間列表
- `POST /api/room/{room_id}/join` - 加入房間
- `POST /api/room/{room_id}/bet` - 下注
- `POST /api/room/{room_id}/start` - 開始遊戲

## 5. 數據模型

- **User**: id, username, password_hash, coins (起始 1000)
- **Room**: id, name, room_code (6位), max_players (20), status
- **Player**: room_id, user_id, is_host, current_bet, bet_choice
- **GameHistory**: dice_result, result (big/small), total_bets_big/small
- **BetRecord**: game_id, user_id, amount, choice, won

## 6. 前端頁面結構

```
/index.html          - 登入/註冊頁
/lobby.html          - 大廳（房間列表）
/room.html           - 房間內（遊戲畫面）
```

## 7. 驗收標準

- [ ] 用戶可以註冊、登入、獲得 JWT Token
- [ ] 登入後顯示遊戲幣餘額
- [ ] 可以創建房間、加入房間
- [ ] 房間內顯示所有玩家 username
- [ ] 20 人上限控制
- [ ] 玩家可以下注（大/細）
- [ ] 遊戲結算正確，莊家抽 1%
- [ ] 顯示誰贏、誰輸
- [ ] 所有數據本地存儲（SQLite）
"""


def post_json(path: str, payload: dict) -> dict:
    req = Request(
        BASE_URL + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    result = post_json(
        "/api/workflows/flow_01/directive",
        {"workspace_path": WORKSPACE, "directive": DIRECTIVE},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
