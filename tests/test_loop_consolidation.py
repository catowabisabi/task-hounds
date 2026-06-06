"""Tests for loop consolidation.

Regression for the duplicate-controller bug: there were TWO loop
implementations (BackgroundLoop + a module-level controller in
api/routes/workflow.py). Both are gone except BackgroundLoop.

After refactor:
  - api/routes/workflow.py has no _loop_thread / _loop_stop / _runner
  - /api/workflow/start-loop delegates to BackgroundLoop.start()
  - /api/workflow/stop-loop delegates to BackgroundLoop.stop()
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


def test_workflow_routes_delegate_to_background_loop():
    import inspect
    from task_hounds_api.api.routes import workflow as workflow_route

    src = inspect.getsource(workflow_route)
    assert "_loop_thread" not in src, (
        "api/routes/workflow.py should not define _loop_thread — "
        "use BackgroundLoop singleton instead"
    )
    assert "_loop_stop" not in src, (
        "api/routes/workflow.py should not define _loop_stop — "
        "use BackgroundLoop singleton instead"
    )
    assert "_runner" not in src, (
        "api/routes/workflow.py should not define _runner — "
        "use BackgroundLoop singleton instead"
    )
    assert "BackgroundLoop" in src, (
        "api/routes/workflow.py should reference BackgroundLoop"
    )
