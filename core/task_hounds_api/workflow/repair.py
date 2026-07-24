"""workflow.repair — parser/repair helpers ported from pre-rebuild manager.py.

The pre-rebuild code (0c44ba2:core/power_teams/agents/manager.py + base.py)
had four helpers that the audit flagged as missing:

  - _extract_section(text, tag)   -> parse <TAG>...</TAG> blocks
  - repair_mojibake(text)         -> fix common UTF-8 decode issues
  - apply_handoff_update(text)    -> parse HANDOFF_UPDATE block, persist
  - handoff_summary(handoff)      -> human-readable handoff rendering
  - parse_prompt_tokens(text)     -> extract all XML-tagged blocks at once
  - repair_todo_json_text(block)  -> try to parse TODO block as JSON

These used to call send_to_agent() to re-prompt the LLM when parsing
failed. The new codebase parses required-keys JSON directly from the
manager response (see executor._call_manager), so the LLM-repair
loops are no longer needed at runtime. We keep the deterministic
parse/repair code so the prompt tokens (which the manager still emits
per agent_prompts/manager_prompts.md) are visible to the UI and to
any consumer that wants the old XML-block structure.
"""
from __future__ import annotations

import json
import re
from typing import Any

from task_hounds_api.db.ops import workflow as db_wf


# ── XML block parsers (replaces pre-rebuild _extract_section) ──────────────

_SECTION_RE = re.compile(
    r"<([A-Z][A-Z0-9_]*)>\s*(.*?)\s*</\1>",
    re.DOTALL,
)


def _extract_section(text: str, tag: str) -> str:
    """Return the content of the first <TAG>...</TAG> block, or '' if absent."""
    if not text or not tag:
        return ""
    m = re.search(
        rf"<{re.escape(tag)}>\s*(.*?)\s*</{re.escape(tag)}>",
        text,
        re.DOTALL,
    )
    return m.group(1).strip() if m else ""


def parse_prompt_tokens(text: str) -> dict[str, str]:
    """Extract all XML-tagged blocks from a manager response.

    Returns a dict mapping tag name (uppercase) to the inner content.
    The full set of tags the manager_prompts.md asks the LLM to emit:
      MANAGER_MESSAGE, PLAN, TODO_LIST, TODO_UPDATE_JSON,
      SUGGESTION_CONTENT, SUGGESTION_VERIFICATION, HANDOFF_UPDATE,
      DIRECTIVE_COMPLETE (self-closing), plus the bare token
      TASK_HOUNDS_STOP_LOOP.

    Missing or empty blocks are simply absent from the returned dict.
    """
    if not text:
        return {}
    out: dict[str, str] = {}
    for m in _SECTION_RE.finditer(text):
        out[m.group(1)] = m.group(2).strip()
    # Self-closing <DIRECTIVE_COMPLETE/> sentinel
    if re.search(r"<DIRECTIVE_COMPLETE\s*/>", text):
        out["DIRECTIVE_COMPLETE"] = ""
    # Bare stop-loop token
    if "TASK_HOUNDS_STOP_LOOP" in text and "TASK_HOUNDS_STOP_LOOP" not in out:
        out["TASK_HOUNDS_STOP_LOOP"] = ""
    return out


# ── Mojibake repair (replaces pre-rebuild repair_mojibake) ─────────────────

_MOJI_REPLACEMENTS = (
    ("\u00e2\u20ac\u0153", '"'),  # smart double-quote left
    ("\u00e2\u20ac\u009d", '"'),  # smart double-quote right
    ("\u00e2\u20ac\u02dc", "'"),  # smart single-quote
    ("\u00e2\u20ac\u2122", "'"),  # smart single-quote
    ("\u00c2\u00a0", " "),        # nbsp mangled
    ("\u00e2\u20ac\u00a6", "..."),# ellipsis mangled
    ("\u00c2\u00b6", "¶"),
)


def repair_mojibake(value: str) -> str:
    """Fix common UTF-8 -> cp1252 decode issues from LLM output."""
    if not value:
        return value
    for bad, good in _MOJI_REPLACEMENTS:
        value = value.replace(bad, good)
    return value


# ── HANDOFF_UPDATE persistence (replaces pre-rebuild apply_handoff_update) ─


def apply_handoff_update(
    manager_response: str,
    updated_by: str = "manager",
    project_session_id: str | None = None,
) -> int | None:
    """Parse a HANDOFF_UPDATE block from the manager response and persist it.

    Returns the new handoff version, or None if no block was present or
    it was unparseable. The pre-rebuild version also called
    _upsert_handoff; here we use db_wf.upsert_handoff (same effective
    SQL) and rely on its version increment. The active project session
    is resolved via get_active_session(); callers that need to target a
    specific session should call db_wf.upsert_handoff directly.
    """
    from task_hounds_api.db.ops.project import get_active_session
    if project_session_id:
        session_id = project_session_id
    else:
        active = get_active_session()
        if not active:
            return None
        session_id = active["id"]

    raw = _extract_section(manager_response or "", "HANDOFF_UPDATE")
    if not raw and "</HANDOFF_UPDATE>" in (manager_response or ""):
        # The LLM sometimes forgets the opening tag; recover by taking
        # the last balanced JSON object before the closing tag.
        before = manager_response.split("</HANDOFF_UPDATE>", 1)[0]
        start = before.rfind("{")
        if start != -1:
            raw = before[start:].strip()
    if not raw:
        return None
    raw = repair_mojibake(raw)

    fields: dict[str, Any] = {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            fields = {k: v for k, v in data.items() if v is not None}
    except json.JSONDecodeError:
        # Fall back: treat the whole block as free-text current_task
        fields = {"current_task": raw}
    if not fields:
        return None

    db_wf.upsert_handoff(session_id, **{**fields, "updated_by": updated_by})
    handoff = db_wf.get_handoff(session_id)
    return handoff["version"] if handoff else None


# ── Handoff summary (replaces pre-rebuild handoff_summary) ─────────────────


_HANDOFF_TEXT_FIELDS = (
    "human_requirements",
    "working_direction",
    "file_structure",
    "important_files",
    "available_scripts",
    "existing_solutions",
    "references_demos",
    "macro_flow",
    "current_task",
    "known_bugs",
    "tested_files",
    "completion_criteria",
    "human_concerns",
    "project_folder_location",
)
_HANDOFF_LIST_FIELDS = (
    "current_micro_flow",
)
_HANDOFF_LABELS = {
    "human_requirements": "Human Requirements",
    "working_direction": "Working Direction",
    "file_structure": "File Structure",
    "important_files": "Important Files",
    "available_scripts": "Available Scripts",
    "existing_solutions": "Existing Solutions",
    "references_demos": "References/Demos",
    "macro_flow": "Macro Flow (Phases)",
    "current_task": "Current Task",
    "current_micro_flow": "Current Micro Flow",
    "known_bugs": "Known Bugs",
    "tested_files": "Tested Files",
    "completion_criteria": "Completion Criteria",
    "human_concerns": "Human Concerns",
    "project_folder_location": "Project Folder",
}


def handoff_summary(handoff: dict | None) -> str:
    """Build a compact human-readable summary from a handoff DB row."""
    if handoff is None:
        return "(no project handoff yet)"
    parts = [f"=== PROJECT HANDOFF v{handoff.get('version', '?')} ==="]

    def _render_text(key: str) -> None:
        val = handoff.get(key)
        if not val:
            return
        parts.append(f"\n{_HANDOFF_LABELS.get(key, key)}: {val}")

    def _render_list(key: str) -> None:
        val = handoff.get(key)
        if not val:
            return
        try:
            parsed = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            parts.append(f"\n{_HANDOFF_LABELS.get(key, key)}: {val}")
            return
        if isinstance(parsed, list) and parsed:
            parts.append(f"\n{_HANDOFF_LABELS.get(key, key)}:")
            for idx, item in enumerate(parsed, start=1):
                if isinstance(item, dict):
                    parts.append(f"  {idx}. " + "; ".join(f"{k}: {v}" for k, v in item.items()))
                else:
                    parts.append(f"  {idx}. {item}")

    for k in _HANDOFF_TEXT_FIELDS:
        _render_text(k)
    for k in _HANDOFF_LIST_FIELDS:
        _render_list(k)

    return "\n".join(parts)


# ── TODO JSON repair (replaces pre-rebuild _repair_todo_json) ──────────────


def repair_todo_json_text(todo_block: str) -> dict | None:
    """Try to parse a TODO_LIST block as the required TODO_UPDATE_JSON shape.

    The pre-rebuild version called send_to_agent() to re-prompt the LLM
    when parsing failed. The new architecture does that re-prompt as
    part of the manager graph loop (see executor.manager_plan), so this
    function is the deterministic fallback: try direct JSON parse, then
    try markdown-style bullet extraction, then give up and return None.

    Returns the parsed dict (shape: {"items": [...]}) on success, or
    None if no interpretation works.
    """
    block = (todo_block or "").strip()
    if not block:
        return None

    # 1) Direct JSON parse
    try:
        data = json.loads(block)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
        if isinstance(data, list):
            return {"items": data}
    except json.JSONDecodeError:
        pass

    # 2) Extract first JSON object from the block
    start = block.find("{")
    end = block.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(block[start : end + 1])
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
        except json.JSONDecodeError:
            pass

    # 3) Markdown bullet extraction: "- [ ] do thing" / "- [x] done"
    bullet_re = re.compile(
        r"^\s*[-*]\s*\[(?P<mark>[ xX→✓✗])\]\s*(?P<content>.+?)\s*$",
        re.MULTILINE,
    )
    status_map = {
        " ": "pending", "x": "completed", "X": "completed",
        "→": "in_progress", "✓": "completed", "✗": "blocked",
    }
    items = []
    for idx, m in enumerate(bullet_re.finditer(block)):
        items.append({
            "id": None,
            "content": m.group("content").strip(),
            "status": status_map.get(m.group("mark"), "pending"),
            "priority": "medium",
            "position": idx,
        })
    if items:
        return {"items": items}

    # 4) Single-line fallback: treat the whole block as one todo
    if block and "\n" not in block.strip():
        return {
            "items": [{
                "id": None,
                "content": block,
                "status": "pending",
                "priority": "medium",
                "position": 0,
            }],
        }
    return None


# Migration audit symbol 126: the 0c44ba2 manager.py had a private
# _section_block(name, content) -> str helper that wrapped text in an
# XML block. The new architecture doesn't use it (LLMs are expected to
# emit the XML blocks via the manager prompt instructions), so the
# helper is GONE from the core code path. We expose it here as a
# public utility for tests and any callers that want to programmatically
# build a section block (e.g., when synthesizing a test fixture or a
# prompt template that needs the same wrap). The shape is byte-identical
# to the 0c44ba2 implementation.
def section_block(name: str, content: str) -> str:
    """Wrap `content` in an XML block with the given tag name.

    Migration audit symbol 126: the 0c44ba2 helper was:
        def _section_block(name, content):
            return f"\\n\\n<{name}>\\n{content.strip()}\\n</{name}>\\n"
    This public version preserves the exact byte-level shape so any
    code or test that fed `_section_block("HANDOFF_UPDATE", json_text)`
    into a parser gets an identical string back.
    """
    return f"\n\n<{name}>\n{content.strip()}\n</{name}>\n"


# Migration audit symbol 129: the 0c44ba2 manager.py had a private
# _is_valid_handoff_json(raw) -> bool helper that returned True only
# if the input was a valid JSON object (dict). The new architecture
# inlines this check inside apply_handoff_update, so the named helper
# is GONE from the core code path. We expose it here as a public
# utility so tests and any callers that want to pre-validate a
# handoff JSON string can do so without re-implementing the logic.
def is_valid_handoff_json(raw: str) -> bool:
    """Return True if `raw` parses as a JSON object, False otherwise.

    Migration audit symbol 129: the 0c44ba2 helper was:
        def _is_valid_handoff_json(raw):
            try:
                data = json.loads(raw or "")
            except json.JSONDecodeError:
                return False
            return isinstance(data, dict)
    This public version preserves the exact same boolean contract
    (empty string / non-JSON / non-dict -> False; JSON object -> True).
    """
    try:
        data = json.loads(raw or "")
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(data, dict)


# ── ID 29 compat: _current_task_todos ────────────────────────────────────
# The 0c44ba2 OpenCodeWorkerExecutor._current_task_todos(state) selected
# todo items for the worker prompt by drilling into state.suggestion_content
# and state.todo_list. The new architecture reads the active suggestion and
# prompt context directly from DB (see executor.worker_execute); the named
# helper is gone from the executor.
# This compat helper preserves the old selection logic for callers that
# need to reconstruct the legacy prompt context.
# P10 audit id 29: do not restore product-decision items; pin only.
def current_task_todos_from_state(state) -> list[dict]:
    """P10 id 29 (legacy compat): return the todo items the old
    _current_task_todos would have selected for the worker prompt.

    Old logic:
      - if todo_list empty: return []
      - if suggestion_content set: filter todo_list to items whose
        content contains the suggestion keywords (case-insensitive),
        or fall back to the first pending/in-progress item
      - if no suggestion_content: return the first todo item
      - items always include id/session_id/parent_id/content/status/priority/owner/position

    The new executor.worker_execute does NOT use this; it reads the
    active suggestion from DB directly. This helper exists so callers
    that need to replicate the old selection behavior can import it
    from workflow.repair.
    """
    if not state.todo_list:
        return []
    suggestion = (getattr(state, "suggestion_content", "") or "").strip().lower()
    pending_statuses = {"pending", "in_progress"}
    if suggestion:
        matched = [
            t for t in state.todo_list
            if isinstance(t, dict)
            and suggestion in (t.get("content") or "").lower()
        ]
        if matched:
            return matched[:1]
        pending = [
            t for t in state.todo_list
            if isinstance(t, dict) and t.get("status") in pending_statuses
        ]
        return [pending[0]] if pending else []
    first = state.todo_list[0]
    if isinstance(first, dict):
        return [first]
    return []


# ── Migration audit P7 compat helpers (ids 14, 18, 31) ────────────────────
# The 0c44ba2 manager.py had three small LLM-prompt helpers that
# the new architecture removed. They live here as opt-in compat
# helpers; the authoritative new helpers in executor.py keep
# their current behavior. Callers that need the legacy shape
# (drill-down, multi-fence scoring, known_issues filtering) can
# import from workflow.repair.

_STRINGIFY_DRILL_KEYS: tuple[str, ...] = (
    "content", "manager_message", "message", "task", "title", "summary",
)


def stringify_manager_field(value) -> str:
    """P7 id 14 (legacy compat): recursively drill dicts via a fixed
    key list, return the first match's string. For non-dict values
    returns str(value).strip(). Mirror of the 0c44ba2
    _stringify_manager_field helper."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for k in _STRINGIFY_DRILL_KEYS:
            if k in value and value[k] is not None:
                inner = value[k]
                if isinstance(inner, dict):
                    return stringify_manager_field(inner)
                if isinstance(inner, list):
                    return "\n".join(str(x) for x in inner)
                return str(inner).strip()
        return str(value).strip()
    if isinstance(value, list):
        return "\n".join(str(x) for x in value)
    if isinstance(value, str):
        return value.strip()
    return str(value)


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _score_block(candidate: str, required: set[str]) -> int:
    """Return the count of required keys present in the (parses-as) JSON
    candidate. Returns -1 if the candidate does not parse as a JSON
    object (so non-JSON fences are scored below the worst valid block).
    """
    try:
        obj = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return -1
    if not isinstance(obj, dict):
        return -1
    return len(required & set(obj.keys()))


def extract_json_object_strict(
    text: str, required_keys: set[str]
) -> dict | None:
    """P7 id 18 (legacy compat): score all fenced JSON blocks by
    required_keys coverage and return the best match. Returns None
    on parse failure / missing required keys (vs. the new
    extract_json_object which raises). Mirror of the 0c44ba2
    _extract_json_object helper.
    """
    if not text:
        return None
    candidates: list[str] = []
    for m in _FENCE_RE.finditer(text):
        candidates.append(m.group(1))
    if not candidates:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])
    best: dict | None = None
    best_score = -1
    for cand in candidates:
        score = _score_block(cand, required_keys)
        if score > best_score:
            try:
                obj = json.loads(cand)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict) and score == len(required_keys & set(obj.keys())):
                best = obj
                best_score = score
            elif isinstance(obj, dict) and best is None:
                best = obj
                best_score = score
    return best


_KNOWN_ISSUE_NEGATIVE_MARKERS = (
    "none", "n/a", "no known issues", "no issues", "no issue",
    "nothing", "no problems", "-", "—",
)
_KNOWN_ISSUE_KEYWORDS = (
    "issue", "blocked", "fail", "error", "problem", "todo",
    "warning", "bug", "stuck", "concern",
)
_KNOWN_ISSUE_MAX = 5


def extract_known_issues(raw_text: str) -> list[str]:
    """P7 id 31 (legacy compat): deterministic regex-based
    extractor. For each non-empty line, strip leading bullet
    markers, drop lines that match the negative markers list,
    keep lines that match at least one keyword, cap at 5.
    Returns [] for empty / no-input. Mirror of the 0c44ba2
    OpenCodeWorkerExecutor._extract_known_issues helper."""
    if not raw_text:
        return []
    out: list[str] = []
    for line in raw_text.splitlines():
        s = line.strip()
        if not s:
            continue
        s = s.lstrip("-*•· ").lstrip()
        if not s:
            continue
        low = s.lower()
        if any(m in low for m in _KNOWN_ISSUE_NEGATIVE_MARKERS):
            continue
        if any(k in low for k in _KNOWN_ISSUE_KEYWORDS):
            out.append(s)
        if len(out) >= _KNOWN_ISSUE_MAX:
            break
    return out
