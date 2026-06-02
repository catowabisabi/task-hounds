"""
Importable flow_01 workflow.

The flow can run as a plain Python object today. If langgraph is installed,
`build_langgraph()` exposes the same Manager -> Worker -> Reviewer sequence as
a compiled graph without making langgraph a hard dependency.
"""
from __future__ import annotations

import subprocess
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .adapters import FastApiServiceSignalAdapter, FlowSignalAdapter
from .interface import (
    DB_PATH,
    FlowInput,
    FlowLimits,
    FlowLoopInput,
    FlowOutput,
    FlowRoleOutput,
    FlowState,
    FlowStorage,
    UIManagerMessage,
    UIPlanData,
    UISuggestion,
    UITodoItem,
    validate_flow_input,
)


def _state_from_raw(raw_state: dict) -> FlowState:
    if isinstance(raw_state.get("flow_input"), FlowInput):
        return FlowState(**raw_state)
    return FlowState(
        flow_input=FlowInput(**raw_state["flow_input"]),
        loop_input=FlowLoopInput(**raw_state["loop_input"]),
        status=raw_state.get("status", "pending"),
        input_digest=raw_state.get("input_digest", ""),
        decision=raw_state.get("decision", {}),
        plan=raw_state.get("plan", ""),
        todo_list=raw_state.get("todo_list", []),
        todo_update_json=raw_state.get("todo_update_json", {}),
        suggestion_content=raw_state.get("suggestion_content", ""),
        suggestion_verification=raw_state.get("suggestion_verification", ""),
        handoff_update=raw_state.get("handoff_update", {}),
    )


@dataclass
class ManagerExecutionResult:
    input_digest: str
    decision: dict[str, Any]
    plan: str
    todo_items: list[str]
    suggestion_content: str
    suggestion_verification: str
    handoff_update: dict[str, Any]
    manager_message: str


@dataclass
class WorkerExecutionResult:
    report: str
    files_changed: list[str]
    test_result: str
    known_issues: list[str]


@dataclass
class ReviewerExecutionResult:
    feedback: str
    qa_result: str
    bugs: list[str]
    uiux_suggestions: list[str]
    possible_problems: list[str]
    safety_security_risks: list[str]


class CancellationToken(Protocol):
    def cancelled(self) -> bool: ...


class ManagerExecutor(Protocol):
    def execute(self, state: FlowState, workdir: Path, cancel_token: CancellationToken | None = None) -> ManagerExecutionResult: ...


class WorkerExecutor(Protocol):
    def execute(self, state: FlowState, workdir: Path, cancel_token: CancellationToken | None = None) -> WorkerExecutionResult: ...


class ReviewerExecutor(Protocol):
    def execute(self, state: FlowState, workdir: Path, cancel_token: CancellationToken | None = None) -> ReviewerExecutionResult: ...


def _extract_json_object(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else ""
    if not raw:
        start = text.find("{")
        end = text.rfind("}")
        raw = text[start : end + 1] if start >= 0 and end > start else ""
    if not raw:
        return {}
    try:
        import json

        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


class LocalManagerExecutor:
    """Deterministic Manager for offline contract checks."""

    def execute(self, state: FlowState, workdir: Path, cancel_token: CancellationToken | None = None) -> ManagerExecutionResult:
        flow_input = state.flow_input
        loop_input = state.loop_input
        thoughts = flow_input.human_new_thought_and_suggestion.strip()
        suggested_task = flow_input.human_suggested_new_task_or_item.strip()
        previous_feedback = loop_input.reviewer_feedback.strip()
        input_digest = (
            f"Directive: {flow_input.human_directive.strip()}\n"
            f"Manager message: {flow_input.manager_message.strip() or '(none)'}\n"
            f"Human thought: {thoughts or '(none)'}\n"
            f"Suggested task: {suggested_task or '(none)'}\n"
            f"Previous reviewer feedback: {previous_feedback or '(none)'}"
        )
        next_task = suggested_task or (flow_input.todo_items[0] if flow_input.todo_items else "Clarify the first useful implementation step")
        decision = {
            "previous_step_status": "pass" if not loop_input.known_issues else "fail",
            "next_action_type": "bugfix" if loop_input.known_issues else "continue",
            "reason": "Use reviewer feedback before opening new work" if previous_feedback else "Start with the highest-value available task",
        }
        return ManagerExecutionResult(
            input_digest=input_digest,
            decision=decision,
            plan=f"Loop {loop_input.loop_index}: advance the directive through one controlled worker task.",
            todo_items=flow_input.todo_items or [next_task],
            suggestion_content=next_task,
            suggestion_verification="Worker reports files changed, test result, and known issues.",
            handoff_update={
                "current_task": next_task,
                "current_micro_flow": ["manager_digest", "worker_execute", "reviewer_feedback"],
                "completion_criteria": ["Worker reports files changed, test result, and known issues."],
            },
            manager_message=f"Manager selected next task: {next_task}",
        )


class OpenCodeManagerExecutor:
    """Run the bound Manager role through send_to_agent()."""

    def execute(self, state: FlowState, workdir: Path, cancel_token: CancellationToken | None = None) -> ManagerExecutionResult:
        from power_teams.agents.base import send_to_agent

        if cancel_token and cancel_token.cancelled():
            raise RuntimeError("Manager execution cancelled before start")
        workspace_path = Path(state.flow_input.workspace_path or workdir)
        if not workspace_path.exists():
            workspace_path = workdir
        fallback = LocalManagerExecutor().execute(state, workdir, cancel_token=cancel_token)
        reply = send_to_agent("manager", self._prompt(state), max_retries=0, cwd=str(workspace_path))
        if cancel_token and cancel_token.cancelled():
            raise RuntimeError("Manager execution cancelled after manager returned")
        data = _extract_json_object(reply)
        todo_items = data.get("todo_list") or data.get("todos") or fallback.todo_items
        if not isinstance(todo_items, list):
            todo_items = fallback.todo_items
        todo_items = [str(item).strip() for item in todo_items if str(item).strip()]
        suggestion_content = str(data.get("suggestion_content") or data.get("worker_task") or fallback.suggestion_content).strip()
        return ManagerExecutionResult(
            input_digest=str(data.get("input_digest") or fallback.input_digest),
            decision=data.get("decision") if isinstance(data.get("decision"), dict) else fallback.decision,
            plan=str(data.get("plan") or fallback.plan),
            todo_items=todo_items or [suggestion_content],
            suggestion_content=suggestion_content,
            suggestion_verification=str(data.get("suggestion_verification") or fallback.suggestion_verification),
            handoff_update=data.get("handoff_update") if isinstance(data.get("handoff_update"), dict) else fallback.handoff_update,
            manager_message=str(data.get("manager_message") or reply.strip() or fallback.manager_message),
        )

    def _prompt(self, state: FlowState) -> str:
        flow_input = state.flow_input
        loop_input = state.loop_input
        return (
            "You are the Manager agent inside Task Hounds flow_01.\n"
            "Digest the Human Directive, todo state, previous Worker report, and Reviewer feedback. "
            "Choose exactly one executable Worker task. Do not implement files yourself.\n\n"
            "Return a JSON object inside one ```json fenced block with these keys:\n"
            "input_digest, decision, manager_message, plan, todo_list, suggestion_content, "
            "suggestion_verification, handoff_update.\n\n"
            "=== HUMAN_DIRECTIVE ===\n"
            f"{flow_input.human_directive}\n\n"
            "=== HUMAN_NEW_THOUGHT_AND_SUGGESTION ===\n"
            f"{flow_input.human_new_thought_and_suggestion or '(none)'}\n\n"
            "=== HUMAN_SUGGESTED_NEW_TASK_OR_ITEM ===\n"
            f"{flow_input.human_suggested_new_task_or_item or '(none)'}\n\n"
            "=== MANAGER MESSAGE HISTORY / HUMAN MESSAGE ===\n"
            f"{flow_input.manager_message or '(none)'}\n\n"
            "=== TODO STATE ===\n"
            + "\n".join(f"- {item}" for item in flow_input.todo_items)
            + "\n\n"
            "=== PREVIOUS WORKER_REPORT ===\n"
            f"{loop_input.worker_report or '(none)'}\n\n"
            "=== REVIEWER_FEEDBACK ===\n"
            f"{loop_input.reviewer_feedback or '(none)'}\n"
        )


class LocalFileWorkerExecutor:
    """Deterministic worker for tests and offline workflow contract checks."""

    def execute(self, state: FlowState, workdir: Path, cancel_token: CancellationToken | None = None) -> WorkerExecutionResult:
        if cancel_token and cancel_token.cancelled():
            raise RuntimeError("Worker execution cancelled before start")
        workdir.mkdir(parents=True, exist_ok=True)
        output_file = workdir / "worker-output.txt"
        output_file.write_text(
            (
                f"Task: {state.suggestion_content}\n"
                f"Loop: {state.loop_input.loop_index}\n"
                f"Directive: {state.flow_input.human_directive}\n"
            ),
            encoding="utf-8",
        )
        return WorkerExecutionResult(
            report=f"Worker executed loop {state.loop_input.loop_index}: {state.suggestion_content}",
            files_changed=[str(output_file)],
            test_result="passed",
            known_issues=[],
        )


class OpenCodeWorkerExecutor:
    """Run the existing bound Worker role through send_to_agent()."""

    def execute(self, state: FlowState, workdir: Path, cancel_token: CancellationToken | None = None) -> WorkerExecutionResult:
        from power_teams.agents.base import get_active_session_id, send_to_agent, worker_report_path, write_text
        from power_teams.db import add_worker_report

        if cancel_token and cancel_token.cancelled():
            raise RuntimeError("Worker execution cancelled before start")
        workspace_path = Path(state.flow_input.workspace_path or workdir)
        if not workspace_path.exists():
            workspace_path = workdir
        before = self._git_status(workspace_path)
        report = send_to_agent("worker", self._prompt(state), cwd=str(workspace_path))
        if cancel_token and cancel_token.cancelled():
            raise RuntimeError("Worker execution cancelled after worker returned")
        after = self._git_status(workspace_path)
        reported_files, outside_workspace_files = self._reported_file_evidence(report, workspace_path)
        files_changed = sorted((after or before) | reported_files)
        known_issues = self._extract_known_issues(report)
        if outside_workspace_files:
            known_issues.append(
                "Worker verified files outside the active workspace: " + ", ".join(sorted(outside_workspace_files))
            )
        if self._report_claims_file_change(report) and not files_changed:
            known_issues.append(
                "Worker report claimed file changes, but flow_01 could not verify any changed or existing reported file in the active workspace."
            )
        test_result = "passed" if not known_issues else "needs_review"

        session_id = get_active_session_id()
        if session_id:
            add_worker_report(session_id, report)
        write_text(worker_report_path(), f"# Worker Report\n\n{report}\n")

        return WorkerExecutionResult(
            report=report,
            files_changed=files_changed,
            test_result=test_result,
            known_issues=known_issues,
        )

    def _prompt(self, state: FlowState) -> str:
        flow_input = state.flow_input
        loop_input = state.loop_input
        return (
            "You are the Worker agent inside Task Hounds flow_01. Execute one controlled task.\n\n"
            "=== HUMAN DIRECTIVE ===\n"
            f"{flow_input.human_directive}\n\n"
            "=== MANAGER MESSAGE ===\n"
            f"{flow_input.manager_message or '(none)'}\n\n"
            "=== CURRENT TASK ===\n"
            f"{state.suggestion_content}\n\n"
            "=== TODO LIST ===\n"
            + "\n".join(f"- {item}" for item in flow_input.todo_items)
            + "\n\n"
            "=== PREVIOUS WORKER REPORT ===\n"
            f"{loop_input.worker_report or '(none)'}\n\n"
            "=== REVIEWER FEEDBACK ===\n"
            f"{loop_input.reviewer_feedback or '(none)'}\n\n"
            "Instructions:\n"
            "- Make the smallest useful implementation change that satisfies the current task.\n"
            "- Keep existing UI/UX contracts stable unless the task explicitly asks otherwise.\n"
            "- Run a relevant verification command when practical.\n"
            "- If you create or modify files, verify the exact path exists before reporting success.\n"
            "- End with a concise worker report containing: changes made, files changed, verification result, known issues.\n"
        )

    def _git_status(self, workspace_path: Path) -> set[str]:
        try:
            result = subprocess.run(
                ["git", "-C", str(workspace_path), "status", "--short"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
        except Exception:
            return set()
        if result.returncode != 0:
            return set()
        changed: set[str] = set()
        for line in result.stdout.splitlines():
            if len(line) >= 4:
                changed.add(line[3:].strip())
        return changed

    def _extract_known_issues(self, report: str) -> list[str]:
        issues: list[str] = []
        for line in report.splitlines():
            cleaned = line.strip("- *").strip()
            lowered = cleaned.lower()
            if not cleaned or lowered in {"none", "n/a", "no known issues"}:
                continue
            if lowered.rstrip(":") in {"known issues", "issues"}:
                continue
            if any(word in lowered for word in ("issue", "blocked", "failed")):
                issues.append(cleaned)
        return issues[:5]

    def _report_claims_file_change(self, report: str) -> bool:
        lowered = report.lower()
        return any(word in lowered for word in ("file", "created", "modified", "changed", "updated"))

    def _reported_file_evidence(self, report: str, workspace_path: Path) -> tuple[set[str], set[str]]:
        candidates = set(re.findall(r"`([^`]+\.[A-Za-z0-9_./\\-]+)`", report))
        existing: set[str] = set()
        outside_workspace: set[str] = set()
        try:
            workspace_resolved = workspace_path.resolve()
        except OSError:
            workspace_resolved = workspace_path
        for raw in candidates:
            candidate = Path(raw)
            path = candidate if candidate.is_absolute() else workspace_path / candidate
            try:
                if not path.exists():
                    continue
                resolved = path.resolve()
                try:
                    resolved.relative_to(workspace_resolved)
                    existing.add(str(resolved))
                except ValueError:
                    outside_workspace.add(str(resolved))
            except OSError:
                continue
        return existing, outside_workspace


class LocalReviewerExecutor:
    """Deterministic Reviewer for offline contract checks."""

    def execute(self, state: FlowState, workdir: Path, cancel_token: CancellationToken | None = None) -> ReviewerExecutionResult:
        return ReviewerExecutionResult(
            feedback="QA passed. No blocking bugs found. Continue with the next manager-selected task.",
            qa_result="pass",
            bugs=[],
            uiux_suggestions=[],
            possible_problems=[],
            safety_security_risks=[],
        )


class OpenCodeReviewerExecutor:
    """Run the bound Reviewer role through send_to_agent()."""

    def execute(self, state: FlowState, workdir: Path, cancel_token: CancellationToken | None = None) -> ReviewerExecutionResult:
        from power_teams.agents.base import send_to_agent

        if cancel_token and cancel_token.cancelled():
            raise RuntimeError("Reviewer execution cancelled before start")
        workspace_path = Path(state.flow_input.workspace_path or workdir)
        if not workspace_path.exists():
            workspace_path = workdir
        reply = send_to_agent("reviewer", self._prompt(state), max_retries=0, cwd=str(workspace_path))
        if cancel_token and cancel_token.cancelled():
            raise RuntimeError("Reviewer execution cancelled after reviewer returned")
        data = _extract_json_object(reply)
        fallback = LocalReviewerExecutor().execute(state, workdir, cancel_token=cancel_token)
        return ReviewerExecutionResult(
            feedback=str(data.get("reviewer_feedback") or data.get("feedback") or reply.strip() or fallback.feedback),
            qa_result=str(data.get("qa_result") or fallback.qa_result),
            bugs=self._list(data.get("bugs")),
            uiux_suggestions=self._list(data.get("uiux_suggestions")),
            possible_problems=self._list(data.get("possible_problems")),
            safety_security_risks=self._list(data.get("safety_security_risks")),
        )

    def _prompt(self, state: FlowState) -> str:
        return (
            "You are the Reviewer agent inside Task Hounds flow_01. Review the Worker's result. "
            "Do not implement files. Check QA, bugs, UI/UX issues, possible stuck states, messy user input, "
            "and safety/security risks.\n\n"
            "Return a JSON object inside one ```json fenced block with these keys:\n"
            "reviewer_feedback, qa_result, bugs, uiux_suggestions, possible_problems, safety_security_risks.\n\n"
            "=== HUMAN_DIRECTIVE ===\n"
            f"{state.flow_input.human_directive}\n\n"
            "=== MANAGER_MESSAGE ===\n"
            f"{state.flow_input.manager_message or state.suggestion_content or '(none)'}\n\n"
            "=== WORKER_TASK ===\n"
            f"{state.suggestion_content}\n\n"
            "=== WORKER_REPORT ===\n"
            f"{state.loop_input.worker_report or '(none)'}\n\n"
            "=== FILES_CHANGED ===\n"
            + "\n".join(f"- {path}" for path in state.loop_input.files_changed)
            + "\n\n"
            "=== TEST_RESULT ===\n"
            f"{state.loop_input.test_result or '(none)'}\n\n"
            "=== KNOWN_ISSUES ===\n"
            + "\n".join(f"- {issue}" for issue in state.loop_input.known_issues)
            + "\n"
        )

    def _list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []


class Flow01Workflow:
    """Baseline Human -> Manager -> Worker -> Reviewer flow."""

    def __init__(
        self,
        storage: FlowStorage | None = None,
        limits: FlowLimits | None = None,
        workdir: Path | None = None,
        signal_adapter: FlowSignalAdapter | None = None,
        manager_executor: ManagerExecutor | None = None,
        worker_executor: WorkerExecutor | None = None,
        reviewer_executor: ReviewerExecutor | None = None,
    ) -> None:
        self.storage = storage or FlowStorage(DB_PATH)
        self.limits = limits or FlowLimits()
        self.workdir = Path(workdir) if workdir else None
        self.signal_adapter = signal_adapter or FastApiServiceSignalAdapter()
        self.manager_executor = manager_executor or LocalManagerExecutor()
        self.worker_executor = worker_executor or LocalFileWorkerExecutor()
        self.reviewer_executor = reviewer_executor or LocalReviewerExecutor()

    def run_once(self, flow_input: FlowInput, loop_input: FlowLoopInput) -> FlowOutput:
        validate_flow_input(flow_input, self.limits)
        self.signal_adapter.loop_started(flow_input, loop_input.loop_index)
        state = FlowState(flow_input=flow_input, loop_input=loop_input, status="running")
        state = self.manager_step(state)
        state = self.worker_step(state)
        state = self.reviewer_step(state)
        output = self.to_output(state)
        self.storage.write_output(flow_input, output)
        self.signal_adapter.manager_completed(output)
        self.signal_adapter.worker_completed(output)
        self.signal_adapter.reviewer_completed(output)
        self.signal_adapter.loop_completed(output)
        return output

    def run_loops(self, flow_input: FlowInput, loops: int = 5) -> list[FlowOutput]:
        if loops < 1 or loops > self.limits.loop_max_iterations:
            raise ValueError(f"loops must be between 1 and {self.limits.loop_max_iterations}")
        outputs: list[FlowOutput] = []
        worker_report = ""
        files_changed: list[str] = []
        test_result = ""
        known_issues: list[str] = []
        reviewer_feedback = ""
        for index in range(1, loops + 1):
            loop_input = FlowLoopInput(
                loop_index=index,
                worker_report=worker_report,
                files_changed=files_changed,
                test_result=test_result,
                known_issues=known_issues,
                reviewer_feedback=reviewer_feedback,
            )
            output = self.run_once(flow_input, loop_input)
            outputs.append(output)
            worker_report = output.worker.content
            files_changed = output.worker.payload.get("files_changed", [])
            test_result = output.worker.payload.get("test_result", "")
            known_issues = output.worker.payload.get("known_issues", [])
            reviewer_feedback = output.reviewer.content
        return outputs

    def manager_step(self, state: FlowState, cancel_token: CancellationToken | None = None) -> FlowState:
        workdir = self.workdir or Path(".") / "test-dir"
        result = self.manager_executor.execute(state, workdir, cancel_token=cancel_token)
        state.input_digest = result.input_digest
        state.decision = result.decision
        state.plan = result.plan
        state.todo_list = [
            {
                "id": f"{state.flow_input.project_session_id}-todo-{pos}",
                "session_id": state.flow_input.project_session_id,
                "parent_id": None,
                "content": item,
                "status": "pending",
                "priority": "medium",
                "position": pos,
                "owner": "manager",
                "updated_at": None,
            }
            for pos, item in enumerate(result.todo_items or [result.suggestion_content])
        ]
        state.todo_update_json = {"items": state.todo_list}
        state.suggestion_content = result.suggestion_content
        state.suggestion_verification = result.suggestion_verification
        state.handoff_update = result.handoff_update
        return state

    def worker_step(self, state: FlowState, cancel_token: CancellationToken | None = None) -> FlowState:
        workdir = self.workdir or Path(".") / "test-dir"
        result = self.worker_executor.execute(state, workdir, cancel_token=cancel_token)
        state.loop_input.worker_report = result.report
        state.loop_input.files_changed = result.files_changed
        state.loop_input.test_result = result.test_result
        state.loop_input.known_issues = result.known_issues
        return state

    def reviewer_step(self, state: FlowState, cancel_token: CancellationToken | None = None) -> FlowState:
        workdir = self.workdir or Path(".") / "test-dir"
        result = self.reviewer_executor.execute(state, workdir, cancel_token=cancel_token)
        state.loop_input.reviewer_feedback = result.feedback
        state.reviewer_payload = {
            "qa_result": result.qa_result,
            "bugs": result.bugs,
            "uiux_suggestions": result.uiux_suggestions,
            "possible_problems": result.possible_problems,
            "safety_security_risks": result.safety_security_risks,
        }
        state.status = "completed"
        return state

    def to_output(self, state: FlowState) -> FlowOutput:
        manager_output = FlowRoleOutput(
            role="manager",
            loop_index=state.loop_input.loop_index,
            content=state.suggestion_content,
            payload={
                "input_digest": state.input_digest,
                "decision": state.decision,
                "plan": state.plan,
                "todo_update_json": state.todo_update_json,
                "handoff_update": state.handoff_update,
            },
        )
        worker_output = FlowRoleOutput(
            role="worker",
            loop_index=state.loop_input.loop_index,
            content=state.loop_input.worker_report,
            payload={
                "files_changed": state.loop_input.files_changed,
                "test_result": state.loop_input.test_result,
                "known_issues": state.loop_input.known_issues,
            },
        )
        reviewer_output = FlowRoleOutput(
            role="reviewer",
            loop_index=state.loop_input.loop_index,
            content=state.loop_input.reviewer_feedback,
            payload=getattr(state, "reviewer_payload", {
                "qa_result": "pass",
                "bugs": [],
                "uiux_suggestions": [],
                "possible_problems": [],
                "safety_security_risks": [],
            }),
        )
        plan = UIPlanData(
            content=state.plan,
            updated_by="manager",
            updated_at=None,
            session_id=state.flow_input.project_session_id,
        )
        todos = [
            UITodoItem(
                id=str(item["id"]),
                session_id=str(item["session_id"]),
                parent_id=item.get("parent_id"),
                content=str(item["content"]),
                status=item.get("status", "pending"),
                priority=item.get("priority", "medium"),
                position=int(item.get("position", 0)),
                owner=item.get("owner", "manager"),
                updated_at=item.get("updated_at"),
            )
            for item in state.todo_list
        ]
        suggestion = UISuggestion(
            id=None,
            content=state.suggestion_content,
            status="released",
            queue_status="released",
            status_label="Released",
            verification=state.suggestion_verification,
            related_files=[],
            created_at=None,
        )
        manager_message = UIManagerMessage(
            id=0,
            content=(
                f"Loop {state.loop_input.loop_index}: I digested the directive, "
                f"manager message, todo list, worker report, and reviewer feedback. "
                f"Next task: {state.suggestion_content}"
            ),
            created_at="",
            is_human=False,
            queue_status="manager_response",
            status_label="Manager response",
        )
        return FlowOutput(
            project_session_id=state.flow_input.project_session_id,
            power_team_project_id=state.flow_input.power_team_project_id,
            loop_index=state.loop_input.loop_index,
            status=state.status,
            plan=plan,
            todos=todos,
            suggestion=suggestion,
            manager_message=manager_message,
            manager=manager_output,
            worker=worker_output,
            reviewer=reviewer_output,
            todo_update_json=state.todo_update_json,
            handoff_update=state.handoff_update,
        )


def build_langgraph() -> Any:
    """Build a langgraph version of flow_01 when langgraph is installed."""
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise ImportError("langgraph is optional for flow_01; install langgraph to build the graph") from exc

    workflow = Flow01Workflow(storage=FlowStorage(DB_PATH))
    graph = StateGraph(dict)

    def manager_node(raw_state: dict) -> dict:
        state = _state_from_raw(raw_state)
        return asdict(workflow.manager_step(state))

    def worker_node(raw_state: dict) -> dict:
        state = _state_from_raw(raw_state)
        return asdict(workflow.worker_step(state))

    def reviewer_node(raw_state: dict) -> dict:
        state = _state_from_raw(raw_state)
        return asdict(workflow.reviewer_step(state))

    graph.add_node("manager", manager_node)
    graph.add_node("worker", worker_node)
    graph.add_node("reviewer", reviewer_node)
    graph.set_entry_point("manager")
    graph.add_edge("manager", "worker")
    graph.add_edge("worker", "reviewer")
    graph.add_edge("reviewer", END)
    return graph.compile()
