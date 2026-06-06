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

from task_hounds_api.db import ROOT
from task_hounds_api.db.ops import workflow as db_wf
from task_hounds_api.db.ops import todo as db_todo
from task_hounds_api.db.ops import chat as db_chat
from task_hounds_api.opencode import client as oc_client
from task_hounds_api.opencode.binding_resolver import resolve_for_role
from task_hounds_api.workflow import models as M


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
    return path.read_text(encoding="utf-8")


# ── JSON extraction ──────────────────────────────────────────────────────────

def extract_json_object(text: str, required_keys: set[str]) -> dict:
    """Find the first JSON object in `text` (within ```json fences if present)."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object found")
        candidate = text[start : end + 1]
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from e
    missing = required_keys - set(obj.keys())
    if missing:
        raise ValueError(f"missing required keys: {sorted(missing)}")
    return obj


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


def _opencode_port() -> int:
    raw = os.environ.get("TASK_HOUNDS_OPENCODE_PORT", "18765")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 18765


def _manager_agent_name() -> str:
    return os.environ.get("TASK_HOUNDS_MANAGER_OPENCODE_AGENT", "Sisyphus - ultraworker")


def _worker_agent_name() -> str:
    return os.environ.get("TASK_HOUNDS_WORKER_OPENCODE_AGENT", "Sisyphus - ultraworker")


def _reviewer_agent_name() -> str:
    return os.environ.get("TASK_HOUNDS_REVIEWER_OPENCODE_AGENT", "Sisyphus - ultraworker")


def _opencode_model(role: str) -> str:
    role_key = f"TASK_HOUNDS_{role.upper()}_OPENCODE_MODEL"
    return os.environ.get(role_key) or os.environ.get(
        "TASK_HOUNDS_OPENCODE_MODEL",
        "minimax-coding-plan/MiniMax-M2.7",
    )


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _todo_status(value) -> str:
    normalized = str(value or "pending").strip().lower().replace("-", "_")
    return normalized if normalized in {"pending", "in_progress", "completed", "blocked"} else "pending"


def _todo_priority(value) -> str:
    normalized = str(value or "medium").strip().lower()
    return normalized if normalized in {"high", "medium", "low"} else "medium"


def _normalize_manager_todos(value, session_id: str, fallback: str) -> list[dict]:
    raw_items = value if isinstance(value, list) else []
    if not raw_items and fallback:
        raw_items = [{"content": fallback}]

    todos = []
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
        todos.append({
            "id": item_id,
            "session_id": session_id,
            "parent_id": None,
            "content": content,
            "status": status,
            "priority": priority,
            "position": pos,
            "owner": owner,
        })
    return todos


def _manager_prompt(state: M.FlowState) -> str:
    fi = state.flow_input
    ctx = state.existing_context or {}
    manager_history = "\n".join(
        f"- {stringify_field(msg)[:500]}" for msg in _as_list(ctx.get("manager_messages"))[:5]
    ) or "(none)"
    existing_todos = "\n".join(f"- {item}" for item in fi.todo_items) or "(none)"
    prompt_hint = _load_prompt("manager_select")

    return (
        "You are the Manager agent in Task Hounds. Plan the next concrete unit of work.\n"
        "Return exactly one JSON object, with no prose outside JSON.\n\n"
        "Required JSON keys:\n"
        "- input_digest: concise summary of directive and current project state\n"
        "- decision: object describing why this task is next\n"
        "- manager_message: short user-facing manager update\n"
        "- plan: implementation plan text\n"
        "- todo_list: array of todo objects with content/status/priority/owner\n"
        "- suggestion_content: one concrete task for Worker to execute now\n"
        "- suggestion_verification: acceptance check for Worker/Reviewer\n"
        "- handoff_update: object with current_task, working_direction, completion_criteria\n\n"
        f"Directive:\n{fi.human_directive.strip()}\n\n"
        f"Workspace path (all file work must stay inside this directory):\n{fi.workspace_path or ROOT}\n\n"
        f"Human thought:\n{fi.human_new_thought_and_suggestion.strip() or '(none)'}\n\n"
        f"Human suggested task:\n{fi.human_suggested_new_task_or_item.strip() or '(none)'}\n\n"
        f"Input digest from local context:\n{state.input_digest.strip() or '(none)'}\n\n"
        f"Existing plan:\n{ctx.get('plan', '') or '(none)'}\n\n"
        f"Existing todos:\n{existing_todos}\n\n"
        f"Last worker report:\n{ctx.get('worker_report', '') or '(none)'}\n\n"
        f"Reviewer feedback:\n{state.loop_input.reviewer_feedback or '(none)'}\n\n"
        f"Recent manager messages:\n{manager_history}\n\n"
        f"Manager prompt reference:\n{prompt_hint[:4000] if prompt_hint else '(none)'}\n"
    )


def _call_manager(state: M.FlowState) -> dict:
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    cred_warnings = RuntimeManager.instance().validate_credentials()
    if cred_warnings:
        raise RuntimeError(
            "Cannot call Manager — missing API credentials. "
            + " | ".join(cred_warnings)
        )

    host, port, agent, model = resolve_for_role("manager")
    result = oc_client.run(
        agent=agent,
        prompt=_manager_prompt(state),
        host=host,
        port=port,
        model=model,
        session_id=state.flow_input.manager_opencode_session_id,
        timeout=300,
        cwd=state.flow_input.workspace_path or ROOT,
    )
    if not result.get("ok"):
        message = result.get("error", {}).get("message", "manager OpenCode call failed")
        raise RuntimeError(f"Manager OpenCode call failed: {message}")

    text = result.get("output", {}).get("text", "")
    return extract_json_object(
        text,
        required_keys={
            "input_digest",
            "decision",
            "manager_message",
            "plan",
            "todo_list",
            "suggestion_content",
            "suggestion_verification",
            "handoff_update",
        },
    )


# ── Existing-context loader ─────────────────────────────────────────────────

def load_existing_context(session_id: str) -> dict:
    """Read the latest plan, handoff, manager messages, worker report, reviewer feedback
    from DB. This is what the Manager uses to estimate current progress."""
    plan = db_wf.get_plan(session_id)
    handoff = db_wf.get_handoff(session_id)
    manager_msgs = db_wf.list_manager_messages(session_id, limit=5)
    worker_rep = db_wf.latest_worker_report(session_id)
    suggestion = db_wf.get_active_suggestion(session_id)
    return {
        "plan": plan.get("content", "") if plan else "",
        "plan_updated_at": plan.get("updated_at", "") if plan else "",
        "handoff_update": {
            "current_task": handoff.get("current_task", "") if handoff else "",
            "current_micro_flow": handoff.get("current_micro_flow", []) if handoff else [],
            "known_bugs": handoff.get("known_bugs", []) if handoff else [],
            "completion_criteria": handoff.get("completion_criteria", []) if handoff else [],
            "working_direction": handoff.get("working_direction", "") if handoff else "",
        } if handoff else {},
        "manager_messages": [m.get("content", "") for m in manager_msgs],
        "worker_report": worker_rep.get("report", "") if worker_rep else "",
        "test_result": worker_rep.get("test_result", "") if worker_rep else "",
        "files_changed": worker_rep.get("files_changed", []) if worker_rep else [],
        "known_issues": worker_rep.get("known_issues", []) if worker_rep else [],
        "active_suggestion": suggestion.get("content", "") if suggestion else "",
    }


# ── State <-> DB loaders ─────────────────────────────────────────────────────

def set_agent_state_safe(role: str, state: str, current_step: str | None = None) -> None:
    """Set agent state in DB. Silent if agent doesn't exist (for tests/offline)."""
    try:
        from task_hounds_api.workflow.signals import set_agent_state
        set_agent_state(role, state, current_step)
    except Exception:
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
            f"Existing plan: {ctx.get('plan', '(none)')[:300]}",
            f"Last worker report: {ctx.get('worker_report', '(none)')[:300]}",
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
    """Ask the Manager LLM to form the plan and next Worker task."""
    fi = state.flow_input
    payload = _call_manager(state)

    state.input_digest = stringify_field(payload.get("input_digest")) or state.input_digest
    decision = payload.get("decision") or {}
    state.decision = decision if isinstance(decision, dict) else {"summary": stringify_field(decision)}
    state.manager_message = stringify_field(payload.get("manager_message"))
    state.plan = stringify_field(payload.get("plan"))
    state.todo_list = _normalize_manager_todos(
        payload.get("todo_list"),
        fi.project_session_id,
        stringify_field(payload.get("suggestion_content")),
    )
    state.todo_update_json = {"items": state.todo_list}
    state.suggestion_content = stringify_field(payload.get("suggestion_content"))
    state.suggestion_verification = stringify_field(payload.get("suggestion_verification"))
    handoff = payload.get("handoff_update") or {}
    state.handoff_update = handoff if isinstance(handoff, dict) else {"working_direction": stringify_field(handoff)}

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
    db_todo.bulk_upsert_todos(fi.project_session_id, state.todo_list)
    return state


# ── Manager step: select task ───────────────────────────────────────────────

def manager_select_task(state: M.FlowState) -> M.FlowState:
    """Pick exactly one task for the worker. If no todos, re-digest."""
    fi = state.flow_input
    if state.suggestion_content.strip():
        return state
    if not state.todo_list:
        state = manager_digest(state)
        state = manager_todo(state)
    if state.todo_list:
        first_pending = next(
            (t for t in state.todo_list if t.get("status") in ("pending", "in_progress")),
            state.todo_list[0],
        )
        state.suggestion_content = first_pending.get("content", "")
    else:
        state.suggestion_content = fi.human_suggested_new_task_or_item.strip() or "Clarify the first useful step"
    return state


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


# ── Worker step ─────────────────────────────────────────────────────────────

def worker_execute(state: M.FlowState) -> M.FlowState:
    """Execute one task. Read latest suggestion from DB; write report to DB."""
    from task_hounds_api.opencode.runtime_manager import RuntimeManager

    cred_warnings = RuntimeManager.instance().validate_credentials()
    if cred_warnings:
        state.worker_report = (
            "Worker skipped — missing API credentials. "
            + " | ".join(cred_warnings)
        )
        state.worker_test_result = "skipped"
        return state
    fi = state.flow_input
    suggestion = db_wf.get_active_suggestion(fi.project_session_id)
    if not suggestion:
        state.worker_report = "No active suggestion to execute."
        state.worker_test_result = "skipped"
        return state

    task = suggestion.get("content", "")
    verification = suggestion.get("verification", "")
    workspace = Path(fi.workspace_path or ROOT)

    # Build worker prompt
    prompt_template = _load_prompt("worker")
    prompt = (
        f"{prompt_template.strip()}\n\n" if prompt_template else "You are the Worker. Execute one controlled task.\n\n"
    ) + (
        f"=== HUMAN DIRECTIVE ===\n{fi.human_directive}\n\n"
        f"=== WORKSPACE ROOT ===\n{workspace}\n\n"
        "All file reads/writes must stay inside WORKSPACE ROOT. Use absolute paths when creating files.\n\n"
        f"=== MANAGER MESSAGE ===\n{state.manager_message or fi.manager_message or '(none)'}\n\n"
        f"=== MANAGER DECISION ===\n{json.dumps(state.decision or {}, ensure_ascii=False)}\n\n"
        f"=== CURRENT TASK ===\n{task}\n\n"
        f"=== ACCEPTANCE CRITERIA ===\n{verification or '(none)'}\n\n"
        "Report what you changed, any files changed, test results, and known issues."
    )
    host, port, _agent_name, model = resolve_for_role("worker")
    result = oc_client.run(
        agent=_agent_name,
        prompt=prompt,
        host=host,
        port=port,
        model=model,
        session_id=fi.worker_opencode_session_id,
        timeout=900,
        cwd=workspace,
    )
    opencode_ok = bool(result.get("ok"))
    if not opencode_ok:
        error_message = result.get("error", {}).get("message", "opencode worker call failed")
        text = f"ERROR: {error_message}"
        state.worker_report = text
        state.worker_files_changed = []
        state.worker_test_result = "failed"
        state.status = "failed"
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
        db_wf.update_suggestion_status(suggestion["id"], "failed")
        return state

    text = result.get("output", {}).get("text", "")

    files = []
    # Detect files changed via git
    try:
        out = subprocess.run(
            ["git", "status", "--short"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in out.stdout.splitlines():
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    files.append(parts[-1])
    except Exception:
        pass

    # Persist (happy path: opencode_ok=True)
    state.worker_report = text
    state.worker_files_changed = files
    state.worker_test_result = "unknown"
    state.suggestion_id = int(suggestion["id"])
    db_wf.append_worker_report(
        fi.project_session_id,
        text,
        files_changed=files,
        test_result=state.worker_test_result,
        known_issues=[],
        worker_opencode_session_id=fi.worker_opencode_session_id,
    )
    # The Worker does NOT mark the suggestion 'done' here. The
    # Reviewer is the only one that may promote a suggestion to
    # 'done' (on qa_result='pass'). The Manager's next loop
    # iteration will see a non-terminal status and won't re-pick
    # the same suggestion until the Reviewer has decided.
    return state


# ── Reviewer step ───────────────────────────────────────────────────────────

def _finalize_suggestion_status(suggestion_id: int | None, state_status: str) -> None:
    """Map the Reviewer's final state.status to a terminal suggestion
    status. This is the single point where the Reviewer writes back
    to suggestion_queue, so the audit's 'suggestion only marked
    done after Reviewer pass' rule is enforced here.

    Mapping:
      state.status == 'completed'  -> suggestion = 'done'
      state.status == 'needs_review' -> suggestion = 'needs_review'
      anything else (failed, skipped, unknown) -> suggestion = 'failed'

    Called from the try/finally wrapper in reviewer_check so every
    return path triggers the write.
    """
    if suggestion_id is None:
        return
    if state_status == "completed":
        new_status = "done"
    elif state_status == "needs_review":
        new_status = "needs_review"
    else:
        new_status = "failed"
    db_wf.update_suggestion_status(suggestion_id, new_status)


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
        cred_warnings = RuntimeManager.instance().validate_credentials()
        if cred_warnings:
            state.reviewer_feedback = (
                "Reviewer skipped — missing API credentials. "
                + " | ".join(cred_warnings)
            )
            state.reviewer_qa_result = "skipped"
            state.status = "failed"
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
            state.status = "needs_review"
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
        if worker_test_result in {"failed", "skipped"}:
            state.reviewer_feedback = (
                f"Worker reported test_result='{worker_test_result}'; "
                f"Reviewer must NOT publish pass on a failed Worker."
            )
            state.reviewer_qa_result = "fail"
            state.status = "failed"
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
            f"{prompt_template.strip()}\n\n" if prompt_template else "You are the Reviewer. Check the worker's output for QA, bugs, UI/UX, risks.\n\n"
        ) + (
            f"=== HUMAN DIRECTIVE ===\n{fi.human_directive}\n\n"
            f"=== WORKSPACE ROOT ===\n{fi.workspace_path or ROOT}\n\n"
            f"=== MANAGER MESSAGE ===\n{state.manager_message or fi.manager_message or '(none)'}\n\n"
            f"=== MANAGER PLAN ===\n{state.plan or '(none)'}\n\n"
            f"=== WORKER REPORT ===\n{worker_rep.get('report', '')}\n\n"
            f"=== FILES CHANGED ===\n{worker_rep.get('files_changed', [])}\n\n"
            f"=== TEST RESULT ===\n{worker_rep.get('test_result', '')}\n\n"
            "Return JSON with: reviewer_feedback, qa_result, bugs, uiux_suggestions."
        )
        host, port, _agent_name, model = resolve_for_role("reviewer")
        result = oc_client.run(
            agent=_agent_name,
            prompt=prompt,
            host=host,
            port=port,
            model=model,
            session_id=fi.reviewer_opencode_session_id,
            timeout=300,
            cwd=fi.workspace_path or ROOT,
        )
        if not result.get("ok"):
            state.reviewer_feedback = f"Reviewer error: {result.get('error', {}).get('message', '?')}"
            state.reviewer_qa_result = "needs_review"
            state.status = "failed"
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
        try:
            obj = extract_json_object(text, required_keys={"reviewer_feedback", "qa_result"})
            state.reviewer_feedback = stringify_field(obj.get("reviewer_feedback"))
            qa = obj.get("qa_result", "needs_review")
            state.reviewer_qa_result = qa
            bugs_list = obj.get("bugs", []) or []
            uiux_list = obj.get("uiux_suggestions", []) or []
            state.reviewer_bugs = bugs_list
            state.reviewer_uiux = uiux_list
        except ValueError:
            state.reviewer_feedback = text.strip()
            state.reviewer_qa_result = "needs_review"
            qa = "needs_review"

        if qa == "pass":
            state.status = "completed"
        elif qa == "fail":
            state.status = "failed"
        else:
            state.status = "needs_review"

        if reviewer_session_id is not None:
            db_wf.update_reviewer_session(
                reviewer_session_id,
                status=(
                    "completed" if qa == "pass"
                    else "failed" if qa == "fail"
                    else "needs_review"
                ),
                review_notes=state.reviewer_feedback,
                bugs_json=json.dumps(bugs_list + uiux_list),
                style_feedback=stringify_field(state.reviewer_feedback)[:4000],
                scripts_documented=(
                    f"files_changed={worker_rep.get('files_changed', [])}; "
                    f"test_result={worker_rep.get('test_result', '')}"
                ),
                completed=True,
            )
        return state
    finally:
        # Reviewer is the single writer of suggestion's terminal
        # status. Removing this would re-introduce the P0-1 bug
        # (Worker marked done, Reviewer saw nothing).
        _finalize_suggestion_status(suggestion_id, state.status)
    return state
