"""Chat agent runner for interactive project conversation."""
from __future__ import annotations

import os
from pathlib import Path

from task_hounds_api.db import ROOT
from task_hounds_api.db.ops import agent as db_agent
from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.db.ops import project as db_project
from task_hounds_api.opencode import client as oc_client
from task_hounds_api.opencode.binding_resolver import resolve_for_role
from task_hounds_api.workflow.signals import set_agent_state


def _opencode_port() -> int:
    raw = os.environ.get("TASK_HOUNDS_OPENCODE_PORT", "18765")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 18765


def _chat_agent_name() -> str:
    return os.environ.get("TASK_HOUNDS_CHAT_OPENCODE_AGENT", "Sisyphus - ultraworker")


def _chat_model() -> str:
    return os.environ.get("TASK_HOUNDS_CHAT_OPENCODE_MODEL") or os.environ.get(
        "TASK_HOUNDS_OPENCODE_MODEL",
        "minimax-coding-plan/MiniMax-M2.7",
    )


def _prompt(session_id: str, content: str, workspace_path: str, history: list[dict]) -> str:
    turns = "\n".join(
        f"{row.get('sender', 'unknown')}: {str(row.get('content', ''))[:1200]}"
        for row in history[-12:]
    ) or "(none)"
    return (
        "You are the Task Hounds Chat agent. Talk directly with the human about "
        "the currently active project session.\n\n"
        "Be concise and conversational. If the human clearly asks to turn the "
        "conversation into implementation work, suggest the exact directive they "
        "can send to Manager instead of silently starting work yourself.\n\n"
        f"Project session id: {session_id}\n"
        f"Workspace root: {workspace_path or ROOT}\n\n"
        f"Recent chat history:\n{turns}\n\n"
        f"Human message:\n{content}\n"
    )


def send(session_id: str, content: str, sender: str = "human") -> dict:
    """Append a human message, run Chat LLM, append reply, and return messages."""
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "empty_message", "messages": db_chat.list_chat(session_id)}

    db_chat.append_chat(session_id, content, sender=sender)
    active = db_project.get_session(session_id) or db_project.get_active_session() or {}
    workspace = Path(active.get("workspace_path") or ROOT)
    history = db_chat.list_chat(session_id, limit=30)

    set_agent_state("chat", "busy", "responding")
    try:
        host, port, agent, model = resolve_for_role("chat")
        result = oc_client.run(
            agent=agent,
            model=model,
            prompt=_prompt(session_id, content, str(workspace), history),
            host=host,
            port=port,
            timeout=300,
            cwd=workspace,
        )
        if not result.get("ok"):
            message = result.get("error", {}).get("message", "chat runtime unavailable")
            db_agent.update_agent("chat", state="error", last_error=message)
            return {"ok": False, "error": message, "messages": db_chat.list_chat(session_id)}

        reply = (result.get("output") or {}).get("text", "").strip()
        if not reply:
            reply = "(Chat agent returned an empty response.)"
        db_chat.append_chat(session_id, reply, sender="chat")
        return {"ok": True, "messages": db_chat.list_chat(session_id)}
    finally:
        set_agent_state("chat", "idle")
