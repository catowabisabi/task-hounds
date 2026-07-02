"""Independent project-aware Manager Chat agent."""
from __future__ import annotations

import json
from pathlib import Path

from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.db.ops import manager_chat as db_manager_chat
from task_hounds_api.db.ops import project as db_project
from task_hounds_api.db.ops import todo as db_todo
from task_hounds_api.db.ops import workflow as db_workflow
from task_hounds_api.db.ops import execution as db_execution
from task_hounds_api.opencode import client as oc_client
from task_hounds_api.opencode.binding_resolver import resolve_for_role
from task_hounds_api.workflow.executor import extract_json_object
from task_hounds_api.workflow.output_contracts import ManagerChatOutput
from task_hounds_api.workflow.prompt_policy import project_methodology


def _context(session_id: str) -> dict:
    directive = db_chat.get_latest_directive(session_id, status=None) or {}
    plan = db_workflow.get_plan(session_id) or {}
    return {
        "directive": directive.get("directive", ""),
        "plan": plan.get("content", ""),
        "active_todos": db_todo.list_active_todos(session_id),
        "recent_archived_todos": db_todo.list_archived_todos(session_id, limit=10),
        "handoff": db_workflow.get_handoff(session_id) or {},
        "worker_report": db_workflow.latest_worker_report(session_id) or {},
    }


def _prompt(session_id: str, message: str, conversation: list[dict] | None = None) -> str:
    return (
        "You are Manager Chat, a project-aware manager speaking directly with the human. "
        "Explain decisions clearly and briefly. Do not edit files or mutate records. "
        "Propose structured amendments for human confirmation instead.\n"
        "Strict amendment types:\n"
        "- suggestion: advice only; it will not enter the amendment checklist.\n"
        "- todo-amendment: payload contains the complete desired active `todos` list and "
        "optionally `archive_updates`.\n"
        "- user-directive-amend: payload contains `directive`.\n"
        "- handoff-amend: payload contains handoff fields to update.\n"
        f"SHARED PROJECT METHOD:\n{project_methodology()}\n"
        "Return exactly one JSON object and no surrounding prose.\n\n"
        f"PROJECT SESSION:\n{session_id}\n\n"
        f"PROJECT CONTEXT:\n{json.dumps(_context(session_id), ensure_ascii=False, default=str)}\n\n"
        f"MANAGER CHAT CONVERSATION:\n{json.dumps(conversation or [], ensure_ascii=False, default=str)}\n\n"
        f"HUMAN MESSAGE:\n{message}\n\n"
        f"OUTPUT SCHEMA:\n{json.dumps(ManagerChatOutput.model_json_schema(), ensure_ascii=False)}"
    )


def send(
    session_id: str,
    message: str,
    conversation: list[dict] | None = None,
) -> dict:
    text = (message or "").strip()
    if not text:
        return {"ok": False, "error": "empty_message"}
    project = db_project.get_session(session_id) or {}
    workspace_text = str(project.get("workspace_path") or "").strip()
    if not workspace_text:
        return {"ok": False, "error": "project workspace is missing"}
    workspace = Path(workspace_text)
    if not workspace.is_dir():
        return {"ok": False, "error": "project workspace is missing"}

    host, port, agent, model = resolve_for_role("manager")
    manager_chat_session_id = oc_client.precreate_session(host, port, cwd=workspace)
    if not manager_chat_session_id:
        return {"ok": False, "error": "Could not create Manager Chat session"}
    db_execution.bind_opencode_session(
        session_id, "manager_chat", manager_chat_session_id
    )
    execution_id = db_execution.execution_id(session_id, None, "manager_chat")
    db_execution.upsert_execution(
        execution_id=execution_id,
        project_session_id=session_id,
        role="manager_chat",
        status="busy",
        opencode_session_id=manager_chat_session_id,
        current_step="responding",
    )
    result = oc_client.run(
        agent=agent,
        model=model,
        prompt=_prompt(session_id, text, conversation),
        host=host,
        port=port,
        session_id=manager_chat_session_id,
        timeout=300,
        cwd=workspace,
        project_session_id=session_id,
        role="manager_chat",
        purpose="manager_chat",
        execution_id=execution_id,
    )
    if not result.get("ok"):
        db_execution.upsert_execution(
            execution_id=execution_id,
            project_session_id=session_id,
            role="manager_chat",
            status="error",
            error=result.get("error", {}).get("message", "Manager unavailable"),
        )
        return {"ok": False, "error": result.get("error", {}).get("message", "Manager unavailable")}
    raw = str((result.get("output") or {}).get("text") or "")
    try:
        payload = extract_json_object(raw, required_keys={"reply", "amendments"})
        parsed = ManagerChatOutput.model_validate(payload).model_dump(mode="json")
    except Exception as first_error:
        repair_session_id = oc_client.precreate_session(host, port, cwd=workspace)
        repair_prompt = (
            "Repair the malformed Manager Chat response below. Preserve its intended "
            "meaning. Return exactly one valid JSON object matching the schema, with "
            "no markdown or surrounding prose.\n\n"
            f"MALFORMED RESPONSE:\n{raw}\n\n"
            f"OUTPUT SCHEMA:\n{json.dumps(ManagerChatOutput.model_json_schema(), ensure_ascii=False)}"
        )
        repaired = oc_client.run(
            agent=agent,
            model=model,
            prompt=repair_prompt,
            host=host,
            port=port,
            session_id=repair_session_id,
            timeout=120,
            cwd=workspace,
            project_session_id=session_id,
            role="manager_chat",
            purpose="manager_chat_json_repair",
            execution_id=execution_id,
        )
        try:
            repaired_raw = str((repaired.get("output") or {}).get("text") or "")
            payload = extract_json_object(
                repaired_raw, required_keys={"reply", "amendments"}
            )
            parsed = ManagerChatOutput.model_validate(payload).model_dump(mode="json")
        except Exception as repair_error:
            message = (
                "Manager returned an invalid response format after one repair attempt. "
                "Please send the message again."
            )
            db_execution.upsert_execution(
                execution_id=execution_id,
                project_session_id=session_id,
                role="manager_chat",
                status="error",
                error=f"{message} First: {first_error}; repair: {repair_error}",
            )
            return {"ok": False, "error": message}
    response_id = db_manager_chat.save_response(session_id, parsed["reply"], parsed["amendments"])
    db_execution.upsert_execution(
        execution_id=execution_id,
        project_session_id=session_id,
        role="manager_chat",
        status="idle",
        opencode_session_id=manager_chat_session_id,
    )
    return {
        "ok": True,
        "response_id": response_id,
        "reply": parsed["reply"],
        "amendments": db_manager_chat.list_amendments(session_id),
    }
