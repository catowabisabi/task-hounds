from __future__ import annotations

import json
import sys
import time
from urllib.request import Request, urlopen

from constants import DEFAULT_FLOW01_BASE_URL
from put_bigsmall_directive import DIRECTIVE, WORKSPACE


BASE_URL = DEFAULT_FLOW01_BASE_URL
TASK = (
    "根據買大細 BigSmall 規格，建立第一個可運行的本地優先 Web App 骨架："
    "FastAPI + SQLite + 靜態 HTML/JS，包含認證、房間、下注、開獎的最小可測版本。"
)


def request(method: str, path: str, payload: dict | None = None, timeout: int = 120) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = Request(
        BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    put_result = request(
        "PUT",
        "/api/workflows/flow_01/directive",
        {"workspace_path": WORKSPACE, "directive": DIRECTIVE},
        timeout=60,
    )
    print("PUT_DIRECTIVE")
    print(json.dumps({
        "ok": put_result.get("ok"),
        "db_directive": put_result.get("db_directive"),
    }, ensure_ascii=False, indent=2))

    start_result = request(
        "POST",
        "/api/workflows/flow_01/start-loop",
        {
            "workspace_path": WORKSPACE,
            "directive": DIRECTIVE,
            "suggested_task": TASK,
            "thought": "Use the new background flow_01 start-loop. Create the todo list first, then let Worker implement one observable slice.",
            "manager_message": "Manager should release one concrete implementation task and require files changed plus verification evidence.",
            "emit_real_ui_signals": True,
            "use_real_executors": True,
        },
        timeout=60,
    )
    print("START_LOOP")
    print(json.dumps(start_result, ensure_ascii=False, indent=2))

    run_id = int(start_result["run_id"])
    time.sleep(2)
    detail = request("GET", f"/api/workflows/flow_01/runs/{run_id}", timeout=60)
    print("RUN_DETAIL")
    print(json.dumps(detail["run"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
