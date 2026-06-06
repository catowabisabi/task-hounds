"""Phase 4: prompt contract tests.

Asserts that the key behavioral rules the user mandated in the
Phase 4 spec are PRESENT in the prompt files. If a future refactor
removes any of these rules, the test fails so the gap is caught
before the next E2E run.

Rules enforced (per the user's Phase 4 spec):

Worker prompt must:
  - Bind the agent to a single workspace root
  - Forbid file creation outside the workspace (sibling/parent/tmp)
  - Require reading existing project + AGENTS.md + package/config before writing
  - Forbid false reports of files_changed and tests
  - Require the Worker to actually run acceptance checks
  - Require a report shape with files_changed, test_result, known_issues

Reviewer prompt must:
  - Verify reported files are inside the active workspace
  - Compare directive vs Manager task vs actual diff
  - Require proof tests actually ran
  - Fail on workspace boundary breach
  - State that reasoning is not proof

Chat prompt must:
  - Be honest about not executing work
  - Forbid claiming execution that did not happen
  - Return a clear error when runtime is unavailable
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

_PROMPTS_DIR = _CORE / "task_hounds_api" / "agent_prompts"


def _read(name: str) -> str:
    p = _PROMPTS_DIR / name
    assert p.exists(), f"prompt file missing: {p}"
    return p.read_text(encoding="utf-8")


# ── Worker prompt contracts ────────────────────────────────────────────────


def test_worker_prompt_binds_agent_to_a_single_workspace_root():
    text = _read("worker_prompts.md")
    assert "WORKSPACE BOUNDARY" in text or "workspace_path" in text, (
        "worker prompt must mention a single workspace boundary"
    )
    assert "{flow_input.workspace_path" in text, (
        "worker prompt must inject flow_input.workspace_path as the active workspace"
    )


def test_worker_prompt_forbids_file_creation_outside_workspace():
    text = _read("worker_prompts.md")
    forbidden = "Do NOT create, copy, or link files in any sibling project"
    assert forbidden in text, (
        f"worker prompt must forbid files outside the workspace; expected: {forbidden!r}"
    )
    assert "workspace-escape" in text or "outside it is a contract violation" in text, (
        "worker prompt must explicitly call out workspace-escape as a contract violation"
    )


def test_worker_prompt_requires_reading_existing_project_before_writing():
    text = _read("worker_prompts.md")
    assert "AGENTS.md" in text, "worker prompt must mention AGENTS.md"
    assert "package.json" in text or "pyproject.toml" in text, (
        "worker prompt must mention package config files (package.json or pyproject.toml)"
    )
    assert "Read the existing project" in text or "before you add anything" in text, (
        "worker prompt must require reading the existing project before writing"
    )


def test_worker_prompt_forbids_false_reports_of_files_changed_and_tests():
    text = _read("worker_prompts.md")
    assert "Do NOT claim files_changed unless you actually wrote them" in text, (
        "worker prompt must forbid false files_changed claims"
    )
    assert "Do NOT claim tests passed unless you actually ran them" in text, (
        "worker prompt must forbid false test-pass claims"
    )


def test_worker_prompt_requires_real_acceptance_check_command():
    text = _read("worker_prompts.md")
    assert "acceptance_check" in text, (
        "worker prompt must require an acceptance_check field with a real command + output"
    )
    assert "the EXACT command" in text, (
        "worker prompt must demand the exact command, not a description"
    )


def test_worker_prompt_report_shape_includes_required_fields():
    text = _read("worker_prompts.md")
    for field in ("files_changed", "test_result", "test_command", "acceptance_check", "known_issues"):
        assert f'"{field}"' in text, (
            f"worker prompt report shape must include {field!r}"
        )


# ── Reviewer prompt contracts ─────────────────────────────────────────────


def test_reviewer_prompt_must_verify_files_inside_active_workspace():
    text = _read("reviewer_prompts.md")
    assert "WORKSPACE BOUNDARY" in text or "workspace" in text.lower(), (
        "reviewer prompt must mention workspace boundary verification"
    )
    assert "must EXIST on disk" in text or "must actually have been run" in text, (
        "reviewer prompt must require real file existence and real test execution"
    )
    assert "automatic FAIL" in text, (
        "reviewer prompt must use 'automatic FAIL' wording for boundary / missing-file cases"
    )


def test_reviewer_prompt_must_require_proof_tests_actually_ran():
    text = _read("reviewer_prompts.md")
    assert "TESTS ACTUALLY RAN" in text or "test_command must be a real shell command" in text, (
        "reviewer prompt must require test command + output as proof"
    )
    assert "exit 0" in text and "exit 1" in text, (
        "reviewer prompt must distinguish exit 0 vs exit 1"
    )


def test_reviewer_prompt_must_compare_directive_vs_diff():
    text = _read("reviewer_prompts.md")
    assert "FILES MATCH THE DIRECTIVE" in text or "directive" in text, (
        "reviewer prompt must compare Worker's claim against the directive"
    )


def test_reviewer_prompt_must_state_reasoning_is_not_proof():
    text = _read("reviewer_prompts.md")
    assert "reasoning is NOT proof" in text or "Reasoning is NOT proof" in text, (
        "reviewer prompt must explicitly state that reasoning alone is not proof"
    )


def test_reviewer_prompt_qa_result_must_be_pass_fail_or_needs_review():
    text = _read("reviewer_prompts.md")
    assert "pass, fail, needs_review" in text, (
        "reviewer prompt must enumerate the 3 valid qa_result values"
    )


# ── Chat prompt contracts ─────────────────────────────────────────────────


def test_chat_prompt_must_be_honest_about_not_executing_work():
    text = _read("chat_prompts.md")
    assert "NEVER claim you executed" in text or "not claim you executed" in text, (
        "chat prompt must forbid claiming work was executed"
    )
    assert "CONVERSATION, not implementation" in text or "not implementation" in text, (
        "chat prompt must define its role as conversation, not implementation"
    )


def test_chat_prompt_must_redirect_to_directive_for_work():
    text = _read("chat_prompts.md")
    assert "directive" in text.lower(), (
        "chat prompt must mention directive (the canonical way to start work)"
    )
    assert "redirect" in text or "suggest the exact directive" in text or "do NOT" in text, (
        "chat prompt must tell the agent to redirect work to a directive"
    )


def test_chat_prompt_must_surface_runtime_unavailable_error_clearly():
    text = _read("chat_prompts.md")
    assert "runtime_available" in text, (
        "chat prompt must check runtime_available before claiming any work"
    )
    assert "Runtime is unavailable" in text or "runtime_available is False" in text, (
        "chat prompt must surface a clear 'Runtime is unavailable' error"
    )
    assert "opencode.jsonc" in text, (
        "chat prompt must tell the user where to find the env-var placeholders"
    )


def test_chat_prompt_must_not_say_i_tried_but_failed():
    text = _read("chat_prompts.md")
    assert "I tried but failed" in text or "I encountered an error" in text, (
        "chat prompt must explicitly forbid the 'I tried but failed' lie"
    )


# ── Cross-cutting: prompts reference workspace_path (the runtime injects) ─


def test_prompts_do_not_have_placeholder_workspace_strings():
    """None of the prompts should hardcode a workspace path or
    assume a project layout. The runtime injects {workspace_path}
    via the binding resolver."""
    for name in ("worker_prompts.md", "reviewer_prompts.md", "chat_prompts.md"):
        text = _read(name)
        assert "/home/" not in text and "/Users/" not in text and "C:\\Users" not in text, (
            f"{name} must not hardcode a workspace path"
        )
        assert "TODO" not in text.split("```")[0] or "TODO" not in text, (
            f"{name} must not contain a 'TODO' placeholder (per the user's stub-removal directive)"
        )
