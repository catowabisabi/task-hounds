"""Contract test: every URL path called by the React UI must exist
in the FastAPI backend.

Catches "UI silently fails because the backend path drifted"
regressions that the shape smoke would miss (because shape smoke
only tests the docs-listed endpoints).

The parser, the ?var:path resolution whitelist, and the
RuntimePanel binding-PUT assertion helper all live in
`task_hounds_api.contract_assets` (a non-test module under
`core/task_hounds_api/`). This file contains only the pytest test
cases; the shared logic is in the contract_assets module so the
test directory does not need to be a Python package for the
parser/assertion to be reusable across multiple test files.

Two assertion groups:

  - `test_ui_calls_have_backend_routes` — every (method, path) used
    by the UI under /api/runtime/ or /api/workflow/ must exist in
    the FastAPI app. This is the slice of the UI the runtime
    refactor is responsible for.

  - `test_runtimepanel_binding_put_is_collected` — explicit assertion
    that the RuntimePanel's `PUT /api/runtime/bindings/${role}` call
    (the one that was changed from apiPost to apiPut in the
    apiPost→apiPut fix) IS in the collected set. This guards against
    a future parser regression that would silently miss template
    literals, and it proves the parser actually sees the new
    apiPut call site.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_UI_SRC = _REPO / "ui" / "web" / "src"

# Shared parser, route index, and KNOWN_VAR_RESOLUTIONS whitelist.
# All in `core/task_hounds_api/contract_assets.py` (a non-test
# module) so both this file and `tests/test_phase4_v2.py` can
# import them without `tests/` needing to be a package.
from task_hounds_api.contract_assets import (  # noqa: E402
    KNOWN_VAR_RESOLUTIONS,
    _extract_path as _shared_extract_path,
    _CALL_HEAD_RE as _shared_call_head_re,
    _METHOD_TO_HTTP as _shared_method_to_http,
    _REPO as _shared_repo,
    assert_runtimepanel_binding_put_collected,
    build_route_index,
    collect_calls,
    collect_calls_from_root,
)


def _var_paths(calls):
    """Return all entries where the path is a variable (parser
    produced `?var:NAME`) — these cannot be auto-verified and must
    be surfaced to a human for manual review."""
    return [(m, p, s) for (m, p, s) in calls if isinstance(p, str) and p.startswith("?var:")]


def test_ui_calls_have_backend_routes():
    """Every (method, path) used by the UI under /api/runtime/* or
    /api/workflow/* must exist in the FastAPI app. Template-literal
    paths with ${dynamic} are normalized to {param} so they match
    the FastAPI placeholder. Non-generic calls (no <T> between the
    method name and the opening paren) are also collected.

    A path passed as a variable (?var:NAME) cannot be auto-verified
    by the parser. For each one, the test checks KNOWN_VAR_RESOLUTIONS:
      - if the variable is in the whitelist, every (method, path)
        tuple it lists must exist in the backend route table
      - if the variable is NOT in the whitelist, the test fails
        loudly with the variable name, source file, and a hint
        about how to add a resolution
    """
    calls = collect_calls()
    var_only = _var_paths(calls)
    runtime_calls = [
        (m, p, s) for (m, p, s) in calls
        if isinstance(p, str) and p.startswith("/api/")
        and ("/api/runtime/" in p or "/api/workflow/" in p)
    ]
    if var_only:
        undeclared: list[tuple[str, str, str, list[str]]] = []
        for _method, p, src in var_only:
            name = p.split(":", 1)[1]
            if name not in KNOWN_VAR_RESOLUTIONS:
                undeclared.append((name, p, src, []))
        if undeclared:
            lines = "\n".join(
                f"  var={n!r}  (parsed as {p!r}, source {s})"
                for n, p, s, _ in undeclared
            )
            pytest.fail(
                f"{len(undeclared)} UI variable path(s) have no entry "
                f"in KNOWN_VAR_RESOLUTIONS. Add an entry to the "
                f"whitelist in core/task_hounds_api/contract_assets.py "
                f"listing the actual paths the variable can resolve "
                f"to (typically a ternary expression like `var ? "
                f"'/api/a' : '/api/b'`).\n{lines}"
            )
        backend = build_route_index()
        missing: list[tuple[str, str, str, str]] = []
        for _method, p, src in var_only:
            name = p.split(":", 1)[1]
            for m, real_path in KNOWN_VAR_RESOLUTIONS[name]:
                if (m, real_path) in backend:
                    continue
                normalized = re.sub(r"\{[^}]+\}", "{param}", real_path)
                if (m, normalized) in backend:
                    continue
                missing.append((name, m, real_path, src))
        if missing:
            lines = "\n".join(
                f"  var={n!r}  {m} {p}  (in {s})"
                for n, m, p, s in missing
            )
            pytest.fail(
                f"{len(missing)} resolved path(s) for whitelisted "
                f"variables do not exist in the backend route table:\n"
                f"{lines}\n\n"
                f"Either update the UI to use a real path, or add the "
                f"missing route in api.routes.* (with a real "
                f"implementation, not a compat stub)."
            )
    assert runtime_calls, (
        "no runtime API calls collected — parser found nothing under "
        "/api/runtime/ or /api/workflow/. Check that the UI source "
        "tree is present and that calls are written as apiXxx(...)."
    )

    backend = build_route_index()
    missing: list[tuple[str, str, str]] = []
    for method, path, src in runtime_calls:
        if (method, path) in backend:
            continue
        missing.append((method, path, src))

    if missing:
        lines = "\n".join(
            f"  {m} {p}  (in {s})" for m, p, s in missing
        )
        pytest.fail(
            f"{len(missing)} UI runtime endpoint(s) have no matching backend route:\n{lines}\n\n"
            f"This usually means the UI is calling a path that the "
            f"backend no longer serves. Either update the UI to use "
            f"the new path/method, or add the missing route in "
            f"api.routes.* (with a real implementation, not a compat "
            f"stub)."
        )


def test_runtimepanel_binding_put_is_collected():
    """Explicit assertion that RuntimePanel's
    `PUT /api/runtime/bindings/${role}` is collected by the parser.

    This guards the apiPost → apiPut fix from commit ba86670: if a
    future refactor of the parser loses the ability to handle
    template-literal paths (with ${...} substitution) this test will
    fail loudly, instead of silently letting the UI drift back to
    a non-existent apiPost endpoint.

    The actual assertion is delegated to the module-level helper
    `assert_runtimepanel_binding_put_collected` (in
    task_hounds_api.contract_assets) so it can be reused from
    test_phase4_v2.py without going through pytest's test import
    machinery (which is fragile across collection modes).
    """
    assert_runtimepanel_binding_put_collected()


def test_known_var_resolutions_have_real_backend_routes():
    """Sanity: every (method, path) in KNOWN_VAR_RESOLUTIONS must
    exist in the backend route table. If a real route is removed
    or renamed, this test fails BEFORE any UI call tries to use
    it. Catches the case where the whitelist drifts out of sync
    with the actual backend."""
    backend = build_route_index()
    bad: list[tuple[str, str, str, str]] = []
    for var, resolutions in KNOWN_VAR_RESOLUTIONS.items():
        for m, p in resolutions:
            if (m, p) in backend:
                continue
            normalized = re.sub(r"\{[^}]+\}", "{param}", p)
            if (m, normalized) in backend:
                continue
            bad.append((var, m, p, "real"))
    assert not bad, (
        f"KNOWN_VAR_RESOLUTIONS references routes that no longer "
        f"exist in the backend:\n"
        + "\n".join(f"  {v}: {m} {p}" for v, m, p, _ in bad)
    )


def test_parser_handles_non_generic_and_template_literal_calls(tmp_path: Path):
    """Micro-test for the parser. Constructs a synthetic TS source
    fragment with:
      (a) a non-generic apiPost call
      (b) a template-literal path with ${dynamic}
      (c) a generic apiGet<...> call
      (d) a path passed as a variable
    and asserts the parser collects all four correctly.
    """
    synth = """
import { apiGet, apiPost, apiPut, apiPatch, apiDelete } from "../../lib/api";

// (a) non-generic call
apiPost("/api/foo/no-generic", { x: 1 });

// (b) template literal with ${dynamic}
apiPut(`/api/foo/${name}/thing`, { x: 2 });

// (c) generic call
apiGet<{ items: string[] }>("/api/foo/typed");

// (d) path as variable
const PATH = "/api/foo/from-var";
apiGet(PATH);
"""
    src = tmp_path / "src"
    synth_panel = src / "components" / "ui" / "SynthPanel.tsx"
    synth_panel.parent.mkdir(parents=True, exist_ok=True)
    synth_panel.write_text(synth, encoding="utf-8")

    calls = collect_calls_from_root(src)
    methods_paths = {(m, p) for (m, p, _s) in calls if not p.startswith("?var:")}
    assert ("POST", "/api/foo/no-generic") in methods_paths, (
        f"non-generic call missed: {methods_paths}"
    )
    assert ("PUT", "/api/foo/{param}/thing") in methods_paths, (
        f"template-literal call missed or not normalized: {methods_paths}"
    )
    assert ("GET", "/api/foo/typed") in methods_paths, (
        f"generic call missed: {methods_paths}"
    )
    var_calls = [p for (_m, p) in {(m, p) for (m, p, _s) in calls} if p.startswith("?var:")]
    assert var_calls, f"variable path call missed: {var_calls}"


def test_runtime_status_contains_ready_field():
    """Regression for the Phase 1 credential policy: the runtime
    status endpoint must surface ready / runtime_available /
    unavailable_reason. The UI RuntimeStatus interface mirrors this;
    if a future refactor drops these fields, the UI silently misreads
    the runtime as ready when it is not."""
    from fastapi.testclient import TestClient
    from task_hounds_api.api import create_app
    from task_hounds_api.opencode import config as oc_config
    from task_hounds_api.opencode import runtime_manager as rm_mod
    import os

    db = os.environ.get("POWER_TEAMS_DB")
    if db:
        Path(db).unlink(missing_ok=True)
    os.environ["TASK_HOUNDS_OPENCODE_PORT"] = "18995"
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()

    with TestClient(create_app()) as c:
        body = c.get("/api/runtime/status").json()
    assert "ready" in body, f"runtime_status must include 'ready' field, got keys: {list(body.keys())}"
    assert "runtime_available" in body
    assert "unavailable_reason" in body
    assert "managed_health" in body
    assert "credential_warnings" in body["managed_health"]
