"""Chat agent runner for interactive project conversation."""
from __future__ import annotations

import os
import json
from pathlib import Path
from datetime import datetime, timezone

from task_hounds_api.db.ops import agent as db_agent
from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.db.ops import project as db_project
from task_hounds_api.opencode import client as oc_client
from task_hounds_api.opencode.binding_resolver import resolve_for_role
from task_hounds_api.workflow.signals import set_agent_state
from task_hounds_api.workflow.prompt_policy import project_methodology
from task_hounds_api.workflow.executor import extract_json_object
from task_hounds_api.workflow.output_contracts import ChatDirectiveOutput
from task_hounds_api.db import ROOT


LANG_INSTRUCTIONS = {
    "en":    "You MUST respond entirely in English. All output, explanations, and structured tags must be in English.",
    "zh-tw": "【語言指令】你必須完全使用繁體中文回應。所有輸出、說明、结构化標籤內容都必須是繁體中文。嚴禁使用英文（程式碼除外）。",
    "ja":    "【言語指令】すべての応答を日本語で行ってください。説明・構造化タグの内容もすべて日本語で記述してください（コードを除く）。",
}

INTENT_TRIGGERS = {
    "report": ["report", "報告", "分析", "rapport", "レポート"],
    "design": ["design", "設計", "UI", "介面", "feel", "風格", "デザイン", "感受"],
    "search": ["search", "research", "查找", "研究", "搜尋", "検索", "web search", "搵"],
}

CONDITIONAL_PROMPTS = {
    "report": {
        "en": """
[REPORT GENERATION DETECTED]
When the user asks for a report, analysis, or summary:
1. Ask: Who is the target audience? (developers, management, clients, etc.)
2. Ask: What are the main pain points or concerns to address?
3. Ask: What scope should the report cover? (technical, progress, risks, etc.)
4. Ask: What is the purpose? (decision-making, status update, stakeholder briefing)

If any information is missing, ask the user before generating the report.""",

        "zh-tw": """
【報告生成偵測】
當用戶要求生成報告、分析或摘要時：
1. 詢問：目標讀者是誰？（開發者、管理層、客戶等）
2. 詢問：需要處理的主要痛點或關注點係乜？
3. 詢問：報告應涵蓋邊啲範圍？（技術、進度、風險等）
4. 詢問：用途係乜？（決策參考、狀態更新、利益相關者簡報）

如果資訊不足，請先詢問用戶再生成報告。""",
    },
    "design": {
        "en": """
[DESIGN REQUEST DETECTED]
When the user asks for design, UI, or visual work:
1. Ask: Who are the target users? (age, technical level, preferences)
2. Ask: What feeling or style is desired? (professional, casual, minimal, modern, playful)
3. Ask: What core features or elements must be included?
4. Ask: Any constraints? (platform limitations, brand guidelines, existing style)

If any information is missing, ask the user before designing.""",

        "zh-tw": """
【設計請求偵測】
當用戶要求設計、UI 或視覺工作時：
1. 詢問：目標用戶係邊個？（年齡、技術水平、偏好）
2. 詢問：想要乜嘢感覺或風格？（專業、休閒、極簡、現代、活潑）
3. 詢問：必須包含邊啲核心功能或元素？
4. 詢問：有任何限制嗎？（平台限制、品牌規範、現有風格）

如果資訊不足，請先詢問用戶再設計。""",
    },
    "search": {
        "en": """
[RESEARCH/SEARCH REQUEST DETECTED]
When the user asks to search, research, or find information:
- Use web search tools to find relevant and up-to-date information
- Provide sources and citations for key findings
- Summarize findings in a structured, easy-to-read format
- Offer to explore any interesting topics in more depth
- If the search topic is vague, ask for clarification first""",

        "zh-tw": """
【搜尋/研究請求偵測】
當用戶要求搜尋、研究或查找資訊時：
- 使用網頁搜尋工具尋找相關及最新資訊
- 為主要發現提供來源和引用
- 以結構化、易讀的格式總結發現
- 主動提議深入探索任何有趣的主題
- 如果搜尋主題模糊，先詢問用戶澄清""",
    },
}


def _detect_intent(content: str) -> list[str]:
    content_lower = content.lower()
    detected = []
    for intent, keywords in INTENT_TRIGGERS.items():
        for kw in keywords:
            if kw.lower() in content_lower:
                detected.append(intent)
                break
    return detected


def _get_conditional_prompt(content: str, lang: str) -> str:
    intents = _detect_intent(content)
    if not intents:
        return ""
    parts = []
    for intent in intents:
        intent_prompts = CONDITIONAL_PROMPTS.get(intent, {})
        prompt = intent_prompts.get(lang, intent_prompts.get("en", ""))
        if prompt:
            parts.append(prompt)
    return "\n".join(parts)

_SETTINGS_PATH = ROOT / "core" / "runtime" / "settings.json"
_CHAT_POLICY_PATH = ROOT / "core" / "task_hounds_api" / "agent_prompts" / "chat_prompts.md"


def _chat_policy() -> str:
    try:
        return _CHAT_POLICY_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return "Talk directly with the human. Never claim work without evidence."


def _is_directive_request(content: str) -> bool:
    normalized = " ".join((content or "").lower().split())
    directive_terms = (
        "human directive",
        "human-directive",
        "人類指令",
        "使用者指令",
    )
    action_terms = (
        "create", "draft", "prepare", "provide", "give", "write",
        "建立", "創建", "草擬", "準備", "提供", "給我", "俾我", "寫",
        "需要", "想要",
    )
    return any(term in normalized for term in directive_terms) and any(
        term in normalized for term in action_terms
    )


def _get_language_instruction() -> str:
    try:
        if _SETTINGS_PATH.exists():
            settings = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8-sig"))
            lang = settings.get("language", "en")
            instr = LANG_INSTRUCTIONS.get(lang, f"You MUST respond in {lang}.")
            if settings.get("force_thinking_language"):
                lang_names = {"en": "English", "zh-tw": "繁體中文", "ja": "日本語"}
                lang_name = lang_names.get(lang, lang)
                instr += f" Your internal reasoning and thinking process must also be in {lang_name}."
            return instr
    except Exception:
        pass
    return ""


class ChatAgentError(RuntimeError):
    """Raised when the chat agent cannot run because the project session
    is misconfigured (missing workspace_path, missing session, etc.).

    Surfaced to the API layer as a 4xx so the UI can show a clear error
    instead of silently falling back to the Task Hounds repo root.
    """


def _opencode_port() -> int:
    raw = os.environ.get("TASK_HOUNDS_OPENCODE_PORT", "18765")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 18765


def _chat_agent_name() -> str:
    return os.environ.get("TASK_HOUNDS_CHAT_OPENCODE_AGENT", "general")


def _chat_model() -> str:
    return os.environ.get("TASK_HOUNDS_CHAT_OPENCODE_MODEL") or os.environ.get(
        "TASK_HOUNDS_OPENCODE_MODEL",
        "minimax-coding-plan/MiniMax-M2.7",
    )


def _prompt(
    session_id: str,
    content: str,
    workspace_path: str,
    history: list[dict],
    directive_request: bool = False,
) -> str:
    turns = "\n".join(
        f"{row.get('sender', 'unknown')}: {str(row.get('content', ''))[:1200]}"
        for row in history[-12:]
    ) or "(none)"
    lang_instr = _get_language_instruction()
    lang = "en"
    for char in content:
        if "\u4e00" <= char <= "\u9fff":
            lang = "zh-tw"
            break
    conditional_prompt = _get_conditional_prompt(content, lang)
    directive_contract = ""
    if directive_request:
        directive_contract = (
            "\nThis is a Human Directive request. Return exactly one JSON object "
            "and no markdown fence or surrounding prose. The `reply` tells the "
            "human that a proposal is ready for confirmation. The "
            "`directive_proposal` contains the complete proposed Human Directive. "
            "Do not create a directive file and do not claim it was saved.\n"
            f"OUTPUT SCHEMA:\n{json.dumps(ChatDirectiveOutput.model_json_schema(), ensure_ascii=False)}\n"
        )
    return (
        (f"[LANGUAGE DIRECTIVE — MANDATORY]\n{lang_instr}\n\n" if lang_instr else "")
        + (conditional_prompt + "\n\n" if conditional_prompt else "")
        + "You are the Task Hounds Chat agent. Talk directly with the human about "
        "the currently active project session.\n\n"
        f"=== CHAT POLICY ===\n{_chat_policy()}\n\n"
        f"=== SHARED PROJECT METHOD ===\n{project_methodology()}\n\n"
        f"Project session id: {session_id}\n"
        f"Workspace root: {workspace_path}\n\n"
        f"Recent chat history:\n{turns}\n\n"
        f"Human message:\n{content}\n"
        f"{directive_contract}"
    )


def _stream_path(agent_name: str = "chat", session_id: str | None = None) -> Path:
    safe = "".join(ch for ch in agent_name if ch.isalnum() or ch in ("-", "_")) or "chat"
    if session_id:
        path = ROOT / "core" / "runtime" / "agent_streams" / session_id / f"{safe}.jsonl"
    else:
        path = ROOT / "core" / "runtime" / "agent_streams" / f"{safe}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _append_stream_event(event: dict, session_id: str | None = None) -> None:
    path = _stream_path("chat", session_id)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    now = datetime.now(timezone.utc).isoformat()
    step = {
        "text": "streaming text",
        "think": "thinking",
        "tool": f"using {event.get('name') or 'tool'}",
        "step_end": "step finished",
        "sys": str(event.get("msg") or "chat runtime"),
        "error": "error",
    }.get(str(event.get("t") or ""), "streaming output")
    db_agent.update_agent(
        "chat",
        last_stream_at=now,
        last_seen=now,
        current_step=step,
        step_source="chat",
    )


def _stream_event_from_opencode(raw: str) -> dict | None:
    try:
        ev = json.loads(raw)
    except json.JSONDecodeError:
        return None
    etype = ev.get("type")
    part = ev.get("part") or {}
    ts = ev.get("timestamp")
    ts_s = (float(ts) / 1000.0) if isinstance(ts, (int, float)) and ts > 10_000_000_000 else ts
    if etype == "text":
        text = str(part.get("text") or "")
        return {"t": "text", "text": text, "ts": ts_s} if text else None
    if etype == "reasoning":
        text = str(part.get("text") or "")
        return {"t": "think", "text": text, "ts": ts_s} if text else None
    if etype == "tool_use":
        state = part.get("state") or {}
        return {
            "t": "tool",
            "name": str(part.get("tool") or "tool"),
            "status": str(state.get("status") or "completed"),
            "input": state.get("input") if isinstance(state.get("input"), dict) else {},
            "output": str(state.get("output") or ""),
            "error": str(state.get("error") or ""),
            "ts": ts_s,
        }
    if etype == "step_finish":
        tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
        return {
            "t": "step_end",
            "reason": str(part.get("reason") or ""),
            "tokens": tokens,
            "cost": float(part.get("cost") or 0),
            "ts": ts_s,
        }
    if etype == "error":
        return {"t": "error", "msg": str(ev.get("error") or ev), "ts": ts_s}
    return None


def send(session_id: str, content: str, sender: str = "human") -> dict:
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "empty_message", "messages": db_chat.list_chat(session_id)}

    active = db_project.get_session(session_id) or db_project.get_active_session() or {}
    workspace_path = active.get("workspace_path")
    if not workspace_path:
        # Refuse to fall back to ROOT (Task Hounds repo) — that would let the
        # chat agent edit its own code. The UI must surface this clearly so
        # the user knows the project session has no workspace configured.
        raise ChatAgentError(
            f"project session {session_id!r} has no workspace_path; "
            "create or pick a project before sending chat messages"
        )
    workspace = Path(workspace_path)
    if not workspace.is_dir():
        raise ChatAgentError(
            f"project session {session_id!r} workspace_path does not exist or is not a directory: {workspace}"
        )

    db_chat.append_chat(session_id, content, sender=sender)
    _append_stream_event({"t": "text", "text": f"You: {content}", "ts": None}, session_id)
    history = db_chat.list_chat(session_id, limit=30)
    chat_session_id = f"{session_id}:chat"

    _append_stream_event({"t": "sys", "kind": "info", "msg": "chat run started"}, session_id)
    db_agent.update_agent("chat", last_error=None)
    set_agent_state("chat", "busy", "responding", project_session_id=session_id, role_session_id=chat_session_id)
    try:
        host, port, agent, model = resolve_for_role("chat")
        def on_chunk(raw: str) -> None:
            stream_event = _stream_event_from_opencode(raw)
            if stream_event:
                _append_stream_event(stream_event, session_id)

        directive_request = _is_directive_request(content)
        result = oc_client.run(
            agent=agent,
            model=model,
            prompt=_prompt(
                session_id,
                content,
                str(workspace),
                history,
                directive_request=directive_request,
            ),
            host=host,
            port=port,
            session_id=None,
            timeout=900,
            stall_timeout=300,
            retry_stalled=True,
            cwd=workspace,
            on_chunk=on_chunk,
            project_session_id=session_id,
            role="chat",
            purpose="interactive_chat",
        )
        if not result.get("ok"):
            message = result.get("error", {}).get("message", "chat runtime unavailable")
            db_agent.update_agent("chat", state="error", last_error=message)
            return {"ok": False, "error": message, "messages": db_chat.list_chat(session_id)}

        raw_reply = (result.get("output") or {}).get("text", "").strip()
        directive_proposal = None
        reply = raw_reply
        if directive_request:
            try:
                payload = extract_json_object(
                    raw_reply,
                    required_keys={"reply", "directive_proposal"},
                )
                parsed = ChatDirectiveOutput.model_validate(payload)
                reply = parsed.reply
                directive_proposal = parsed.directive_proposal
            except Exception:
                reply = (
                    "I could not produce a valid Human Directive proposal. "
                    "Please try again; no directive was saved."
                )
        if not reply:
            reply = "(Chat agent returned an empty response.)"
        db_chat.append_chat(
            session_id,
            reply,
            sender="chat",
            directive_proposal=directive_proposal,
        )
        db_agent.update_agent("chat", last_error=None)
        _append_stream_event({"t": "sys", "kind": "info", "msg": "chat run completed"}, session_id)
        return {"ok": True, "reply": reply, "messages": db_chat.list_chat(session_id)}
    finally:
        set_agent_state("chat", "idle", project_session_id=session_id, role_session_id=chat_session_id)
