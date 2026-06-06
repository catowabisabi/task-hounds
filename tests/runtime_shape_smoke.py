"""tests.runtime_shape_smoke - small focused smoke test for response shapes.

Why this exists
---------------
The UI crashed with `TypeError: null.content` because some routes returned
`None` (serialized as `null`) when there was no active session. The UI code
unconditionally did `d.content` on the response.

This test guards against that regression. It hits ~30 read-only endpoints
in-process (no network) and asserts that:

  1. status_code is 200 (not 500)
  2. response body is not None / null
  3. JSON shape matches what the UI expects (dict has expected keys,
     list is a list, etc.)

Runs in <3 seconds. No external dependencies, no DB write, no loop start.

Run standalone:
    set PYTHONPATH=core
    python tests/runtime_shape_smoke.py

Run via pytest:
    pytest tests/runtime_shape_smoke.py -v
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Allow running this file directly without setting PYTHONPATH.
# tests/runtime_shape_smoke.py -> ../../core is the package root.
_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from fastapi.testclient import TestClient  # noqa: E402

from task_hounds_api.api import create_app  # noqa: E402


DEFAULT_SESSION_ID = "ps_default"

ENDPOINTS: list[tuple[str, type, tuple[str, ...], dict | None]] = [
    ("/api/ping", dict, ("ok",), None),
    ("/api/health", dict, ("ok", "active_project_session", "opencode"), None),

    ("/api/dashboard/active", dict, (), None),
    ("/api/directive", dict, (), None),
    ("/api/directive/status", dict, (), None),
    ("/api/user-input/has-content", dict, (), None),

    ("/api/projects", list, ("id", "label", "path", "active", "path_missing"), None),
    ("/api/projects/active", dict, ("id", "label", "path", "active", "path_missing"), None),
    ("/api/workspaces", list, ("id", "label", "path", "active", "path_missing"), None),

    ("/api/agents", list, (), None),
    ("/api/agents/manager", dict, (), None),
    ("/api/agents/worker", dict, (), None),
    ("/api/agents/reviewer", dict, (), None),
    ("/api/agents/chat", dict, (), None),

    ("/api/plan", dict, (), {"session_id": DEFAULT_SESSION_ID}),
    ("/api/todos", list, (), {"session_id": DEFAULT_SESSION_ID}),
    ("/api/suggestion", dict, (), {"session_id": DEFAULT_SESSION_ID}),
    ("/api/handoff", dict, (), {"session_id": DEFAULT_SESSION_ID}),
    ("/api/manager-messages", list, (), {"session_id": DEFAULT_SESSION_ID}),
    ("/api/streams/manager-messages", list, (), {"session_id": DEFAULT_SESSION_ID}),

    ("/api/workflows/flow_01/plan", dict, (), None),
    ("/api/workflows/flow_01/todos", list, (), None),
    ("/api/workflows/flow_01/suggestion", dict, (), None),
    ("/api/workflows/flow_01/handoff", dict, (), None),
    ("/api/workflows/flow_01/manager-messages", list, (), None),

    ("/api/chat/messages", list, (), None),
    ("/api/chat/status", dict, ("ok", "enabled"), None),

    ("/api/timer/manager", dict, ("agent", "content"), None),
    ("/api/timer/worker", dict, ("agent", "content"), None),

    ("/api/runtime/opencode", dict, (), None),
    ("/api/runtime/status", dict, (), None),
    ("/api/opencode/agents", list, (), None),
]


def run_all() -> int:
    """Run the smoke test. Returns 0 on success, 1 on first failure."""
    print("Building app (this initializes the DB + seeds defaults)...")
    t0 = time.perf_counter()
    app = create_app()
    print(f"  app built in {time.perf_counter() - t0:.2f}s")

    passed = 0
    failed = 0
    failures: list[str] = []

    with TestClient(app) as client:
        print("  TestClient ready, starting requests", flush=True)
        for i, (path, expected_type, required_keys, query_params) in enumerate(ENDPOINTS):
            label = path
            if path.startswith("/api/streams/"):
                print(f"  [{i+1}/{len(ENDPOINTS)}] SKIP {path} (SSE - infinite stream)", flush=True)
                continue
            print(f"  [{i+1}/{len(ENDPOINTS)}] GET {path}", flush=True)
            try:
                resp = client.get(path, params=query_params)
            except Exception as exc:
                failed += 1
                failures.append(f"{label}: request raised {type(exc).__name__}: {exc}")
                continue

            if resp.status_code != 200:
                failed += 1
                body_preview = (resp.text or "")[:200]
                failures.append(
                    f"{label}: status {resp.status_code} (expected 200). "
                    f"body: {body_preview!r}"
                )
                continue

            try:
                body = resp.json()
            except Exception as exc:
                failed += 1
                failures.append(f"{label}: invalid JSON ({exc})")
                continue

            if body is None:
                failed += 1
                failures.append(
                    f"{label}: body is null (this is the original null.content bug)"
                )
                continue

            if not isinstance(body, expected_type):
                failed += 1
                failures.append(
                    f"{label}: expected {expected_type.__name__}, "
                    f"got {type(body).__name__}: {str(body)[:120]!r}"
                )
                continue

            if required_keys:
                target = body if isinstance(body, dict) else (body[0] if body else None)
                if target is not None:
                    missing = [k for k in required_keys if k not in target]
                    if missing:
                        failed += 1
                        failures.append(
                            f"{label}: missing required keys {missing}. "
                            f"got: {list(target.keys()) if isinstance(target, dict) else target!r}"
                        )
                        continue

            passed += 1

    elapsed = time.perf_counter() - t0
    print()
    print(f"  passed: {passed}/{passed + failed}")
    print(f"  failed: {failed}")
    print(f"  elapsed: {elapsed:.2f}s")

    if failures:
        print()
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print()
    print("OK: all endpoints returned 200 with the expected shape.")
    return 0


# ── pytest compatibility ──────────────────────────────────────────
# When pytest runs this file, treat the script logic as one test case.
import pytest  # noqa: E402


@pytest.mark.smoke
def test_all_endpoints_have_safe_shape():
    """Pytest wrapper around run_all() — fails the test on any mismatch."""
    rc = run_all()
    assert rc == 0, "runtime shape smoke test reported failures (see output above)"


if __name__ == "__main__":
    # Allow `python tests/runtime_shape_smoke.py`
    sys.exit(run_all())
