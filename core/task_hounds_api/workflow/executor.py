"""workflow.executor — Manager/Worker/Reviewer role executors.

Each executor:
  1. Reads fresh state from DB
  2. Builds a prompt
  3. Calls opencode.client.run(...)
  4. Writes its result back to DB

No state lives in memory between steps. The DB is the only whiteboard.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from task_hounds_api.db import ROOT
from task_hounds_api.db.ops import workflow as db_wf
from task_hounds_api.db.ops import todo as db_todo
from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.db.ops import execution as db_execution
from task_hounds_api.opencode import client as oc_client
from task_hounds_api.opencode.binding_resolver import resolve_for_role
from task_hounds_api.workflow import models as M
from task_hounds_api.workflow.prompt_policy import with_project_methodology
from task_hounds_api.workflow import repair as wf_repair
from task_hounds_api.workflow.output_contracts import (
    ManagerOutput,
    ReviewerOutput,
    WorkerOutput,
    issue_text,
)


# ── Language / settings helpers ───────────────────────────────────────────────

LANG_INSTRUCTIONS = {
    "en":    "You MUST respond entirely in English. All output, explanations, and structured tags must be in English.",
    "zh-tw": "【語言指令】你必須完全使用繁體中文回應。所有輸出、說明、結構化標籤內容都必須是繁體中文。嚴禁使用英文（程式碼除外）。",
    "ja":    "【言語指令】すべての応答を日本語で行ってください。説明・構造化タグの内容もすべて日本語で記述してください（コードを除く）。",
}

_SETTINGS_PATH = ROOT / "core" / "runtime" / "settings.json"


def get_language_instruction() -> str:
    """Read language setting and return the appropriate language instruction for prompts."""
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


def _lang_directive() -> str:
    """Return the language directive string to prepend to prompts, or empty string if none."""
    instr = get_language_instruction()
    if not instr:
        return ""
    lang_instr = f"[LANGUAGE DIRECTIVE — MANDATORY]\n{instr}\n"
    return lang_instr


def _stream_path(agent_name: str, session_id: str | None = None) -> Path:
    safe = "".join(ch for ch in agent_name if ch.isalnum() or ch in ("-", "_")) or agent_name
    if session_id:
        path = ROOT / "core" / "runtime" / "agent_streams" / session_id / f"{safe}.jsonl"
    else:
        path = ROOT / "core" / "runtime" / "agent_streams" / f"{safe}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _append_agent_stream_event(event: dict, agent_name: str, session_id: str | None = None) -> None:
    path = _stream_path(agent_name, session_id)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def _make_stream_callback(agent_name: str, session_id: str | None) -> Callable[[str], None]:
    def on_chunk(raw: str) -> None:
        event = _parse_opencode_event(raw)
        if event:
            _append_agent_stream_event(event, agent_name, session_id)
    return on_chunk


def _parse_opencode_event(raw: str) -> dict | None:
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


class WorkspacePathError(ValueError):
    """Raised when a flow input has no usable workspace_path.

    Surfaces to the API layer as a 4xx so the caller (UI) can prompt
    the user to pick a project before the loop tries to run. This is
    the same contract as chat_agent.ChatAgentError but for the
    Manager/Worker/Reviewer pipeline.
    """


def resolve_workspace(fi: M.FlowInput, role: str) -> Path:
    """Return the workspace Path for a role, refusing the silent ROOT fallback.

    Pre-fix: every role call site had `fi.workspace_path or ROOT` which
    let an empty workspace_path silently run the agent against the
    Task Hounds repo itself (the WORST case of a misconfigured project).
    Post-fix: explicit error so the API layer returns 4xx and the UI
    can prompt the user to pick a project.
    """
    if not fi.workspace_path or not fi.workspace_path.strip():
        raise WorkspacePathError(
            f"{role} requires a project workspace_path; "
            f"project session {fi.project_session_id!r} has none configured"
        )
    return Path(fi.workspace_path)


# ── Prompt loading (all prompts are .md files, no in-code strings) ─────────

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "agent_prompts"

# Map role -> filename
_PROMPT_FILES = {
    "manager_digest": "manager_prompts.md",     # section "## Prompt 1" (digest)
    "manager_todo": "manager_step_prompts.md",  # section "## Prompt 1" (todo)
    "manager_select": "manager_v2_prompts.md",  # selection prompt
    "manager_release": "manager_v2_prompts.md",  # release prompt
    "worker": "worker_prompts.md",
    "reviewer": "reviewer_prompts.md",
    "system": "system_principles.md",
}


def _load_prompt(role: str) -> str:
    fname = _PROMPT_FILES[role]
    path = _PROMPTS_DIR / fname
    if not path.exists():
        return ""
    return with_project_methodology(path.read_text(encoding="utf-8"))


# ── JSON extraction ──────────────────────────────────────────────────────────

def extract_json_object(text: str, required_keys: set[str]) -> dict:
    """Find the JSON object in `text` that best satisfies `required_keys`.

    Agent output frequently contains several JSON blocks — todo updates,
    <HANDOFF_UPDATE> payloads, schema echoes — so taking only the *first*
    fenced block used to grab the wrong object and fail with
    "missing required keys" even though the contract JSON was present
    later in the message. Score every fenced block (plus the outermost
    brace span as a fallback) by required-key coverage and return the
    best match.
    """
    candidates: list[str] = [
        m.group(1)
        for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    ]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    if not candidates:
        raise ValueError("no JSON object found")
    best: dict | None = None
    best_coverage = -1
    parse_error: Exception | None = None
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError as e:
            parse_error = e
            continue
        if not isinstance(obj, dict):
            continue
        coverage = len(required_keys & set(obj.keys()))
        if coverage > best_coverage:
            best = obj
            best_coverage = coverage
            if not (required_keys - set(obj.keys())):
                break
    if best is None:
        raise ValueError(f"invalid JSON: {parse_error}")
    missing = required_keys - set(best.keys())
    if missing:
        raise ValueError(f"missing required keys: {sorted(missing)}")
    return best


def stringify_field(value) -> str:
    """Pull a readable string out of a manager's JSON field."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(x) for x in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _validate_contract(text: str, contract):
    required = {name for name, field in contract.model_fields.items() if field.is_required()}
    payload = extract_json_object(text, required_keys=required)
    return contract.model_validate(payload)


def _format_repair_prompt(contract, invalid_text: str, error: Exception) -> str:
    schema = json.dumps(contract.model_json_schema(), ensure_ascii=False)
    return (
        _lang_directive()
        + "Reformat the previous response only. Do not execute tools, edit files, or add commentary. "
        "Return exactly one JSON object matching this JSON Schema. Unknown keys are forbidden.\n"
        f"SCHEMA:\n{schema}\n\n"
        f"VALIDATION ERROR:\n{str(error)[:3000]}\n\n"
        f"PREVIOUS RESPONSE:\n{invalid_text[:12000]}"
    )


def _manager_contract_fallback(state: M.FlowState, text: str, error: Exception) -> dict:
    """Return a safe ManagerOutput payload when the Manager never emits JSON.

    Worker and Reviewer contract failures are represented in the graph
    state instead of crashing the worker. Manager needs the same behavior:
    a malformed model response should pause for human attention, not
    surface as a generic Background Server "GraphFlow failed" error.
    """
    ctx = state.existing_context or {}
    current_plan = stringify_field(ctx.get("plan")) or state.plan
    current_handoff = ctx.get("handoff_update") if isinstance(ctx.get("handoff_update"), dict) else {}
    current_task = (
        state.suggestion_content
        or stringify_field(current_handoff.get("current_task"))
        or state.flow_input.human_suggested_new_task_or_item.strip()
        or "Review the latest Manager output and decide the next safe task."
    )
    message = (
        "Manager response did not match the structured JSON contract after retry. "
        "GraphFlow paused for human attention instead of continuing with ambiguous instructions."
    )
    evidence = [
        f"contract_error: {str(error)[:1000]}",
    ]
    if text.strip():
        evidence.append("manager_output_excerpt: " + text.strip()[:1000])
    return {
        "input_digest": state.input_digest or state.flow_input.human_directive.strip() or "Manager contract fallback",
        "decision": {
            "action": "request_human",
            "summary": "Manager structured output was invalid after retry.",
            "rationale": message,
            "evidence": evidence,
        },
        "manager_message": message,
        "plan": current_plan or "Pause and inspect the Manager output contract failure before continuing.",
        "todo_list": [
            {
                "content": current_task,
                "status": "in_progress",
                "priority": "high",
                "owner": "manager",
            }
        ],
        "suggestion_content": "",
        "suggestion_verification": "Human review required because Manager returned no valid JSON contract.",
        "handoff_update": {
            **current_handoff,
            "current_task": current_task,
            "working_direction": message,
            "human_concerns": "Manager format_contract_error after retry.",
        },
    }


def _parse_worker_metadata(text: str) -> dict:
    """Best-effort parse of the Worker's report JSON.

    Workers often return a fenced JSON object with files_changed,
    test_result, and known_issues. Relying only on git status misses
    changes in disposable non-git workspaces used by external E2E tests.
    """
    try:
        obj = extract_json_object(
            text,
            required_keys={"files_changed", "test_result", "known_issues"},
        )
    except ValueError:
        return {"files_changed": [], "test_result": "", "known_issues": []}
    files = obj.get("files_changed")
    if not isinstance(files, list):
        files = []
    known = obj.get("known_issues")
    if not isinstance(known, list):
        known = [known] if known else []
    return {
        "files_changed": [str(item) for item in files if str(item).strip()],
        "test_result": stringify_field(obj.get("test_result")),
        "known_issues": [stringify_field(item) for item in known if stringify_field(item)],
    }


def _compact_text(value: str, limit: int = 800) -> str:
    text = stringify_field(value).replace("\r\n", "\n").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _worker_context_summary(worker_rep: dict | None) -> str:
    if not worker_rep:
        return ""
    report = stringify_field(worker_rep.get("report", ""))
    meta = _parse_worker_metadata(report) if report else {}
    files_changed = worker_rep.get("files_changed") or meta.get("files_changed") or []
    test_result = worker_rep.get("test_result") or meta.get("test_result") or ""
    known_issues = worker_rep.get("known_issues") or meta.get("known_issues") or []
    lines = [
        f"files_changed={files_changed}",
        f"test_result={test_result or '(none)'}",
        f"known_issues={known_issues}",
    ]
    if not meta.get("files_changed") and report:
        lines.extend(["report_excerpt:", _compact_text(report, 300) or "(none)"])
    return "\n".join(lines)


def _opencode_port() -> int:
    raw = os.environ.get("TASK_HOUNDS_OPENCODE_PORT", "18765")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 18765


def _manager_agent_name() -> str:
    return os.environ.get("TASK_HOUNDS_MANAGER_OPENCODE_AGENT", "general")


def _worker_agent_name() -> str:
    return os.environ.get("TASK_HOUNDS_WORKER_OPENCODE_AGENT", "general")


def _reviewer_agent_name() -> str:
    return os.environ.get("TASK_HOUNDS_REVIEWER_OPENCODE_AGENT", "general")


def _opencode_model(role: str) -> str:
    role_key = f"TASK_HOUNDS_{role.upper()}_OPENCODE_MODEL"
    return os.environ.get(role_key) or os.environ.get(
        "TASK_HOUNDS_OPENCODE_MODEL",
        "minimax-coding-plan/MiniMax-M2.7",
    )


def _blocking_credential_warnings_for_role(role: str) -> list[str]:
    """Only block on local credentials when no reachable external binding exists."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    rm = RuntimeManager.instance()
    try:
        host, port, _agent, model = resolve_for_role(role)
    except Exception:
        # A transient server/binding resolution failure must not widen
        # credential validation to every provider in opencode.jsonc.
        # The role's configured model remains the source of truth.
        try:
            from task_hounds_api.db.ops import runtime as db_rt

            binding = db_rt.get_binding(role) or {}
            model = str(binding.get("model") or _opencode_model(role))
        except Exception:
            model = _opencode_model(role)
        provider_ids = {model.split("/", 1)[0]} if "/" in model else None
        return rm.validate_credentials(provider_ids=provider_ids)

    provider_ids = None
    if model and "/" in model:
        provider_ids = {model.split("/", 1)[0]}
    warnings = rm.validate_credentials(provider_ids=provider_ids)
    if not warnings:
        return []

    if not rm.test_server(host, port).get("reachable", False):
        return warnings

    for server in rm.list_servers():
        if (
            server.get("host") == host
            and int(server.get("port", 0)) == int(port)
            and server.get("owner") == "external"
            and server.get("status") != "ignored"
        ):
            return []
    return warnings


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _ensure_role_session(
    flow_input: M.FlowInput,
    role: str,
    host: str,
    port: int,
    workspace: Path,
) -> str:
    attr = f"{role}_opencode_session_id"
    session_id = oc_client.precreate_session(host, port, cwd=workspace)
    if not session_id:
        raise RuntimeError(f"Could not create OpenCode session for {role}")
    db_execution.bind_opencode_session(flow_input.project_session_id, role, session_id)
    setattr(flow_input, attr, session_id)
    return session_id


def _todo_status(value) -> str:
    normalized = str(value or "pending").strip().lower().replace("-", "_")
    return normalized if normalized in {"pending", "in_progress", "completed"} else "pending"


def _todo_priority(value) -> str:
    normalized = str(value or "medium").strip().lower()
    return normalized if normalized in {"high", "medium", "low"} else "medium"


def _normalize_manager_todos(
    value,
    session_id: str,
    fallback: str,
    reopen_todo_ids: set[str] | None = None,
) -> list[dict]:
    raw_items = value if isinstance(value, list) else []
    if not raw_items and fallback:
        raw_items = [{"content": fallback}]
    existing_rows = db_todo.list_active_todos(session_id)
    existing_by_id = {str(row.get("id")): row for row in existing_rows}
    existing_by_content = {str(row.get("content")): row for row in existing_rows}

    todos = []
    allowed_reopens = reopen_todo_ids or set()
    for pos, item in enumerate(raw_items):
        if isinstance(item, dict):
            content = stringify_field(item.get("content") or item.get("task") or item.get("title"))
            status = _todo_status(item.get("status"))
            priority = _todo_priority(item.get("priority"))
            owner = stringify_field(item.get("owner")) or "manager"
            item_id = stringify_field(item.get("id")) or f"{session_id}-todo-{pos}"
        else:
            content = stringify_field(item)
            status = "pending"
            priority = "medium"
            owner = "manager"
            item_id = f"{session_id}-todo-{pos}"

        if not content:
            continue
        previous = existing_by_id.get(item_id) or existing_by_content.get(content) or {}
        if (
            previous.get("status") == "completed"
            and status != "completed"
            and item_id not in allowed_reopens
        ):
            status = "completed"
        todos.append({
            "id": item_id,
            "session_id": session_id,
            "parent_id": None,
            "content": content,
            "status": status,
            "worker_task_status": previous.get("worker_task_status", "pending"),
            "reviewer_task_status": previous.get("reviewer_task_status", "pending"),
            "attempt_count": int(previous.get("attempt_count", 0) or 0),
            "worker_timeout_count": int(previous.get("worker_timeout_count", 0) or 0),
            "human_attention_status": previous.get("human_attention_status", "none"),
            "priority": priority,
            "position": pos,
            "owner": owner,
        })
    return todos


_MANAGER_FORMAT_INSTRUCTIONS = (
    "You are the Manager agent in Task Hounds. Plan the next concrete unit of work.\n"
    "You are the only role allowed to decide todo_list.status. Treat Worker and Reviewer outputs as evidence. "
    "After a failed, skipped, or needs-review attempt, either mark the todo completed with justification, "
    "keep it in_progress and choose a materially different retry strategy, or split/rewrite it. "
    "Never mark a todo blocked merely because one attempt had a problem.\n"
    "Completed todos are persistent. Never change a completed todo back to pending or in_progress "
    "unless new evidence proves it incomplete; in that case include it in reopen_todos with "
    "todo_id, a non-empty reason, and at least one evidence item.\n"
    "If you emit a stop_signal, manager_message must clearly explain why the loop should stop "
    "and what, if anything, remains unfinished. Never emit an unexplained stop signal.\n"
    "Return exactly one JSON object, with no prose outside JSON.\n\n"
    "Required JSON keys:\n"
    "- input_digest: concise summary of directive and current project state\n"
    "- decision: object describing why this task is next\n"
    "- manager_message: short user-facing manager update\n"
    "- plan: implementation plan text\n"
    "- todo_list: array of todo objects with content/status/priority/owner\n"
    "- suggestion_content: one concrete task for Worker to execute now\n"
    "- suggestion_verification: acceptance check for Worker/Reviewer\n"
    "- handoff_update: object with current_task, working_direction, completion_criteria\n"
)

_FORCE_PLANNING_INSTRUCTION = (
    "IMPORTANT: You MUST produce a detailed plan before any other output. "
    "Set plan: before anything else in your response.\n"
)

_FORCE_TODO_INSTRUCTION = (
    "IMPORTANT: You MUST produce at least one todo item in todo_list. "
    "Never leave todo_list empty unless the directive is fully complete.\n"
)

_MANAGER_JSON_CONTRACT = (
    "JSON-only contract:\n"
    "- First character must be `{`; last character must be `}`.\n"
    "- Return exactly one JSON object. No markdown fences, prose, XML, comments, or schema echo.\n"
    "- Unknown keys are forbidden.\n"
    "- decision.action must be one of: execute, retry, split, complete, request_human, stop.\n"
    "- todo.status must be one of: pending, in_progress, completed.\n"
    "- todo.priority must be one of: high, medium, low.\n"
    "- stop_signal may be null, TASK_HOUNDS_STOP_LOOP, or DIRECTIVE_COMPLETE.\n"
    "- Required top-level keys: input_digest, decision, manager_message, plan, todo_list, "
    "suggestion_content, suggestion_verification, handoff_update.\n"
    "- Optional top-level keys: archive_updates, reopen_todos, stop_signal.\n"
    "Minimal shape:\n"
    "{\"input_digest\":\"...\",\"decision\":{\"action\":\"execute\",\"summary\":\"...\","
    "\"rationale\":\"...\",\"evidence\":[]},\"manager_message\":\"...\",\"plan\":\"...\","
    "\"todo_list\":[{\"content\":\"...\",\"status\":\"pending\",\"priority\":\"high\","
    "\"owner\":\"manager\"}],\"suggestion_content\":\"...\","
    "\"suggestion_verification\":\"...\",\"handoff_update\":{\"current_task\":\"...\","
    "\"working_direction\":\"...\",\"completion_criteria\":[]},\"archive_updates\":[],"
    "\"reopen_todos\":[],\"stop_signal\":null}\n"
)


def _manager_settings_path() -> Path:
    return ROOT / "core" / "runtime" / "settings.json"


def _build_manager_instructions() -> str:
    """Return the manager format instructions string.

    Migration audit symbol 121: this function was ported from the old
    _build_manager_instructions (manager.py:154-162). It returns the format
    instructions and conditionally prepends force_planning / force_todo
    prefixes based on settings.

    The old code read settings.get('force_planning') and
    settings.get('force_todo') and appended
    _FORCE_PLANNING_INSTRUCTION / _FORCE_TODO_INSTRUCTION to the prompt.
    This restores that settings-driven behavior.
    """
    instructions = _MANAGER_FORMAT_INSTRUCTIONS
    try:
        settings_path = _manager_settings_path()
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
            if settings.get("force_planning"):
                instructions = _FORCE_PLANNING_INSTRUCTION + instructions
            if settings.get("force_todo"):
                instructions = _FORCE_TODO_INSTRUCTION + instructions
    except Exception:
        pass
    return instructions


def _manager_prompt(state: M.FlowState) -> str:
    fi = state.flow_input
    ctx = state.existing_context or {}
    manager_history = "\n".join(
        f"- {stringify_field(msg)[:500]}" for msg in _as_list(ctx.get("manager_messages"))[:5]
    ) or "(none)"
    current_todos = state.todo_list or db_todo.list_active_todos(fi.project_session_id)
    archived_todos = db_todo.list_archived_todos(fi.project_session_id, limit=20)
    existing_todos = "\n".join(
        "- "
        + json.dumps(
            {
                "id": item.get("id"),
                "content": item.get("content"),
                "status": item.get("status"),
                "worker_task_status": item.get("worker_task_status", "pending"),
                "reviewer_task_status": item.get("reviewer_task_status", "pending"),
                "attempt_count": item.get("attempt_count", 0),
                "worker_timeout_count": item.get("worker_timeout_count", 0),
                "human_attention_status": item.get("human_attention_status", "none"),
            },
            ensure_ascii=False,
        )
        for item in current_todos
    ) or "(none)"
    worker_summary = _worker_context_summary({
        "report": state.worker_report or ctx.get("worker_report", ""),
        "files_changed": state.worker_files_changed or ctx.get("files_changed", []),
        "test_result": state.worker_test_result or ctx.get("test_result", ""),
        "known_issues": state.worker_known_issues or ctx.get("known_issues", []),
    })
    prompt_hint = _load_prompt("manager_select")
    workspace = resolve_workspace(fi, "manager")

    return (
        _lang_directive()
        + _build_manager_instructions() + "\n"
        f"Directive:\n{fi.human_directive.strip()}\n\n"
        f"Workspace path (all file work must stay inside this directory):\n{workspace}\n\n"
        f"Human thought:\n{fi.human_new_thought_and_suggestion.strip() or '(none)'}\n\n"
        f"Human suggested task:\n{fi.human_suggested_new_task_or_item.strip() or '(none)'}\n\n"
        f"Input digest from local context:\n{state.input_digest.strip() or '(none)'}\n\n"
        f"Existing plan:\n{_compact_text(ctx.get('plan', ''), 1200) or '(none)'}\n\n"
        f"Existing todos:\n{existing_todos}\n\n"
        "Recent archived/outdated todos (history only; never count these toward completion):\n"
        + (
            "\n".join(
                f"- {item.get('content')} | reason={item.get('archive_reason')} | "
                f"note={item.get('archive_note')}"
                for item in archived_todos
            )
            or "(none)"
        )
        + "\n\n"
        f"Last worker summary:\n{worker_summary or '(none)'}\n\n"
        f"Reviewer feedback:\n{state.reviewer_feedback or state.loop_input.reviewer_feedback or '(none)'}\n\n"
        f"Reviewer outcome:\n{state.reviewer_qa_result or '(none)'}\n\n"
        f"Recent manager messages:\n{manager_history}\n\n"
        f"Manager prompt reference:\n{prompt_hint[:4000] if prompt_hint else '(none)'}\n\n"
        f"{_MANAGER_JSON_CONTRACT}\n"
    )


def _call_manager(state: M.FlowState) -> dict:
    cred_warnings = _blocking_credential_warnings_for_role("manager")
    if cred_warnings:
        raise RuntimeError(
            "Cannot call Manager — missing API credentials. "
            + " | ".join(cred_warnings)
        )

    sid = state.flow_input.project_session_id
    host, port, agent, model = resolve_for_role("manager")
    manager_workspace = resolve_workspace(state.flow_input, "manager")
    manager_session_id = _ensure_role_session(
        state.flow_input, "manager", host, port, manager_workspace
    )
    result = oc_client.run(
        agent=agent,
        model=model,
        prompt=_manager_prompt(state),
        host=host,
        port=port,
        timeout=900,
        cwd=manager_workspace,
        on_chunk=_make_stream_callback("manager", sid),
        workflow_run_id=state.flow_input.run_id,
        project_session_id=sid,
        role="manager",
        session_id=manager_session_id,
        execution_id=db_execution.execution_id(sid, state.flow_input.run_id, "manager"),
    )
    if not result.get("ok"):
        message = result.get("error", {}).get("message", "manager OpenCode call failed")
        raise RuntimeError(f"Manager OpenCode call failed: {message}")

    text = result.get("output", {}).get("text", "")
    try:
        validated = _validate_contract(text, ManagerOutput)
    except (ValueError, ValidationError) as first_error:
        repair_result = oc_client.run(
            agent=agent,
            model=model,
            prompt=_format_repair_prompt(ManagerOutput, text, first_error),
            host=host,
            port=port,
            timeout=180,
            cwd=manager_workspace,
            on_chunk=_make_stream_callback("manager", sid),
            workflow_run_id=state.flow_input.run_id,
            project_session_id=sid,
            role="manager",
            session_id=_ensure_role_session(
                state.flow_input, "manager", host, port, manager_workspace
            ),
            execution_id=db_execution.execution_id(sid, state.flow_input.run_id, "manager"),
        )
        if not repair_result.get("ok"):
            raise RuntimeError(f"manager format_contract_error: {first_error}") from first_error
        text = repair_result.get("output", {}).get("text", "")
        try:
            validated = _validate_contract(text, ManagerOutput)
        except (ValueError, ValidationError) as second_error:
            payload = _manager_contract_fallback(state, text, second_error)
            return {"text": text, "payload": payload}
    payload = validated.model_dump(mode="json", exclude_none=True)
    # Return both the raw text and the parsed payload so callers
    # (manager_plan) can also extract prompt tokens that the LLM emits
    # OUTSIDE the JSON (e.g. <HANDOFF_UPDATE>...</HANDOFF_UPDATE>).
    return {"text": text, "payload": payload}


# ── Existing-context loader ─────────────────────────────────────────────────

def load_existing_context(session_id: str) -> dict:
    """Read the latest plan, handoff, manager messages, worker report, reviewer feedback
    from DB. This is what the Manager uses to estimate current progress."""
    plan = db_wf.get_plan(session_id)
    handoff = db_wf.get_handoff(session_id)
    manager_msgs = db_wf.list_manager_messages(session_id, limit=5)
    worker_rep = db_wf.latest_worker_report(session_id)
    suggestion = db_wf.get_active_suggestion(session_id)
    worker_summary = _worker_context_summary(worker_rep)
    return {
        "plan": plan.get("content", "") if plan else "",
        "plan_updated_at": plan.get("updated_at", "") if plan else "",
        "handoff_update": handoff if handoff else {},
        "manager_messages": [m.get("content", "") for m in manager_msgs],
        "worker_report": worker_rep.get("report", "") if worker_rep else "",
        "worker_report_summary": worker_summary,
        "test_result": worker_rep.get("test_result", "") if worker_rep else "",
        "files_changed": worker_rep.get("files_changed", []) if worker_rep else [],
        "known_issues": worker_rep.get("known_issues", []) if worker_rep else [],
        "active_suggestion": suggestion.get("content", "") if suggestion else "",
    }


# ── State <-> DB loaders ─────────────────────────────────────────────────────

def set_agent_state_safe(
    role: str,
    state: str,
    current_step: str | None = None,
    *,
    project_session_id: str | None = None,
    role_session_id: str | None = None,
    workflow_run_id: int | None = None,
) -> None:
    """Set agent state in DB. Silent if agent doesn't exist (for tests/offline).

    Forwards the project_session_id and role_session_id through to
    set_agent_state so the registry row can be scoped to a specific
    project and a specific role-scoped OpenCode session.
    """
    try:
        from task_hounds_api.workflow.signals import set_agent_state
        set_agent_state(
            role, state, current_step,
            project_session_id=project_session_id,
            role_session_id=role_session_id,
            workflow_run_id=workflow_run_id,
        )
    except Exception:
        pass


def checkpoint(state: M.FlowState, step_name: str) -> None:
    """Persist a checkpoint row for the current step (tA2a).

    Bumps state.step_index and state.step_name, then writes to the
    flow_checkpoints table. Silently no-ops if state.flow_input.run_id
    is None (caller didn't create a workflow_run; that should only
    happen in legacy tests that bypass run_loop()).
    """
    state.step_index += 1
    state.step_name = step_name
    if state.flow_input.run_id is None:
        return
    try:
        import json as _json
        db_wf.save_checkpoint(
            run_id=state.flow_input.run_id,
            session_id=state.flow_input.project_session_id,
            power_team_project_id=state.flow_input.power_team_project_id,
            step_name=step_name,
            step_index=state.step_index,
            state_json=_json.dumps(
                {k: getattr(state, k) for k in (
                    "status", "input_digest", "decision", "manager_message",
                    "plan", "todo_list", "todo_update_json", "suggestion_content",
                    "suggestion_verification", "handoff_update",
                    "worker_report", "worker_files_changed", "worker_test_result",
                    "worker_known_issues", "reviewer_feedback", "reviewer_qa_result",
                    "reviewer_bugs", "reviewer_uiux", "reviewer_possible_problems",
                    "reviewer_safety_security_risks", "suggestion_id", "current_todo_id",
                    "archive_updates", "reopen_todos",
                    "step_name", "step_index",
                )},
                default=str,
            ),
        )
    except Exception:
        # Checkpoint write is best-effort; never break the graph on a
        # checkpoint failure (the audit's tA2x 'no silent fail' is about
        # user-visible errors, not internal durability).
        pass


def state_from_db(flow_input: M.FlowInput, loop_input: M.FlowLoopInput) -> M.FlowState:
    """Read all relevant DB rows into a fresh FlowState. Always re-reads from DB."""
    session_id = flow_input.project_session_id
    ctx = load_existing_context(session_id)
    state = M.FlowState(flow_input=flow_input, loop_input=loop_input, existing_context=ctx)
    # Pre-populate from DB so the manager can see existing progress
    state.plan = ctx.get("plan", "")
    state.manager_message = ctx.get("manager_messages", [""])[0] if ctx.get("manager_messages") else ""
    state.handoff_update = ctx.get("handoff_update", {})
    state.worker_report = ctx.get("worker_report", "")
    state.worker_test_result = ctx.get("test_result", "")
    state.worker_files_changed = ctx.get("files_changed", [])
    state.worker_known_issues = ctx.get("known_issues", [])
    return state


# ── Manager step: digest ────────────────────────────────────────────────────

def manager_digest(state: M.FlowState) -> M.FlowState:
    """Read the directive, existing context, and form an input_digest.

    Writes the digest back to manager_messages (so the UI can see it).
    """
    fi = state.flow_input
    has_existing = bool(state.existing_context.get("plan")) or bool(state.existing_context.get("worker_report"))

    if has_existing:
        # Estimate progress
        ctx = state.existing_context
        progress_parts = [
            f"Directive: {fi.human_directive.strip()}",
            f"Existing plan: {_compact_text(ctx.get('plan', '(none)'), 300)}",
            f"Last worker summary: {_compact_text(ctx.get('worker_report_summary') or ctx.get('worker_report', '(none)'), 500)}",
            f"Test result: {ctx.get('test_result', '(none)')}",
            f"Known issues: {ctx.get('known_issues', [])}",
        ]
        state.input_digest = "[ESTIMATING PROGRESS]\n" + "\n".join(progress_parts)
    else:
        # Fresh start — focus on directive
        state.input_digest = (
            f"[FRESH START — NO EXISTING STATE]\n"
            f"Directive: {fi.human_directive.strip()}\n"
            f"Human thought: {fi.human_new_thought_and_suggestion.strip() or '(none)'}\n"
            f"Suggested task: {fi.human_suggested_new_task_or_item.strip() or '(none)'}\n"
            f"Existing todos: {fi.todo_items or '(none)'}"
        )
    db_wf.append_manager_message(fi.project_session_id, state.input_digest)
    return state


# ── Manager step: plan ──────────────────────────────────────────────────────

def manager_plan(state: M.FlowState, *, on_missing: Callable[[], None] | None = None) -> M.FlowState:
    """Ask the Manager LLM to form the plan and next Worker task.

    Recognizes two stop signals from the manager JSON response:
      stop_signal == "TASK_HOUNDS_STOP_LOOP" -> state.status = "completed"
        (manager decided the loop has no more useful work; skip Worker)
      stop_signal == "DIRECTIVE_COMPLETE"    -> state.status = "completed"
        (user explicitly told the manager to stop; this is the
         <DIRECTIVE_COMPLETE/> mention in the manager prompt)

    Either signal short-circuits the graph to END without running
    worker_execute or reviewer_check. The graph router checks
    state.status in _route_after_manager_release and routes to END.
    """
    fi = state.flow_input
    call_result = _call_manager(state)
    text = call_result.get("text", "")
    payload = call_result.get("payload", {})

    state.input_digest = stringify_field(payload.get("input_digest")) or state.input_digest
    decision = payload.get("decision") or {}
    state.decision = decision if isinstance(decision, dict) else {"summary": stringify_field(decision)}
    state.manager_message = stringify_field(payload.get("manager_message"))
    state.plan = stringify_field(payload.get("plan"))
    state.reopen_todos = payload.get("reopen_todos") or []
    reopen_ids = {
        str(item.get("todo_id"))
        for item in state.reopen_todos
        if isinstance(item, dict) and item.get("todo_id")
    }
    state.todo_list = _normalize_manager_todos(
        payload.get("todo_list"),
        fi.project_session_id,
        stringify_field(payload.get("suggestion_content")),
        reopen_ids,
    )
    state.todo_update_json = {"items": state.todo_list}
    state.suggestion_content = stringify_field(payload.get("suggestion_content"))
    state.suggestion_verification = stringify_field(payload.get("suggestion_verification"))
    state.archive_updates = payload.get("archive_updates") or []
    handoff = payload.get("handoff_update") or {}
    state.handoff_update = handoff if isinstance(handoff, dict) else {"working_direction": stringify_field(handoff)}

    # tA3e: parse XML-block prompt tokens the manager emits OUTSIDE the
    # JSON (per agent_prompts/manager_prompts.md). Each non-empty token
    # is appended as a labeled manager_message so the UI and the
    # directive's diagnostic payload can show what the manager emitted.
    tokens = wf_repair.parse_prompt_tokens(text)
    for tag in ("MANAGER_MESSAGE", "PLAN", "TODO_LIST", "SUGGESTION_CONTENT",
                "SUGGESTION_VERIFICATION", "HANDOFF_UPDATE"):
        body = tokens.get(tag)
        if body and body != stringify_field(payload.get({
            "MANAGER_MESSAGE": "manager_message",
            "PLAN": "plan",
            "TODO_LIST": "todo_list",
            "SUGGESTION_CONTENT": "suggestion_content",
            "SUGGESTION_VERIFICATION": "suggestion_verification",
            "HANDOFF_UPDATE": "handoff_update",
        }.get(tag, ""), "")):
            db_wf.append_manager_message(
                fi.project_session_id, f"[{tag}]\n{body}"
            )

    # tA3d: if the manager emitted a HANDOFF_UPDATE XML block AND the
    # JSON handoff_update was empty, persist the XML block via the
    # repair helper so apply_handoff_update's contract still works.
    if not state.handoff_update and tokens.get("HANDOFF_UPDATE"):
        wf_repair.apply_handoff_update(
            text,
            updated_by="manager",
            project_session_id=fi.project_session_id,
        )

    stop_signal = stringify_field(payload.get("stop_signal")).strip().upper()
    if stop_signal in {"TASK_HOUNDS_STOP_LOOP", "DIRECTIVE_COMPLETE"}:
        unfinished = [
            todo for todo in state.todo_list
            if _todo_status(todo.get("status")) in {"pending", "in_progress"}
        ]
        if unfinished:
            db_wf.append_manager_message(
                fi.project_session_id,
                f"[continue] Ignored {stop_signal}: {len(unfinished)} todo item(s) remain unfinished.",
            )
        else:
            state.status = "completed"
            # In-memory only: project_handoff has a fixed schema; the
            # manager_messages append below gives the UI the visible signal.
            state.handoff_update = {
                **state.handoff_update,
                "stop_signal": stop_signal,
                "stopped_after": "manager_plan",
            }
            db_wf.append_manager_message(
                fi.project_session_id,
                f"[stop] {stop_signal} — {state.manager_message or '(no manager message)'}",
            )
            return state

    db_wf.set_plan(fi.project_session_id, state.plan, updated_by="manager")
    if not state.plan.strip() and on_missing:
        on_missing()
    return state


# ── Manager step: todo ──────────────────────────────────────────────────────

def manager_todo(state: M.FlowState) -> M.FlowState:
    """Persist the Manager LLM todo list. If absent, re-ask Manager."""
    fi = state.flow_input
    if not state.plan.strip():
        state = manager_digest(state)
        state = manager_plan(state)

    if not state.todo_list:
        state.todo_list = _normalize_manager_todos(
            [],
            fi.project_session_id,
            state.suggestion_content
            or fi.human_suggested_new_task_or_item.strip()
            or "Clarify the first useful implementation step",
        )
    state.todo_update_json = {"items": state.todo_list}
    db_todo.sync_manager_todos(
        fi.project_session_id,
        state.todo_list,
        state.archive_updates,
        reopen_todos=state.reopen_todos,
    )
    return state


# ── Manager step: select task ───────────────────────────────────────────────

def manager_select_task(state: M.FlowState) -> M.FlowState:
    """Pick exactly one task for the worker. If no todos, re-digest."""
    fi = state.flow_input

    def begin_attempt(todo: dict) -> None:
        state.current_todo_id = str(todo.get("id") or "")
        attempts = int(todo.get("attempt_count", 0) or 0)
        if attempts >= MAX_TASK_ATTEMPTS:
            todo["human_attention_status"] = "attention_required"
            if todo.get("id"):
                db_todo.patch_todo(
                    todo["id"],
                    human_attention_status="attention_required",
                )
            return
        todo["status"] = "in_progress"
        todo["worker_task_status"] = "pending"
        todo["reviewer_task_status"] = "pending"
        todo["attempt_count"] = attempts + 1
        if todo.get("id"):
            db_todo.patch_todo(
                todo["id"],
                status="in_progress",
                worker_task_status="pending",
                reviewer_task_status="pending",
                attempt_count=todo["attempt_count"],
            )

    if state.suggestion_content.strip():
        selected = next(
            (
                t
                for t in state.todo_list
                if t.get("content") == state.suggestion_content
                and t.get("status") in ("pending", "in_progress")
                and int(t.get("attempt_count", 0) or 0) < MAX_TASK_ATTEMPTS
                and t.get("human_attention_status") != "attention_required"
            ),
            None,
        )
        if selected is not None:
            begin_attempt(selected)
            return state
        state.suggestion_content = ""
    if not state.todo_list:
        state = manager_digest(state)
        state = manager_todo(state)
    if state.todo_list:
        first_pending = next(
            (
                t for t in state.todo_list
                if t.get("status") in ("pending", "in_progress")
                and int(t.get("attempt_count", 0) or 0) < MAX_TASK_ATTEMPTS
                and t.get("human_attention_status") != "attention_required"
            ),
            None,
        )
        if first_pending is None:
            state.status = "paused"
            state.suggestion_content = ""
            state.current_todo_id = ""
            return state
        state.suggestion_content = first_pending.get("content", "")
        begin_attempt(first_pending)
    else:
        state.suggestion_content = fi.human_suggested_new_task_or_item.strip() or "Clarify the first useful step"
    return state


def _current_todo(state: M.FlowState) -> dict | None:
    if state.current_todo_id:
        by_id = next(
            (todo for todo in state.todo_list if str(todo.get("id") or "") == state.current_todo_id),
            None,
        )
        if by_id:
            return by_id
    current = (state.suggestion_content or "").strip()
    return next(
        (todo for todo in state.todo_list if (todo.get("content") or "").strip() == current),
        None,
    )


def set_current_todo_role_status(state: M.FlowState, role: str, status: str) -> None:
    todo = _current_todo(state)
    if not todo:
        return
    field = "worker_task_status" if role == "worker" else "reviewer_task_status"
    todo[field] = status
    if todo.get("id"):
        db_todo.patch_todo(todo["id"], **{field: status})


# ── Manager step: release ───────────────────────────────────────────────────

def manager_release(state: M.FlowState) -> M.FlowState:
    """Write manager output and release one active Worker suggestion."""
    fi = state.flow_input
    if not state.suggestion_content:
        state = manager_select_task(state)
    if not state.manager_message.strip():
        state.manager_message = f"Manager selected next task: {state.suggestion_content}"
    state.handoff_update = {
        **state.handoff_update,
        "current_task": state.suggestion_content,
        "completion_criteria": [state.suggestion_verification] if state.suggestion_verification else [],
    }
    db_wf.append_manager_message(fi.project_session_id, state.manager_message)
    db_wf.upsert_handoff(fi.project_session_id, **state.handoff_update)
    db_wf.create_suggestion(
        fi.project_session_id,
        state.suggestion_content,
        verification=state.suggestion_verification or None,
        status="released",
    )
    return state


# ── Manager step: brainstorm ─────────────────────────────────────────────────

def manager_brainstorm(state: M.FlowState) -> M.FlowState:
    """After todos are complete, ask the Manager to brainstorm next steps.

    The Manager should:
      - Review what was accomplished so far
      - Identify gaps, potential improvements, new angles
      - Generate concrete new todos if useful work exists
      - Set __done_signal__ = True if the directive is truly complete

    Returns state with updated todo_list and __done_signal__.
    """
    fi = state.flow_input

    # Build a prompt for brainstorming
    existing_todos = "\n".join(f"- [{t.get('status','?')}] {t.get('content','')}" for t in state.todo_list) or "(none)"
    worker_summary = _worker_context_summary({
        "report": state.worker_report,
        "files_changed": state.worker_files_changed,
        "test_result": state.worker_test_result,
        "known_issues": state.worker_known_issues,
    })

    prompt = (
        _lang_directive()
        + "You are the Manager in Task Hounds. After completing existing work, your job is to:\n"
        "1. Review what has been accomplished so far\n"
        "2. Identify gaps, potential improvements, new angles, or missing pieces\n"
        "3. Generate concrete new todos if useful work exists\n"
        "4. Signal done if the directive is truly complete\n\n"
        "Return a JSON object with the following keys:\n"
        "- todo_list: array of new todo objects (each with content, status='pending', priority, owner='manager')\n"
        "- manager_message: brief summary of your brainstorming findings\n"
        "- done: boolean, true if directive is complete (no useful work remains)\n\n"
        "If done=false and todo_list is empty, the system will signal completion anyway.\n\n"
        f"=== HUMAN DIRECTIVE ===\n{fi.human_directive.strip()}\n\n"
        f"=== WORKSPACE PATH ===\n{resolve_workspace(fi, 'manager')}\n\n"
        f"=== EXISTING PLAN ===\n{state.plan or '(none)'}\n\n"
        f"=== EXISTING TODOS ===\n{existing_todos}\n\n"
        f"=== LAST WORKER REPORT ===\n{worker_summary or '(none)'}\n\n"
        f"=== MANAGER MESSAGE (current guidance) ===\n{state.manager_message or '(none)'}\n\n"
        f"=== REVIEWER FEEDBACK ===\n{state.loop_input.reviewer_feedback or '(none)'}\n\n"
    )

    cred_warnings = _blocking_credential_warnings_for_role("manager")
    if cred_warnings:
        state.manager_message = "Brainstorm skipped — missing API credentials. " + " | ".join(cred_warnings)
        state.__done_signal__ = True
        return state

    sid = state.flow_input.project_session_id
    host, port, agent, model = resolve_for_role("manager")
    manager_workspace = resolve_workspace(state.flow_input, "manager")
    manager_session_id = _ensure_role_session(
        state.flow_input, "manager", host, port, manager_workspace
    )
    result = oc_client.run(
        agent=agent,
        model=model,
        prompt=prompt,
        host=host,
        port=port,
        timeout=900,
        cwd=manager_workspace,
        on_chunk=_make_stream_callback("manager", sid),
        workflow_run_id=state.flow_input.run_id,
        project_session_id=sid,
        role="manager",
        session_id=manager_session_id,
        execution_id=db_execution.execution_id(sid, state.flow_input.run_id, "manager"),
    )
    if not result.get("ok"):
        state.manager_message = "Brainstorm call failed: " + result.get("error", {}).get("message", "unknown error")
        state.__done_signal__ = True
        return state

    text = result.get("output", {}).get("text", "")
    try:
        obj = extract_json_object(text, required_keys={"todo_list"})
        todo_items = obj.get("todo_list", [])
        if isinstance(todo_items, list) and todo_items:
            state.todo_list = _normalize_manager_todos(
                todo_items,
                fi.project_session_id,
                "",
            )
            state.todo_update_json = {"items": state.todo_list}
            state.manager_message = stringify_field(obj.get("manager_message", "Brainstorming complete. New work identified."))
            state.__done_signal__ = False
        else:
            # No new todos - mark done
            state.manager_message = stringify_field(obj.get("manager_message", "No new work identified."))
            state.__done_signal__ = bool(obj.get("done", True))
    except ValueError:
        # Could not parse JSON - default to done
        state.manager_message = "Could not parse brainstorming response. Defaulting to complete."
        state.__done_signal__ = True

    db_wf.append_manager_message(fi.project_session_id, f"[brainstorm] {state.manager_message}")
    return state


# ── Worker step ─────────────────────────────────────────────────────────────

def worker_execute(state: M.FlowState) -> M.FlowState:
    """Execute one task. Read latest suggestion from DB; write report to DB."""
    set_current_todo_role_status(state, "worker", "running")
    cred_warnings = _blocking_credential_warnings_for_role("worker")
    if cred_warnings:
        state.worker_report = (
            "Worker skipped — missing API credentials. "
            + " | ".join(cred_warnings)
        )
        state.worker_test_result = "skipped"
        set_current_todo_role_status(state, "worker", "skipped")
        return state
    fi = state.flow_input
    suggestion = db_wf.get_active_suggestion(fi.project_session_id)
    if not suggestion:
        state.worker_report = "No active suggestion to execute."
        state.worker_test_result = "skipped"
        set_current_todo_role_status(state, "worker", "skipped")
        return state

    task = suggestion.get("content", "")
    verification = suggestion.get("verification", "")
    workspace = resolve_workspace(fi, "worker")

    # Build worker prompt
    prompt_template = _load_prompt("worker")
    prompt = (
        _lang_directive()
        + f"{prompt_template.strip()}\n\n" if prompt_template else _lang_directive() + "You are the Worker. Execute one controlled task.\n\n"
    ) + (
        f"=== HUMAN DIRECTIVE ===\n{fi.human_directive}\n\n"
        f"=== WORKSPACE ROOT ===\n{workspace}\n\n"
        "All file reads/writes must stay inside WORKSPACE ROOT. Use absolute paths when creating files.\n\n"
        f"=== MANAGER MESSAGE ===\n{state.manager_message or fi.manager_message or '(none)'}\n\n"
        f"=== MANAGER DECISION ===\n{json.dumps(state.decision or {}, ensure_ascii=False)}\n\n"
        f"=== CURRENT TASK ===\n{task}\n\n"
        f"=== ACCEPTANCE CRITERIA ===\n{verification or '(none)'}\n\n"
        "Return exactly one JSON object matching this schema; unknown keys are forbidden:\n"
        f"{json.dumps(WorkerOutput.model_json_schema(), ensure_ascii=False)}"
    )
    host, port, _agent_name, model = resolve_for_role("worker")
    worker_session_id = _ensure_role_session(fi, "worker", host, port, workspace)
    result = oc_client.run(
        agent=_agent_name,
        prompt=prompt,
        host=host,
        port=port,
        model=model,
        session_id=worker_session_id,
        timeout=900,
        cwd=workspace,
        on_chunk=_make_stream_callback("worker", fi.project_session_id),
        workflow_run_id=fi.run_id,
        project_session_id=fi.project_session_id,
        role="worker",
        execution_id=db_execution.execution_id(fi.project_session_id, fi.run_id, "worker"),
    )
    opencode_ok = bool(result.get("ok"))
    if not opencode_ok:
        error_message = result.get("error", {}).get("message", "opencode worker call failed")
        current_todo = _current_todo(state)
        is_timeout = "timed out" in str(error_message).lower()
        timeout_count = int((current_todo or {}).get("worker_timeout_count", 0) or 0)
        escalation = ""
        if is_timeout:
            timeout_count += 1
            if current_todo is not None:
                current_todo["worker_timeout_count"] = timeout_count
                if current_todo.get("id"):
                    db_todo.patch_todo(
                        current_todo["id"],
                        worker_timeout_count=timeout_count,
                    )
            if timeout_count == 3:
                escalation = (
                    " Third consecutive Worker timeout. OpenCode restart requires "
                    "human confirmation; do not restart it automatically."
                )
            elif timeout_count >= 4:
                escalation = (
                    " Fourth consecutive Worker timeout. Stop retrying this Worker task "
                    "and return control to Manager for a different plan or loop termination."
                )
        text = f"ERROR: {error_message}"
        if escalation:
            text += escalation
        state.worker_report = text
        state.worker_files_changed = []
        state.worker_test_result = "failed"
        # Persist a row so the Reviewer step can see WHY the Worker
        # failed, then mark the suggestion as failed (NOT done) so the
        # Manager picks the next suggestion on the next tick instead of
        # skipping this one forever.
        db_wf.append_worker_report(
            fi.project_session_id,
            text,
            files_changed=[],
            test_result="failed",
            known_issues=[f"worker opencode call failed: {error_message}"],
            worker_opencode_session_id=fi.worker_opencode_session_id,
        )
        set_current_todo_role_status(state, "worker", "error")
        return state

    current_todo = _current_todo(state)
    if current_todo and int(current_todo.get("worker_timeout_count", 0) or 0):
        current_todo["worker_timeout_count"] = 0
        if current_todo.get("id"):
            db_todo.patch_todo(current_todo["id"], worker_timeout_count=0)

    text = result.get("output", {}).get("text", "")
    try:
        worker_output = _validate_contract(text, WorkerOutput)
    except (ValueError, ValidationError) as first_error:
        repair_result = oc_client.run(
            agent=_agent_name,
            prompt=_format_repair_prompt(WorkerOutput, text, first_error),
            host=host,
            port=port,
            model=model,
            session_id=_ensure_role_session(fi, "worker", host, port, workspace),
            timeout=180,
            cwd=workspace,
            on_chunk=_make_stream_callback("worker", fi.project_session_id),
            workflow_run_id=fi.run_id,
            project_session_id=fi.project_session_id,
            role="worker",
            execution_id=db_execution.execution_id(fi.project_session_id, fi.run_id, "worker"),
        )
        if not repair_result.get("ok"):
            state.worker_report = f"Worker format_contract_error: {first_error}"
            state.worker_test_result = "failed"
            set_current_todo_role_status(state, "worker", "error")
            return state
        text = repair_result.get("output", {}).get("text", "")
        try:
            worker_output = _validate_contract(text, WorkerOutput)
        except (ValueError, ValidationError) as second_error:
            state.worker_report = f"Worker format_contract_error after retry: {second_error}"
            state.worker_test_result = "failed"
            set_current_todo_role_status(state, "worker", "error")
            return state
    worker_payload = worker_output.model_dump(mode="json")
    worker_meta = {
        **worker_payload,
        "known_issues": [issue_text(issue) for issue in worker_payload["known_issues"]],
    }
    raw_files: list[str] = list(worker_meta.get("files_changed") or [])
    # Detect files changed via git
    try:
        out = subprocess.run(
            ["git", "status", "--short", "--", "."],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in out.stdout.splitlines():
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    raw_files.append(parts[-1])
    except Exception:
        pass

    # tA5a: validate the worker-claimed files. git status can include
    # paths outside the workspace (when the worker wandered), deleted
    # files (no longer on disk), or empty strings (parse glitches).
    # Filter to paths that (a) are inside workspace, (b) exist on disk.
    # Files that exist in git's index but were deleted are still
    # reported -- the Reviewer wants the "what changed" picture.
    workspace_resolved = workspace.resolve()
    validated_files: list[str] = []
    suspicious: list[str] = []
    for f in dict.fromkeys(raw_files):
        if not f or f.startswith("?"):
            # untracked files: include if real, drop if querystring glitch
            continue
        try:
            abs_path = (workspace / f).resolve()
        except Exception:
            continue
        try:
            abs_path.relative_to(workspace_resolved)
        except ValueError:
            # Path escaped the workspace -- this is the security
            # boundary the audit wanted validated.
            suspicious.append(f)
            continue
        if abs_path.exists():
            validated_files.append(f)
        # else: deleted file; the path is still a real change, keep
        # it (the Reviewer will see FILES_CHANGED with the path even
        # if the file no longer exists on disk).

    files = validated_files
    if suspicious:
        # Note the escapees in known_issues so the Reviewer can
        # see that the worker touched something outside the
        # workspace. The DB row gets the validated list (paths
        # inside workspace only); the suspicious ones are written
        # to the worker's report text so the operator can audit.
        text = (
            text
            + "\n\n[files-outside-workspace] "
            + ", ".join(suspicious)
        )

    # Persist (happy path: opencode_ok=True)
    state.worker_report = text
    state.worker_files_changed = files
    state.worker_test_result = worker_meta.get("test_result") or "unknown"
    state.suggestion_id = int(suggestion["id"])
    set_current_todo_role_status(
        state,
        "worker",
        "skipped" if str(state.worker_test_result).lower().startswith("skipped") else "reported",
    )
    db_wf.append_worker_report(
        fi.project_session_id,
        text,
        files_changed=files,
        test_result=state.worker_test_result,
        known_issues=worker_meta.get("known_issues") or [],
        worker_opencode_session_id=fi.worker_opencode_session_id,
    )
    # The Worker does NOT mark the suggestion 'done' here. The
    # Reviewer is the only one that may promote a suggestion to
    # 'done' (on qa_result='pass'). The Manager's next loop
    # iteration will see a non-terminal status and won't re-pick
    # the same suggestion until the Reviewer has decided.
    return state


# ── Reviewer step ───────────────────────────────────────────────────────────

MAX_TASK_ATTEMPTS = 4


def manager_reconcile(state: M.FlowState) -> M.FlowState:
    """Let Manager exclusively decide todo status after role reports."""
    fi = state.flow_input
    reviewed_content = (state.suggestion_content or "").strip()
    reviewed_id = state.current_todo_id
    reviewed_before = _current_todo(state)
    reviewed_attempts = int((reviewed_before or {}).get("attempt_count", 0) or 0)
    state.existing_context = load_existing_context(fi.project_session_id)
    state.loop_input.worker_report = state.worker_report
    state.loop_input.files_changed = list(state.worker_files_changed)
    state.loop_input.test_result = state.worker_test_result
    state.loop_input.known_issues = list(state.worker_known_issues)
    state.loop_input.reviewer_feedback = state.reviewer_feedback

    state = manager_plan(state)
    state = manager_todo(state)

    reviewed_todo = next(
        (
            todo for todo in state.todo_list
            if (reviewed_id and str(todo.get("id") or "") == reviewed_id)
            or (todo.get("content") or "").strip() == reviewed_content
        ),
        None,
    )
    progress_summary = [
        f"Attempt {reviewed_attempts}/{MAX_TASK_ATTEMPTS}",
        f"Worker: {(reviewed_before or {}).get('worker_task_status', 'unknown')}",
        f"Reviewer: {(reviewed_before or {}).get('reviewer_task_status', 'unknown')}",
    ]
    state.handoff_update = {
        **state.handoff_update,
        "current_task": reviewed_content,
        "current_micro_flow": progress_summary,
        "tested_files": list(state.worker_files_changed),
        "known_bugs": list(dict.fromkeys(
            text
            for item in [
                *state.worker_known_issues,
                *state.reviewer_bugs,
                *state.reviewer_possible_problems,
            ]
            if (text := stringify_field(item))
        )),
    }
    db_wf.upsert_handoff(fi.project_session_id, **{**state.handoff_update, "updated_by": "manager"})
    if state.manager_message.strip():
        db_wf.append_manager_message(fi.project_session_id, state.manager_message)
    if state.suggestion_id is not None:
        db_wf.update_suggestion_status(
            state.suggestion_id,
            "done" if reviewed_todo and reviewed_todo.get("status") == "completed" else "reviewed",
        )

    if reviewed_todo and reviewed_todo.get("status") != "completed":
        attempts = max(reviewed_attempts, int(reviewed_todo.get("attempt_count", 0) or 0))
        if attempts >= MAX_TASK_ATTEMPTS:
            reviewed_todo["human_attention_status"] = "attention_required"
            db_todo.patch_todo(
                reviewed_todo["id"],
                human_attention_status="attention_required",
            )
            db_wf.append_manager_message(
                fi.project_session_id,
                f"[human attention required] '{reviewed_content}' remains unresolved after {attempts} attempts.",
            )
            state.status = "pending"
            state.suggestion_content = ""
            state.suggestion_id = None
            state.current_todo_id = ""
            return state

    active_todos = db_todo.list_active_todos(fi.project_session_id)
    if active_todos and all(todo.get("status") == "completed" for todo in active_todos):
        state.todo_list = active_todos
        state.status = "completed"
        return state

    state.status = "pending"
    state.suggestion_content = ""
    state.suggestion_id = None
    state.current_todo_id = ""
    return state


def reviewer_check(state: M.FlowState) -> M.FlowState:
    """Review the worker's result. Read latest worker report from DB; write feedback.

    Persistence (Phase-7 fix for the silent-reviewer bug):
      1. create_reviewer_session(suggestion_id) at the start of the
         function -- so a row exists even on early-exit paths
         (missing creds, no worker report).
      2. update_reviewer_session(...) at every return point with
         status in {completed, failed, needs_review, skipped}.
      3. qa_result drives the final status:
           'pass'            -> status=completed, completed_at=NOW
           'fail'            -> status=failed,    completed_at=NOW
           'needs_review'    -> status=needs_review, completed_at=NOW
           'skipped'          -> status=skipped,   completed_at=NOW
      4. state.status is set to 'completed' only when qa_result=='pass',
         to 'failed' when qa_result=='fail' or LLM errored, and to
         'needs_review' otherwise. The graph layer then propagates
         state.status so the directive ends as failed (not processed)
         when the Reviewer rejected the work.
      5. (Phase-8 fix) The function body is wrapped in try/finally
         so the suggestion row is moved to a TERMINAL status
         (done/failed/needs_review) on every return path. The
         Worker no longer marks the suggestion 'done' (see
         worker_execute); the Reviewer is the only one that may
         promote a suggestion to 'done', and only on qa_result='pass'.
    """
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    fi = state.flow_input
    set_current_todo_role_status(state, "reviewer", "running")

    # 1. The Reviewer reviews the SAME suggestion the Worker just
    # executed. state.suggestion_id is the explicit plumbing set by
    # worker_execute. We fall back to get_active_suggestion() only
    # for the standalone-reviewer test path (no Worker call in
    # between). Falling back is safe because in the production flow
    # the Manager would have released a new suggestion in the
    # meantime, so without state.suggestion_id we'd risk reviewing
    # the wrong row -- but the test path doesn't loop.
    suggestion_id: int | None = state.suggestion_id
    if suggestion_id is None:
        suggestion = db_wf.get_active_suggestion(fi.project_session_id)
        if suggestion is not None:
            suggestion_id = int(suggestion["id"])
    reviewer_session_id: int | None = None
    if suggestion_id is not None:
        reviewer_session_id = db_wf.create_reviewer_session(
            suggestion_id, status="running"
        )

    try:
        cred_warnings = _blocking_credential_warnings_for_role("reviewer")
        if cred_warnings:
            state.reviewer_feedback = (
                "Reviewer skipped — missing API credentials. "
                + " | ".join(cred_warnings)
            )
            state.reviewer_qa_result = "skipped"
            if reviewer_session_id is not None:
                db_wf.update_reviewer_session(
                    reviewer_session_id,
                    status="skipped",
                    review_notes=state.reviewer_feedback,
                    error="missing_credentials",
                    completed=True,
                )
            return state

        worker_rep = db_wf.latest_worker_report(fi.project_session_id)
        if not worker_rep:
            state.reviewer_feedback = "No worker report to review."
            state.reviewer_qa_result = "needs_review"
            if reviewer_session_id is not None:
                db_wf.update_reviewer_session(
                    reviewer_session_id,
                    status="needs_review",
                    review_notes=state.reviewer_feedback,
                    error="no_worker_report",
                    completed=True,
                )
            return state

        # Phase-8 (P0-2) defensive check: refuse to publish pass
        # when the Worker's worker_reports row shows test_result
        # in {failed, skipped}. The LLM may incorrectly say pass
        # when reading an ERROR-prefixed report.
        worker_test_result = str(worker_rep.get("test_result", "") or "").strip().lower()
        if worker_test_result == "failed":
            state.reviewer_feedback = (
                f"Worker reported test_result='{worker_test_result}'; "
                f"Reviewer must NOT publish pass on a failed Worker."
            )
            state.reviewer_qa_result = "fail"
            if reviewer_session_id is not None:
                db_wf.update_reviewer_session(
                    reviewer_session_id,
                    status="failed",
                    review_notes=state.reviewer_feedback,
                    error=f"worker test_result={worker_test_result}",
                    completed=True,
                )
            return state

        prompt_template = _load_prompt("reviewer")
        prompt = (
            _lang_directive()
            + f"{prompt_template.strip()}\n\n" if prompt_template else _lang_directive() + "You are the Reviewer. Check the worker's output for QA, bugs, UI/UX, risks.\n\n"
        ) + (
            f"=== HUMAN DIRECTIVE ===\n{fi.human_directive}\n\n"
            f"=== WORKSPACE ROOT ===\n{resolve_workspace(fi, 'reviewer')}\n\n"
            f"=== MANAGER MESSAGE ===\n{state.manager_message or fi.manager_message or '(none)'}\n\n"
            f"=== MANAGER PLAN ===\n{state.plan or '(none)'}\n\n"
            f"=== WORKER REPORT ===\n{worker_rep.get('report', '')}\n\n"
            f"=== FILES CHANGED ===\n{worker_rep.get('files_changed', [])}\n\n"
            f"=== TEST RESULT ===\n{worker_rep.get('test_result', '')}\n\n"
            "Return exactly one JSON object matching this schema; unknown keys are forbidden:\n"
            f"{json.dumps(ReviewerOutput.model_json_schema(), ensure_ascii=False)}"
        )
        host, port, _agent_name, model = resolve_for_role("reviewer")
        workspace = resolve_workspace(fi, "reviewer")
        reviewer_session_id_value = _ensure_role_session(
            fi, "reviewer", host, port, workspace
        )
        result = oc_client.run(
            agent=_agent_name,
            prompt=prompt,
            host=host,
            port=port,
            model=model,
            session_id=reviewer_session_id_value,
            timeout=300,
            cwd=workspace,
            on_chunk=_make_stream_callback("reviewer", fi.project_session_id),
            workflow_run_id=fi.run_id,
            project_session_id=fi.project_session_id,
            role="reviewer",
            execution_id=db_execution.execution_id(fi.project_session_id, fi.run_id, "reviewer"),
        )
        if not result.get("ok"):
            state.reviewer_feedback = f"Reviewer error: {result.get('error', {}).get('message', '?')}"
            state.reviewer_qa_result = "needs_review"
            if reviewer_session_id is not None:
                db_wf.update_reviewer_session(
                    reviewer_session_id,
                    status="failed",
                    review_notes=state.reviewer_feedback,
                    error=str(result.get("error", {}).get("message", "opencode error")),
                    completed=True,
                )
            return state

        text = result.get("output", {}).get("text", "")
        bugs_list: list = []
        uiux_list: list = []
        possible_problems_list: list = []
        safety_security_risks_list: list = []
        try:
            reviewer_output = _validate_contract(text, ReviewerOutput)
        except (ValueError, ValidationError) as first_error:
            repair_result = oc_client.run(
                agent=_agent_name,
                prompt=_format_repair_prompt(ReviewerOutput, text, first_error),
                host=host,
                port=port,
                model=model,
                session_id=_ensure_role_session(
                    fi, "reviewer", host, port, workspace
                ),
                timeout=180,
                cwd=workspace,
                on_chunk=_make_stream_callback("reviewer", fi.project_session_id),
                workflow_run_id=fi.run_id,
                project_session_id=fi.project_session_id,
                role="reviewer",
                execution_id=db_execution.execution_id(fi.project_session_id, fi.run_id, "reviewer"),
            )
            if not repair_result.get("ok"):
                state.reviewer_feedback = f"Reviewer format_contract_error: {first_error}"
                state.reviewer_qa_result = "needs_review"
                qa = "needs_review"
                return state
            text = repair_result.get("output", {}).get("text", "")
            try:
                reviewer_output = _validate_contract(text, ReviewerOutput)
            except (ValueError, ValidationError) as second_error:
                state.reviewer_feedback = f"Reviewer format_contract_error after retry: {second_error}"
                state.reviewer_qa_result = "needs_review"
                qa = "needs_review"
                return state
        reviewer_payload = reviewer_output.model_dump(mode="json")
        state.reviewer_feedback = reviewer_payload["reviewer_feedback"]
        qa = reviewer_payload["qa_result"]
        state.reviewer_qa_result = qa
        bugs_list = [issue_text(issue) for issue in reviewer_payload["bugs"]]
        uiux_list = [issue_text(issue) for issue in reviewer_payload["uiux_suggestions"]]
        possible_problems_list = [issue_text(issue) for issue in reviewer_payload["possible_problems"]]
        safety_security_risks_list = [issue_text(issue) for issue in reviewer_payload["safety_security_risks"]]
        state.reviewer_bugs = bugs_list
        state.reviewer_uiux = uiux_list
        state.reviewer_possible_problems = possible_problems_list
        state.reviewer_safety_security_risks = safety_security_risks_list

        if reviewer_session_id is not None:
            db_wf.update_reviewer_session(
                reviewer_session_id,
                status=(
                    "completed" if qa == "pass"
                    else "failed" if qa == "fail"
                    else "needs_review"
                ),
                review_notes=state.reviewer_feedback,
                bugs_json=json.dumps(bugs_list + uiux_list + possible_problems_list + safety_security_risks_list),
                style_feedback=stringify_field(state.reviewer_feedback)[:4000],
                scripts_documented=(
                    f"files_changed={worker_rep.get('files_changed', [])}; "
                    f"test_result={worker_rep.get('test_result', '')}"
                ),
                completed=True,
            )

        try:
            all_issues = []
            for bug in bugs_list:
                all_issues.append({"issue_type": "bug", "severity": 2, "description": bug})
            for uiux in uiux_list:
                all_issues.append({"issue_type": "ui_ux", "severity": 3, "description": uiux})
            for prob in possible_problems_list:
                all_issues.append({"issue_type": "other", "severity": 3, "description": prob})
            for risk in safety_security_risks_list:
                all_issues.append({"issue_type": "risk", "severity": 1, "description": risk})
            if all_issues:
                db_wf.create_reviewer_issues_batch(
                    issues=all_issues,
                    project_session_id=fi.project_session_id,
                    suggestion_id=suggestion_id,
                )
        except Exception:
            pass

        return state
    finally:
        reviewer_status = (
            "pass" if state.reviewer_qa_result == "pass"
            else "fail" if state.reviewer_qa_result == "fail"
            else "needs_review"
        )
        set_current_todo_role_status(state, "reviewer", reviewer_status)
    return state
