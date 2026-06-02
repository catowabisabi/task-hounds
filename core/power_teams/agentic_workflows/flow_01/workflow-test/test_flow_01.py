from __future__ import annotations

import sys
from pathlib import Path

import pytest

CORE_DIR = Path(__file__).resolve().parents[4]
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from power_teams.agentic_workflows.flow_01 import (  # noqa: E402
    DB_PATH,
    WORKFLOW_TEST_DIR,
    Flow01Workflow,
    FlowInput,
    FlowLimits,
    FlowStorage,
    FastApiServiceSignalAdapter,
    RecordingSignalAdapter,
    build_langgraph,
)
from power_teams.agentic_workflows.flow_01.interface import FlowValidationError  # noqa: E402


def _flow_input() -> FlowInput:
    return FlowInput(
        power_team_project_id="pt_flow_project_01",
        project_session_id="ps_flow_01_test",
        workspace_id="ws_flow_01",
        workspace_path=str(WORKFLOW_TEST_DIR),
        manager_opencode_session_id="oc_manager_flow_01",
        worker_opencode_session_id="oc_worker_flow_01",
        reviewer_opencode_session_id="oc_reviewer_flow_01",
        chat_opencode_session_id="oc_chat_flow_01",
        server_instance_id=101,
        human_directive="Build a controlled fake workflow for Task Hounds.",
        human_new_thought_and_suggestion="Keep the flow swappable and easy to test.",
        human_suggested_new_task_or_item="Create a fake DB-backed workflow loop.",
        manager_message="Use manager message as shared guidance for all roles.",
        todo_items=["Define interfaces", "Write fake DB output", "Review loop result"],
    )


TASK_SCENARIOS = [
    {
        "slug": "counter",
        "directive": "Build a tiny counter app with increment, decrement, and reset.",
        "task": "Create the counter app interaction contract.",
        "todos": ["Define counter state", "Add increment/decrement/reset", "Review button states"],
    },
    {
        "slug": "todo",
        "directive": "Build a simple todo list app for quick personal notes.",
        "task": "Create the todo list task flow.",
        "todos": ["Add todo input", "Toggle completed state", "Persist visible todo rows"],
    },
    {
        "slug": "weather-report",
        "directive": "Build a weather report generator for a daily email.",
        "task": "Create the weather report generation flow.",
        "todos": ["Collect city input", "Generate summary sections", "Review empty weather data handling"],
    },
    {
        "slug": "language-learning",
        "directive": "Build a Chinese learning app for young people.",
        "task": "Create the first vocabulary lesson flow.",
        "todos": ["Define lesson cards", "Add quiz feedback", "Review youth-friendly tone"],
    },
    {
        "slug": "expense-tracker",
        "directive": "Build an expense tracker for small monthly budgets.",
        "task": "Create the expense entry and summary flow.",
        "todos": ["Add amount/category fields", "Calculate monthly total", "Review invalid amount handling"],
    },
    {
        "slug": "invoice-generator",
        "directive": "Build an invoice generator for freelancers.",
        "task": "Create the invoice draft workflow.",
        "todos": ["Capture client details", "Add line items", "Review totals and tax fields"],
    },
    {
        "slug": "booking-calendar",
        "directive": "Build a booking calendar for a small service business.",
        "task": "Create the booking slot workflow.",
        "todos": ["List available slots", "Reserve selected slot", "Review double-booking risk"],
    },
    {
        "slug": "study-dashboard",
        "directive": "Build a student study dashboard with progress tracking.",
        "task": "Create the study progress workflow.",
        "todos": ["Track study sessions", "Summarize progress", "Review motivational feedback"],
    },
    {
        "slug": "crm-lite",
        "directive": "Build a lightweight CRM for following up with leads.",
        "task": "Create the lead follow-up workflow.",
        "todos": ["Capture lead status", "Schedule follow-up", "Review stale lead warnings"],
    },
    {
        "slug": "automation-hub",
        "directive": "Build an automation hub that runs recurring reports.",
        "task": "Create the recurring report workflow.",
        "todos": ["Define report schedule", "Track last run result", "Review failure recovery path"],
    },
]


def _scenario_input(index: int, scenario: dict) -> FlowInput:
    return FlowInput(
        power_team_project_id=f"pt_flow_project_task_{index:02d}",
        project_session_id=f"ps_flow_task_{index:02d}",
        workspace_id=f"ws_flow_task_{index:02d}",
        workspace_path=str(WORKFLOW_TEST_DIR / f"test-dir-{index:02d}"),
        manager_opencode_session_id=f"oc_manager_task_{index:02d}",
        worker_opencode_session_id=f"oc_worker_task_{index:02d}",
        reviewer_opencode_session_id=f"oc_reviewer_task_{index:02d}",
        chat_opencode_session_id=f"oc_chat_task_{index:02d}",
        server_instance_id=200 + index,
        human_directive=scenario["directive"],
        human_new_thought_and_suggestion="Keep the existing UI/UX contract stable.",
        human_suggested_new_task_or_item=scenario["task"],
        manager_message="Use the current directive, manager message, and todo list as worker context.",
        todo_items=scenario["todos"],
    )


def _workflow(test_dir_name: str) -> Flow01Workflow:
    test_dir = WORKFLOW_TEST_DIR / test_dir_name
    test_dir.mkdir(parents=True, exist_ok=True)
    storage = FlowStorage(DB_PATH)
    storage.reset_db()
    return Flow01Workflow(storage=storage, workdir=test_dir, signal_adapter=RecordingSignalAdapter())


def test_dir_01_runs_five_loops_and_writes_fake_db():
    workflow = _workflow("test-dir-01")
    outputs = workflow.run_loops(_flow_input(), loops=5)

    assert len(outputs) == 5
    assert outputs[-1].status == "completed"
    assert workflow.storage.count("project_sessions") == 1
    assert workflow.storage.count("project_session_role_sessions") == 4
    assert workflow.storage.count("workflow_runs") == 5
    assert workflow.storage.count("user_directives") == 5
    assert workflow.storage.count("manager_messages") == 5
    assert workflow.storage.count("session_plan") == 1
    assert workflow.storage.count("session_todos") == 3
    assert workflow.storage.count("suggestion_queue") == 5
    assert workflow.storage.count("worker_reports") == 5
    assert workflow.storage.count("reviewer_sessions") == 5
    assert workflow.storage.count("project_handoff") == 5


def test_dir_02_preserves_contract_shapes_for_five_loops():
    workflow = _workflow("test-dir-02")
    outputs = workflow.run_loops(_flow_input(), loops=5)

    for output in outputs:
        assert output.power_team_project_id == "pt_flow_project_01"
        assert output.project_session_id == "ps_flow_01_test"
        assert output.todo_update_json["items"]
        assert output.plan.session_id == "ps_flow_01_test"
        assert output.suggestion.status == "released"
        assert output.manager_message.queue_status == "manager_response"
        assert output.todos[0].session_id == "ps_flow_01_test"
        assert output.handoff_update["current_task"]
        assert output.manager.payload["decision"]["next_action_type"] in {"continue", "bugfix"}
        assert output.worker.payload["test_result"] == "passed"
        assert output.reviewer.payload["qa_result"] == "pass"


def test_dir_03_validates_limits_before_looping():
    workflow = _workflow("test-dir-03")
    outputs = workflow.run_loops(_flow_input(), loops=5)
    assert len(outputs) == 5

    bad_input = _flow_input()
    bad_input.human_directive = "x" * (FlowLimits().directive_max_chars + 1)

    with pytest.raises(FlowValidationError):
        workflow.run_loops(bad_input, loops=5)


def test_dir_04_rejects_more_than_five_loops():
    workflow = _workflow("test-dir-04")
    outputs = workflow.run_loops(_flow_input(), loops=5)
    assert len(outputs) == 5

    with pytest.raises(ValueError, match="loops must be between 1 and 5"):
        workflow.run_loops(_flow_input(), loops=6)


def test_dir_05_langgraph_builder_is_optional():
    workflow = _workflow("test-dir-05")
    outputs = workflow.run_loops(_flow_input(), loops=5)
    assert len(outputs) == 5

    try:
        graph = build_langgraph()
    except ImportError:
        graph = None
    assert graph is None or hasattr(graph, "invoke")


def test_dir_06_runs_ten_app_tasks_five_loops_each():
    storage = FlowStorage(DB_PATH)
    storage.reset_db()
    completion_summary = []

    for index, scenario in enumerate(TASK_SCENARIOS, start=1):
        test_dir = WORKFLOW_TEST_DIR / f"test-dir-{index:02d}"
        test_dir.mkdir(parents=True, exist_ok=True)
        signals = RecordingSignalAdapter()
        workflow = Flow01Workflow(storage=storage, workdir=test_dir, signal_adapter=signals)
        outputs = workflow.run_loops(_scenario_input(index, scenario), loops=5)
        output_file = test_dir / "worker-output.txt"

        assert len(outputs) == 5
        assert outputs[-1].status == "completed"
        assert outputs[-1].suggestion.content == scenario["task"]
        assert outputs[-1].reviewer.payload["qa_result"] == "pass"
        assert output_file.exists()
        output_text = output_file.read_text(encoding="utf-8")
        assert scenario["task"] in output_text
        assert scenario["directive"] in output_text
        assert len(signals.events) == 25
        assert signals.events[0] == ("loop_started", f"ps_flow_task_{index:02d}", 1)
        assert signals.events[-1] == ("loop_completed", f"ps_flow_task_{index:02d}", 5)
        completion_summary.append((scenario["slug"], outputs[-1].status))

    assert len(completion_summary) == 10
    assert all(status == "completed" for _, status in completion_summary)
    assert storage.count("project_sessions") == 10
    assert storage.count("project_session_role_sessions") == 40
    assert storage.count("workflow_runs") == 50
    assert storage.count("suggestion_queue") == 50
    assert storage.count("worker_reports") == 50
    assert storage.count("reviewer_sessions") == 50


def test_fastapi_signal_adapter_writes_streams_and_agent_state(tmp_path):
    storage = FlowStorage(DB_PATH)
    storage.reset_db()
    workflow = Flow01Workflow(storage=storage, workdir=tmp_path / "adapter-task", signal_adapter=RecordingSignalAdapter())
    output = workflow.run_loops(_scenario_input(1, TASK_SCENARIOS[0]), loops=1)[0]

    class FakeAgents:
        def __init__(self):
            self.updates = []

        def update_state(self, role: str, **fields):
            self.updates.append((role, fields))

    class FakeStreams:
        def active_path(self, role: str):
            return tmp_path / "streams" / f"{role}_stream.txt"

    class FakeUtils:
        def utc_now(self):
            return "2026-06-01T00:00:00+00:00"

    class FakeLoop:
        run_log = tmp_path / "runtime.log"

    class FakeServices:
        def __init__(self):
            self.agents = FakeAgents()
            self.streams = FakeStreams()
            self.utils = FakeUtils()
            self.loop = FakeLoop()

    fake_services = FakeServices()
    adapter = FastApiServiceSignalAdapter.__new__(FastApiServiceSignalAdapter)
    adapter.services = fake_services

    adapter.loop_started(_scenario_input(1, TASK_SCENARIOS[0]), 1)
    adapter.manager_completed(output)
    adapter.worker_completed(output)
    adapter.reviewer_completed(output)
    adapter.loop_completed(output)

    assert (tmp_path / "streams" / "manager_stream.txt").exists()
    assert (tmp_path / "streams" / "worker_stream.txt").exists()
    assert (tmp_path / "streams" / "reviewer_stream.txt").exists()
    assert (tmp_path / "runtime.log").exists()
    assert any(role == "manager" and fields["state"] == "busy" for role, fields in fake_services.agents.updates)
    assert any(role == "worker" and fields["state"] == "busy" for role, fields in fake_services.agents.updates)
    assert any(role == "reviewer" and fields["state"] == "busy" for role, fields in fake_services.agents.updates)
