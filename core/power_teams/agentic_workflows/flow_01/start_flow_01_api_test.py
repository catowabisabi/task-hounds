from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_TEST_DIR = Path.home() / "Desktop" / "test" / "05"
DEFAULT_DIRECTIVE = (
    "Create a minimal observable flow_01 test artifact in this workspace. "
    "Start from the manager-selected task, keep the change small, and report "
    "the exact files changed and verification result."
)


def ensure_directive_file(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        path.write_text(DEFAULT_DIRECTIVE + "\n", encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=1800) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Start a flow_01 API test run.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8866")
    parser.add_argument("--workspace", default=str(DEFAULT_TEST_DIR))
    parser.add_argument("--directive-file", default=str(DEFAULT_TEST_DIR / "human_directive.txt"))
    parser.add_argument("--loops", type=int, default=1)
    parser.add_argument("--local-worker", action="store_true", help="Use deterministic local worker instead of OpenCode.")
    parser.add_argument("--no-ui-signals", action="store_true", help="Disable writes to existing UI streams.")
    parser.add_argument("--allow-known-issues", action="store_true", help="Return success even if the flow reports known issues.")
    args = parser.parse_args()

    workspace = Path(args.workspace)
    directive_file = Path(args.directive_file)
    directive = ensure_directive_file(directive_file)

    payload = {
        "loops": args.loops,
        "directive": directive,
        "suggested_task": "Start process from human_directive.txt and create one observable test artifact.",
        "thought": "This is the first flow_01 API smoke test using Desktop/test/05 as the workspace.",
        "manager_message": "Manager should release exactly one small task, then Worker should report concrete evidence.",
        "workspace_path": str(workspace),
        "emit_real_ui_signals": not args.no_ui_signals,
        "use_real_worker": not args.local_worker,
    }
    url = args.base_url.rstrip("/") + "/api/workflows/flow_01/run"
    print(f"POST {url}")
    print(json.dumps({"payload": payload}, ensure_ascii=False, indent=2))

    try:
        result = post_json(url, payload)
    except HTTPError as exc:
        print(f"HTTP {exc.code}", file=sys.stderr)
        print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    known_issues = (result.get("last_output") or {}).get("known_issues") or []
    if known_issues and not args.allow_known_issues:
        print("Flow reported known issues; failing the smoke test.", file=sys.stderr)
        return 2
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
