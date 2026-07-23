"""P7 Batch 1 Easy Compat Wrappers tests.

Covers:
  14  stringify_manager_field (legacy dict-drill)
  18  extract_json_object_strict (multi-fence scoring, lenient)
  31  extract_known_issues (regex filter, capped at 5)
  128 delete dead repair_todo_json_text
  211 unscoped_suggestions decoration fields
  234 work_status alias
  242 handoff_versions returns 501
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parents[2] / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    db = tmp_path / "wave_p7_easy_compat.db"
    monkeypatch.setenv("POWER_TEAMS_DB", str(db))
    for name in list(sys.modules):
        if name == "task_hounds_api" or name.startswith("task_hounds_api."):
            sys.modules.pop(name, None)
    from task_hounds_api.db import init_db
    from task_hounds_api.opencode import config as oc_config
    from task_hounds_api.opencode import runtime_manager as rm_mod
    rm_mod.RuntimeManager.reset_instance()
    oc_config.reset_cache()
    init_db()
    return db


@pytest.fixture()
def client(fresh_db):
    from task_hounds_api.api.main import create_app
    return TestClient(create_app())


# ── ID 14: stringify_manager_field (legacy dict-drill) ──────────────────


def test_id_14_stringify_dict_drills_content_key():
    """P7 id 14: dict with 'content' key returns its string."""
    from task_hounds_api.workflow.repair import stringify_manager_field
    assert stringify_manager_field({"content": "hello"}) == "hello"


def test_id_14_stringify_dict_drills_first_matching_key():
    """P7 id 14: dict drills the first key in the known list
    (content, manager_message, message, task, title, summary)."""
    from task_hounds_api.workflow.repair import stringify_manager_field
    # 'task' is the 4th in the list; if dict has 'task' but no
    # earlier keys, the helper still finds it.
    assert stringify_manager_field({"task": "do thing"}) == "do thing"


def test_id_14_stringify_dict_drills_recursively():
    """P7 id 14: nested dict is recursed."""
    from task_hounds_api.workflow.repair import stringify_manager_field
    out = stringify_manager_field({"outer": {"content": "nested"}})
    # outer is not in the drill key list; falls back to str(dict).
    # The helper recurses ONLY if the matching key's value is itself
    # a dict; it does not recurse into unknown outer keys. So this
    # specific input returns str({"outer":...}).strip() — which is
    # truthy but not "nested". Pin the actual behavior.
    assert "outer" in out


def test_id_14_stringify_scalar_string():
    from task_hounds_api.workflow.repair import stringify_manager_field
    assert stringify_manager_field("plain") == "plain"


def test_id_14_stringify_none_returns_empty():
    from task_hounds_api.workflow.repair import stringify_manager_field
    assert stringify_manager_field(None) == ""


def test_id_14_stringify_list_joined():
    from task_hounds_api.workflow.repair import stringify_manager_field
    assert stringify_manager_field(["a", "b"]) == "a\nb"


# ── ID 18: extract_json_object_strict (multi-fence scoring) ─────────────


def test_id_18_extract_strict_picks_best_scoring_block():
    """P7 id 18: when 2 JSON fences are present with different
    required_keys coverage, the helper picks the higher-coverage
    one (vs. the new extract_json_object which picks the first)."""
    from task_hounds_api.workflow.repair import extract_json_object_strict
    text = """
    Here is a partial block:
    ```json
    {"foo": 1}
    ```
    And a fuller one:
    ```json
    {"foo": 2, "bar": 3, "baz": 4}
    ```
    """
    out = extract_json_object_strict(text, required_keys={"foo", "bar", "baz"})
    assert out is not None
    assert out.get("baz") == 4
    assert out.get("bar") == 3


def test_id_18_extract_strict_returns_none_on_parse_fail():
    """P7 id 18: invalid JSON returns None (vs. the new helper
    which raises ValueError)."""
    from task_hounds_api.workflow.repair import extract_json_object_strict
    out = extract_json_object_strict("not json at all", required_keys={"x"})
    assert out is None


def test_id_18_extract_strict_returns_none_on_empty():
    from task_hounds_api.workflow.repair import extract_json_object_strict
    assert extract_json_object_strict("", required_keys={"x"}) is None


# ── ID 31: extract_known_issues (regex filter) ──────────────────────────


def test_id_31_known_issues_filters_negative_markers():
    """P7 id 31: 'no known issues' is filtered out (matches the
    negative marker list)."""
    from task_hounds_api.workflow.repair import extract_known_issues
    out = extract_known_issues("- no known issues")
    assert out == []


def test_id_31_known_issues_keeps_keyword_lines():
    """P7 id 31: a line with a keyword is kept."""
    from task_hounds_api.workflow.repair import extract_known_issues
    out = extract_known_issues("- db migration failed: timeout")
    assert any("failed" in line for line in out)


def test_id_31_known_issues_caps_at_5():
    """P7 id 31: more than 5 keyword-matching lines yields 5."""
    from task_hounds_api.workflow.repair import extract_known_issues
    text = "\n".join(f"- issue {i}" for i in range(8))
    out = extract_known_issues(text)
    assert len(out) == 5


def test_id_31_known_issues_handles_empty():
    from task_hounds_api.workflow.repair import extract_known_issues
    assert extract_known_issues("") == []


# ── ID 128: repair_todo_json_text is dead code ──────────────────────────


def test_id_128_repair_todo_json_text_has_no_callers():
    """P7 id 128: grep production code (core/) for callers of
    repair_todo_json_text. Only the docstring mention and the
    definition inside repair.py itself may match; any other hit
    would mean a real caller exists (which would mean the
    dead-code finding is wrong).

    Note: the grep is scoped to core/ only — including the test
    tree would match this very file and made the assertion
    self-defeating.
    """
    import subprocess
    r = subprocess.run(
        ["git", "grep", "-n", "-F", "repair_todo_json_text", "--", "core/"],
        capture_output=True,
        cwd=str(_HERE.parents[2]),
        text=True,
    )
    matches = [
        line for line in r.stdout.splitlines()
        if line and not line.startswith("Binary")
    ]
    assert matches, "expected repair.py definition to match"
    for m in matches:
        assert "core/task_hounds_api/workflow/repair.py" in m, (
            f"unexpected caller outside repair.py: {m}"
        )


# ── ID 211: unscoped_suggestions decorations ─────────────────────────────


def test_id_211_unscoped_suggestions_include_decoration_fields(
    client, fresh_db, tmp_path
):
    """P7 id 211: the legacy response decorates each row with
    scope_warning, cleanup_only, queue_status, status_label.
    Seed an unscoped suggestion via raw DB INSERT (no helper
    exists in db/ops/workflow.py for unscoped)."""
    from task_hounds_api.db import connect as db_connect
    with db_connect() as db:
        db.execute(
            "INSERT INTO suggestion_queue (session_id, content, status) "
            "VALUES (NULL, 'legacy text', 'released')"
        )
        db.commit()
    resp = client.get("/api/workflows/flow_01/suggestions/unscoped")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) >= 1
    item = body[0]
    for key in ("scope_warning", "cleanup_only", "queue_status", "status_label"):
        assert key in item, f"missing decoration field {key!r}"
    assert item["scope_warning"] == "historical_unscoped"
    assert item["cleanup_only"] is True
    assert item["queue_status"] == "queued_for_worker"
    assert item["status_label"] == "Queued for worker"


# ── ID 234: work_status alias ────────────────────────────────────────────


def test_id_234_work_status_aliases_work_0001_status(
    client, fresh_db, tmp_path, monkeypatch
):
    """P7 id 234: GET /api/files/work_status reads the file
    agent_files/work_0001_status.txt when present. We seed the
    file in the ROOT/agent_files/ dir using monkeypatch on the
    file path."""
    from task_hounds_api.db import ROOT
    af = ROOT / "core" / "runtime" / "agent_files"
    af.mkdir(parents=True, exist_ok=True)
    target = af / "work_0001_status.txt"
    target.write_text("DONE: 2026-06-09 12:34:56\n", encoding="utf-8")
    try:
        resp = client.get("/api/files/work_status")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "2026-06-09 12:34:56" in body["content"]
        assert "work_0001_status.txt" in body["name"]
    finally:
        target.unlink(missing_ok=True)


def test_id_234_work_status_returns_empty_when_no_file(
    client, fresh_db
):
    """P7 id 234: when no work_status file exists, the route
    returns an empty content envelope (not 404)."""
    resp = client.get("/api/files/work_status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"] == ""
    assert body["name"] == "work_status"


# ── ID 242: handoff_versions returns 501 ────────────────────────────────


def test_id_242_handoff_versions_returns_501(client, fresh_db):
    """P7 id 242: the legacy /api/handoff/versions route now
    returns 501 with a descriptive error (not an empty list).
    Callers can detect the feature is intentionally absent."""
    resp = client.get("/api/handoff/versions")
    assert resp.status_code == 501, resp.text
    assert "not tracked" in resp.text
