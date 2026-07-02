"""Shared helpers for the UI/backend contract test suite.

These helpers live in `core/task_hounds_api/` (which is on
PYTHONPATH when running tests with `PYTHONPATH=core`) rather than
inside `docs/testing/tests/` because the test directory is not a Python
package — it has no `__init__.py`. A `from tests.X import Y` import
only works in some pytest collection modes; importing from
`task_hounds_api.contract_assets` works in all of them.

Both `docs/testing/tests/test_ui_backend_contract.py` and
`docs/testing/tests/test_phase4_v2.py` import from this module. Adding a new
contract test file? Import from here too.
"""
from __future__ import annotations

import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent  # core/task_hounds_api/contract_assets.py -> repo root
_UI_SRC = _REPO / "ui" / "web" / "src"

# State-machine parser for `apiXxx(...)` call sites in the UI
# source. Walk the call, extract the first argument as a path,
# and record it. Handles: string literals, template literals
# with ${dynamic} substitutions, identifiers (variables),
# optional generic types, and calls split across multiple lines.
_METHOD_TO_HTTP = {
    "apiGet": "GET",
    "apiPost": "POST",
    "apiPut": "PUT",
    "apiPatch": "PATCH",
    "apiDelete": "DELETE",
    "apiGetJson": "GET",
    "apiPostJson": "POST",
}

_CALL_HEAD_RE = re.compile(
    r"\b(apiGet|apiPost|apiPut|apiPatch|apiDelete|apiGetJson|apiPostJson)\b"
    r"\s*(?:<[^>]*>)?"   # optional generic type like <T>
    r"\s*\("               # opening paren of the call
)


def _extract_path(text: str, paren_open: int) -> tuple[str | None, int]:
    """Walk forward from just after `(` to extract the first argument
    as a path-like string. Returns (path, end_pos) or (None, end_pos)
    if no path could be identified.
    """
    i = paren_open + 1
    n = len(text)
    while i < n and text[i] in " \t\r\n":
        i += 1
    if i >= n:
        return None, i
    c = text[i]
    if c in ("'", '"'):
        quote = c
        i += 1
        start = i
        while i < n and text[i] != quote:
            if text[i] == "\\" and i + 1 < n:
                i += 2
                continue
            i += 1
        path = text[start:i]
        if i < n:
            i += 1
        return path, i
    if c == "`":
        i += 1
        start = i
        while i < n and text[i] != "`":
            if text[i] == "$" and i + 1 < n and text[i + 1] == "{":
                i += 2
                depth = 1
                while i < n and depth > 0:
                    ch = text[i]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                    i += 1
            else:
                i += 1
        path = text[start:i]
        if i < n:
            i += 1
        return path, i
    if c.isalpha() or c == "_" or c == "$":
        start = i
        while i < n and (text[i].isalnum() or text[i] in "_$"):
            i += 1
        return f"?var:{text[start:i]}", i
    return None, i


def collect_calls_from_root(ui_src: Path) -> list[tuple[str, str, str]]:
    """Walk every apiXxx call site under `ui_src`. Returns
    [(method, normalized_path, source_file), ...].
    """
    out: list[tuple[str, str, str]] = []
    if not ui_src.exists():
        return out
    for ts_file in ui_src.rglob("*"):
        if ts_file.suffix not in (".ts", ".tsx"):
            continue
        if "/lib/api." in str(ts_file) or "/lib/api.ts" in str(ts_file):
            continue
        try:
            text = ts_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for m in _CALL_HEAD_RE.finditer(text):
            method = m.group(1)
            paren_open = text.rfind("(", m.start(), m.end())
            if paren_open < 0:
                continue
            raw_path, _end = _extract_path(text, paren_open)
            if raw_path is None:
                continue
            method_short = _METHOD_TO_HTTP.get(method, method)
            try:
                rel = str(ts_file.relative_to(_REPO))
            except ValueError:
                rel = str(ts_file)
            if raw_path.startswith("?var:"):
                out.append((method_short, raw_path, rel))
            elif raw_path.startswith("/api/") or "/api/" in raw_path:
                normalized = re.sub(r"\$\{[^}]+\}", "{param}", raw_path)
                out.append((method_short, normalized, rel))
    return out


def collect_calls() -> list[tuple[str, str, str]]:
    """Default scan: walk the real UI source tree under ui/web/src."""
    return collect_calls_from_root(_UI_SRC)


# Manual whitelist of known variable resolutions. The parser cannot
# see inside ternary expressions like
#   `apiGet<X>(flow01Mode ? "/api/a" : "/api/b")`
# so it records the path as `?var:flow01Mode`. The operator MUST
# add an entry here for every such variable, listing the actual
# paths it can resolve to. The contract test then verifies that
# all listed paths exist in the backend. This is the "resolve/
# validate" path the user asked for.
KNOWN_VAR_RESOLUTIONS: dict[str, list[tuple[str, str]]] = {
    "flow01Mode": [
        ("GET", "/api/workflows/flow_01/suggestion"),
        ("GET", "/api/workflows/flow_01/manager-messages"),
        ("GET", "/api/suggestion"),
        ("GET", "/api/manager-messages"),
    ],
}


def build_route_index() -> set[tuple[str, str]]:
    """Return {(method, normalized_path), ...} for every registered
    FastAPI route. Path templates are normalized so /api/foo/{id}
    and /api/foo/{param} match each other.
    """
    from task_hounds_api.api import create_app

    app = create_app()
    seen: set[tuple[str, str]] = set()
    for r in app.routes:
        if not hasattr(r, "methods") or not hasattr(r, "path"):
            continue
        path = r.path
        for m in (r.methods or set()) - {"HEAD"}:
            seen.add((m, path))
            normalized = re.sub(r"\{[^}]+\}", "{param}", path)
            seen.add((m, normalized))
    return seen


def assert_runtimepanel_binding_put_collected() -> None:
    """Module-level helper (not a pytest test) that asserts
    RuntimePanel.tsx's PUT /api/runtime/bindings/${role} call is
    collected by the parser. Designed to be called from
    test_phase4_v2.py and from the test_runtimepanel_binding_put_*
    pytest test, so the same assertion is shared across both files
    without going through pytest's test import machinery.
    """
    runtime_panel = _UI_SRC / "components" / "ui" / "RuntimePanel.tsx"
    assert runtime_panel.exists(), f"RuntimePanel not found at {runtime_panel}"
    calls = collect_calls()
    panel_calls = [
        (m, p, s) for (m, p, s) in calls
        if s.endswith("RuntimePanel.tsx")
    ]
    assert panel_calls, "no apiXxx calls collected from RuntimePanel.tsx"

    binding_put = [
        (m, p, s) for (m, p, s) in panel_calls
        if m == "PUT" and p == "/api/runtime/bindings/{param}"
    ]
    assert binding_put, (
        f"RuntimePanel.tsx must contain a "
        f"PUT /api/runtime/bindings/${{role}} call (the apiPut fix). "
        f"Collected calls: {panel_calls}"
    )
