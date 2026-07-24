import pytest
from pydantic import ValidationError

from task_hounds_api.workflow.output_contracts import (
    AgentIssue,
    ArchiveDirective,
    ManagerOutput,
    ManagerChatOutput,
    QaResult,
    ReviewerOutput,
    WorkerOutput,
)


def issue(description="problem"):
    return {
        "type": "bug",
        "severity": "high",
        "description": description,
        "evidence": "test evidence",
    }


def test_reviewer_contract_accepts_only_declared_enum_and_shape():
    parsed = ReviewerOutput.model_validate({
        "reviewer_feedback": "Checked.",
        "qa_result": "pass",
        "bugs": [issue()],
        "uiux_suggestions": [],
        "possible_problems": [],
        "safety_security_risks": [],
    })
    assert parsed.qa_result == QaResult.pass_
    assert parsed.bugs[0] == AgentIssue.model_validate(issue())


@pytest.mark.parametrize("qa_result", ["passed", "ok", True, {"value": "pass"}])
def test_reviewer_contract_rejects_unknown_qa_values(qa_result):
    with pytest.raises(ValidationError):
        ReviewerOutput.model_validate({
            "reviewer_feedback": "Checked.",
            "qa_result": qa_result,
            "bugs": [],
            "uiux_suggestions": [],
            "possible_problems": [],
            "safety_security_risks": [],
        })


def test_contracts_forbid_unknown_keys_and_unstructured_issues():
    with pytest.raises(ValidationError):
        WorkerOutput.model_validate({
            "files_changed": [],
            "test_result": "pass",
            "known_issues": ["plain string is not an AgentIssue"],
            "surprise": "not allowed",
        })


def test_manager_contract_rejects_invalid_todo_status():
    with pytest.raises(ValidationError):
        ManagerOutput.model_validate({
            "input_digest": "digest",
            "decision": {"action": "execute", "summary": "do the task"},
            "manager_message": "message",
            "plan": "plan",
            "todo_list": [{
                "content": "task",
                "status": "blocked",
                "priority": "urgent",
                "owner": "manager",
            }],
            "suggestion_content": "task",
            "suggestion_verification": "verify",
            "handoff_update": {},
        })


def test_contract_enforces_text_and_array_limits():
    with pytest.raises(ValidationError):
        AgentIssue.model_validate({
            **issue(),
            "description": "x" * 1001,
        })
    with pytest.raises(ValidationError):
        WorkerOutput.model_validate({
            "files_changed": [f"file-{i}" for i in range(51)],
            "test_result": "pass",
            "known_issues": [],
        })


def test_other_archive_reason_requires_explanation():
    with pytest.raises(ValidationError):
        ArchiveDirective.model_validate({
            "todo_id": "todo-1",
            "reason": "other",
            "note": "",
        })


@pytest.mark.parametrize(
    ("amendment_type", "payload"),
    [
        ("user-directive-amend", {}),
        ("todo-amendment", {"todos": "not-a-list"}),
        ("handoff-amend", {}),
    ],
)
def test_manager_chat_rejects_invalid_amendment_payload(amendment_type, payload):
    with pytest.raises(ValidationError):
        ManagerChatOutput.model_validate({
            "reply": "I have a proposed update.",
            "amendments": [{
                "type": amendment_type,
                "title": "Update project state",
                "payload": payload,
            }],
        })


def test_manager_chat_suggestion_does_not_require_mutation_payload():
    parsed = ManagerChatOutput.model_validate({
        "reply": "Here is my advice.",
        "amendments": [{
            "type": "suggestion",
            "title": "Consider running the focused tests",
            "payload": {},
        }],
    })
    assert parsed.amendments[0].type.value == "suggestion"


def test_manager_reopen_requires_reason_and_evidence():
    base = {
        "input_digest": "digest",
        "decision": {"action": "retry", "summary": "recheck"},
        "manager_message": "Rechecking.",
        "plan": "Verify the evidence.",
        "todo_list": [],
        "suggestion_content": "Verify",
        "suggestion_verification": "Run test",
        "handoff_update": {},
    }
    with pytest.raises(ValidationError):
        ManagerOutput.model_validate({
            **base,
            "reopen_todos": [{
                "todo_id": "todo-1",
                "reason": "Regression suspected",
                "evidence": [],
            }],
        })


def test_manager_contract_retry_failure_returns_safe_payload(monkeypatch, tmp_path):
    from task_hounds_api.workflow import executor
    from task_hounds_api.workflow.models import FlowInput, FlowState

    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "output": {"text": "I cannot emit JSON this time."}}

    flow_input = FlowInput(
        power_team_project_id="pt-test",
        project_session_id="ps-test",
        human_directive="Build the first safe slice.",
        human_suggested_new_task_or_item="Inspect the broken Manager response.",
        workspace_path=str(tmp_path),
        run_id=123,
    )
    state = FlowState(
        flow_input=flow_input,
        input_digest="digest",
        existing_context={"plan": "Existing plan", "handoff_update": {"current_task": "Current task"}},
    )

    monkeypatch.setattr(executor, "resolve_for_role", lambda role: ("127.0.0.1", 9999, role, "model"))
    monkeypatch.setattr(executor, "resolve_workspace", lambda flow_input, role: str(tmp_path))
    monkeypatch.setattr(executor, "_ensure_role_session", lambda *args, **kwargs: "session-id")
    monkeypatch.setattr(executor, "_blocking_credential_warnings_for_role", lambda role: [])
    monkeypatch.setattr(executor.oc_client, "run", fake_run)

    result = executor._call_manager(state)

    assert len(calls) == 2
    payload = result["payload"]
    parsed = ManagerOutput.model_validate(payload)
    assert parsed.decision.action.value == "request_human"
    assert "structured JSON contract" in parsed.manager_message
