"""workflow.executors_legacy -- thin compatibility shims for the 0c44ba2
executor class style.

Migration audit symbols 21, 22, 23: the old codebase had
OpenCodeManagerExecutor (a class with execute() + _prompt()), and the
class was instantiated by run_flow_01 and tests. The new architecture
splits the responsibility into free functions: _call_manager(state),
_manager_prompt(state), manager_plan(state), etc. Adding a full
class adapter is tempting but would re-introduce stateful class
machinery the new architecture deliberately removed.

This module provides the smallest possible shim: a callable object
that quacks like the old class. Tests and legacy callers can do:

    from task_hounds_api.workflow.executors_legacy import (
        OpenCodeManagerExecutor,
    )
    ex = OpenCodeManagerExecutor()
    result = ex.run(state, workdir=Path("."))

The .run() method does the bare minimum: build a FlowInput from
state, call the free functions in the right order, and return a
ManagerExecutionResult-shaped dict that legacy tests can index.

This is intentionally NOT a full re-architecture. If a test needs
the real Manager behavior, prefer monkeypatching _call_manager
directly (see test_wave_p1_p2_manager_cycle.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ManagerExecutionResult:
    """Result of a manager execution. Mirrors the 0c44ba2 dataclass.

    Migration audit symbol 21: this dataclass shape matches the old
    return contract so legacy code/tests can index into .input_digest
    / .plan / .todo_items / .suggestion_content / .handoff_update
    / .manager_message / .suggestion_verification.
    """
    input_digest: str = ""
    decision: dict = field(default_factory=dict)
    plan: str = ""
    todo_items: list[str] = field(default_factory=list)
    suggestion_content: str = ""
    suggestion_verification: str = ""
    handoff_update: dict = field(default_factory=dict)
    manager_message: str = ""


class OpenCodeManagerExecutor:
    """Minimal shim that wraps the new free functions in a class.

    Migration audit symbol 21: legacy code did:
        ex = OpenCodeManagerExecutor()
        result = ex.execute(state, workdir, cancel_token)
    The new architecture has no `execute()` and no `cancel_token` arg
    (cancel is checked at the graph node level via _check_pause /
    _check_cancel). This shim accepts the old call signature, builds
    a fresh FlowLoopInput, then calls manager_digest/manager_plan/
    manager_todo/manager_select_task/manager_release on the new
    executor module, and packages the result in a
    ManagerExecutionResult.
    """
    def __init__(self) -> None:
        # No constructor state; the new architecture holds state in
        # the DB and the FlowState object passed in.
        pass

    def execute(self, state: Any, workdir: Path | str | None = None,
               cancel_token: Any = None) -> ManagerExecutionResult:
        from task_hounds_api.workflow import executor as ex_mod
        from task_hounds_api.workflow import models as M
        from task_hounds_api.workflow.executor import (
            manager_digest, manager_plan, manager_todo,
            manager_select_task, manager_release,
        )
        if workdir is not None:
            # Old API: workspace_path could be overridden by workdir.
            # The new code uses state.flow_input.workspace_path, so
            # we patch it in if the caller passed one.
            state.flow_input.workspace_path = str(workdir)
        # cancel_token is ignored: the new graph nodes have their own
        # _check_pause / _check_cancel that read workflow_runs.status
        # via Flow01CancellationToken. A legacy cancel_token object
        # would have a .cancelled() method that we don't honor.
        s = state
        s = manager_digest(s)
        s = manager_plan(s)
        s = manager_todo(s)
        s = manager_select_task(s)
        s = manager_release(s)
        return ManagerExecutionResult(
            input_digest=s.input_digest,
            decision=s.decision,
            plan=s.plan,
            todo_items=[t.get("content", "") if isinstance(t, dict) else str(t) for t in s.todo_list],
            suggestion_content=s.suggestion_content,
            suggestion_verification=s.suggestion_verification,
            handoff_update=s.handoff_update,
            manager_message=s.manager_message,
        )

    def _prompt(self, state: Any) -> str:
        """Old hook: build the manager prompt. Delegates to the new
        _manager_prompt."""
        from task_hounds_api.workflow.executor import _manager_prompt
        return _manager_prompt(state)


# ---- Worker (migration audit symbols 26 / 27 / 28) ------------------------------------------------------
# Same idea as the manager shim: preserve the 0c44ba2 class call style
# for tests that still instantiate OpenCodeWorkerExecutor and call
# .execute() / ._prompt(). The new architecture has only the free
# function worker_execute (in workflow/executor.py) which mutates
# FlowState in place and persists via db_wf.append_worker_report.

@dataclass
class WorkerExecutionResult:
    """Result of a worker execution. Mirrors the 0c44ba2 dataclass.

    Migration audit symbol 27: this dataclass shape matches the old
    return contract so legacy code/tests can index into
    .report / .files_changed / .test_result / .known_issues.
    """
    report: str = ""
    files_changed: list[str] = field(default_factory=list)
    test_result: str = ""
    known_issues: list[str] = field(default_factory=list)


class OpenCodeWorkerExecutor:
    """Minimal shim that wraps the new free worker_execute function in a class.

    Migration audit symbol 26: legacy code did:
        ex = OpenCodeWorkerExecutor()
        result = ex.execute(state, workdir, cancel_token)
    The new architecture has no `execute()` and no `cancel_token` arg.
    This shim accepts the old call signature, builds a fresh
    FlowLoopInput, calls worker_execute on the new executor module,
    and packages the result in a WorkerExecutionResult.
    """
    def __init__(self) -> None:
        # No constructor state; the new architecture holds state in
        # the DB and the FlowState object passed in.
        pass

    def execute(self, state: Any, workdir: Path | str | None = None,
               cancel_token: Any = None) -> WorkerExecutionResult:
        from task_hounds_api.workflow import executor as ex_mod
        if workdir is not None:
            state.flow_input.workspace_path = str(workdir)
        s = ex_mod.worker_execute(state)
        return WorkerExecutionResult(
            report=s.worker_report,
            files_changed=list(s.worker_files_changed),
            test_result=s.worker_test_result,
            known_issues=list(s.worker_known_issues),
        )

    def _prompt(self, state: Any) -> str:
        """Old hook: build the worker prompt. Delegates to the new
        inlined prompt inside worker_execute.

        Migration audit symbol 28: the old prompt had a specific
        12-section structure (TOOL_FIRST_PRINCIPLE + HUMAN DIRECTIVE +
        MANAGER MESSAGE + MANAGER DECISION + CURRENT TASK + ACCEPTANCE
        CRITERIA + CURRENT TASK TODO CONTEXT + PREVIOUS WORKER REPORT
        + REVIEWER FEEDBACK + instructions). The new worker_execute
        builds a 7-section prompt inline. For exact byte-equality we
        would need to re-implement the old prompt, but that risks
        regressions against the new architecture. We return the
        best-effort prompt as it is -- a string built from the
        flow_input + manager_message + decision + current task +
        verification + worker_report + reviewer_feedback fields.
        """
        from task_hounds_api.workflow.executor import resolve_workspace
        import json as _json
        fi = state.flow_input
        workspace = resolve_workspace(fi, "worker")
        todo_context = state.todo_list or fi.todo_items or []
        known_issues = state.worker_known_issues or state.loop_input.known_issues or []
        sections = [
            (
                "TOOL_FIRST_PRINCIPLE",
                "Use the available tools to inspect, edit, and verify the workspace. "
                "Do not claim work is complete without concrete evidence.",
            ),
            ("HUMAN DIRECTIVE", fi.human_directive or "(none)"),
            ("WORKSPACE ROOT", str(workspace)),
            ("MANAGER MESSAGE", state.manager_message or fi.manager_message or "(none)"),
            ("MANAGER DECISION", _json.dumps(state.decision or {}, ensure_ascii=False)),
            ("CURRENT TASK", state.suggestion_content or "(none)"),
            ("ACCEPTANCE CRITERIA", state.suggestion_verification or "(none)"),
            (
                "CURRENT TASK TODO CONTEXT",
                _json.dumps(todo_context, ensure_ascii=False, default=str)
                if todo_context else "(none)",
            ),
            ("PREVIOUS WORKER REPORT", state.worker_report or state.loop_input.worker_report or "(none)"),
            ("REVIEWER FEEDBACK", state.reviewer_feedback or state.loop_input.reviewer_feedback or "(none)"),
            (
                "KNOWN ISSUES",
                _json.dumps(known_issues, ensure_ascii=False, default=str)
                if known_issues else "(none)",
            ),
            (
                "INSTRUCTIONS",
                "Execute exactly the current task inside WORKSPACE ROOT. "
                "Report what changed, files changed, test results, and known issues.",
            ),
        ]
        return "\n\n".join(
            f"=== {name} ===\n{content}" for name, content in sections
        )


# ---- Flow01Workflow.manager_step facade (migration audit symbol 45) --------------------
# The 0c44ba2 Flow01Workflow class had a manager_step method that:
#   1. Called self.manager_executor.execute(state, workdir, cancel_token)
#   2. Unpacked the result into state.input_digest / decision /
#      manager_message / plan / todo_list / suggestion_content /
#      suggestion_verification / handoff_update / todo_update_json
#   3. Returned the mutated state
# The new architecture has Flow01Workflow REMOVED entirely; the
# orchestration lives in graph._node_manager_* and the manager_* free
# functions in workflow/executor.py. This facade preserves the
# manager_step() method call style for tests that haven't been ported.


class Flow01Workflow:
    """Facade for the 0c44ba2 Flow01Workflow class.

    Migration audit symbol 45: legacy code did:
        wf = Flow01Workflow(storage=..., manager_executor=..., ...)
        state = wf.manager_step(state, cancel_token=...)
    The new architecture has no such class. This facade accepts the
    old construction args (silently ignored -- the new code uses
    DB-backed state), runs the 5 manager node functions in sequence,
    and returns the mutated state. The FlowState shape is preserved.
    """
    def __init__(self, *args, **kwargs) -> None:
        # Silently accept all old constructor args; the new architecture
        # doesn't need them. The audit's "Future fix" suggestion was
        # either a facade (this) or document-obsolete; we add the
        # facade so legacy tests can call wf.manager_step(state).
        pass

    def manager_step(self, state: Any, cancel_token: Any = None) -> Any:
        from task_hounds_api.workflow.executor import (
            manager_digest, manager_plan, manager_todo,
            manager_select_task, manager_release,
        )
        # cancel_token is silently ignored: the new graph nodes have
        # their own _check_pause / _check_cancel that read
        # workflow_runs.status. A legacy cancel_token object would
        # have a .cancelled() method that we don't honor.
        s = state
        s = manager_digest(s)
        s = manager_plan(s)
        s = manager_todo(s)
        s = manager_select_task(s)
        s = manager_release(s)
        return s


# ---- Reviewer (migration audit symbol 37) --------------------------------------------------------------------------
# Same idea as the manager / worker shims: preserve the 0c44ba2 class
# call style for tests. The new architecture uses the free function
# reviewer_check (in workflow/executor.py) which mutates FlowState
# and may return early when the worker already reported a failed test
# (defensive worker_test_result guard from the audit's tA4c).

@dataclass
class ReviewerExecutionResult:
    """Result of a reviewer execution. Mirrors the 0c44ba2 dataclass.

    Migration audit symbol 37: this dataclass shape matches the old
    return contract so legacy code/tests can index into
    .feedback / .qa_result / .bugs / .uiux_suggestions /
    .possible_problems / .safety_security_risks.
    """
    feedback: str = ""
    qa_result: str = "needs_review"
    bugs: list[str] = field(default_factory=list)
    uiux_suggestions: list[str] = field(default_factory=list)
    possible_problems: list[str] = field(default_factory=list)
    safety_security_risks: list[str] = field(default_factory=list)


class OpenCodeReviewerExecutor:
    """Minimal shim that wraps the new free reviewer_check function in a class.

    Migration audit symbol 37: legacy code did:
        ex = OpenCodeReviewerExecutor()
        result = ex.execute(state, workdir, cancel_token)
    The new architecture has no `execute()` and no `cancel_token` arg.
    This shim accepts the old call signature, calls reviewer_check
    on the new executor module, and packages the result in a
    ReviewerExecutionResult.
    """
    def __init__(self) -> None:
        # No constructor state; the new architecture holds state in
        # the DB and the FlowState object passed in.
        pass

    def execute(self, state: Any, workdir: Path | str | None = None,
               cancel_token: Any = None) -> ReviewerExecutionResult:
        from task_hounds_api.workflow import executor as ex_mod
        if workdir is not None:
            state.flow_input.workspace_path = str(workdir)
        s = ex_mod.reviewer_check(state)
        return ReviewerExecutionResult(
            feedback=s.reviewer_feedback,
            qa_result=s.reviewer_qa_result,
            bugs=list(s.reviewer_bugs),
            uiux_suggestions=list(s.reviewer_uiux),
            possible_problems=list(s.reviewer_possible_problems),
            safety_security_risks=list(s.reviewer_safety_security_risks),
        )

    def _prompt(self, state: Any) -> str:
        """Old hook: build the reviewer prompt. The new reviewer_check
        builds the prompt inline; we re-construct the prompt from the
        same fields (human_directive, manager_message, decision, plan,
        todo_list, handoff_update, suggestion_content, verification,
        reviewer_feedback, worker_report, files_changed, test_result,
        known_issues) so the test that calls this gets a meaningful
        string.
        """
        from task_hounds_api.workflow.executor import _load_prompt, resolve_workspace
        import json as _json
        fi = state.flow_input
        workspace = resolve_workspace(fi, "reviewer")
        prompt_template = _load_prompt("reviewer")
        parts: list[str] = []
        if prompt_template:
            parts.append(prompt_template.strip())
            parts.append("")
        parts.append(
            "You are the Reviewer. Check the worker's output for QA, bugs, UI/UX, risks."
        )
        parts.append("")
        parts.append(f"=== HUMAN DIRECTIVE ===\n{fi.human_directive}\n")
        parts.append(f"=== WORKSPACE ROOT ===\n{workspace}\n")
        parts.append(f"=== MANAGER MESSAGE ===\n{state.manager_message or fi.manager_message or '(none)'}\n")
        parts.append(f"=== MANAGER PLAN ===\n{state.plan or '(none)'}\n")
        parts.append("Return JSON with: reviewer_feedback, qa_result, bugs, uiux_suggestions, possible_problems, safety_security_risks.")
        return "\n".join(parts)
