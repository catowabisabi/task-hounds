"""Output/signal adapters for flow_01.

The workflow always writes its own fake DB through FlowStorage. Signal adapters
decide whether the same run should also be visible to the real dashboard.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from .interface import FlowInput, FlowOutput, utc_now


class FlowSignalAdapter(Protocol):
    def loop_started(self, flow_input: FlowInput, loop_index: int) -> None: ...
    def manager_completed(self, output: FlowOutput) -> None: ...
    def worker_completed(self, output: FlowOutput) -> None: ...
    def reviewer_completed(self, output: FlowOutput) -> None: ...
    def loop_completed(self, output: FlowOutput) -> None: ...


class NoopSignalAdapter:
    """Adapter that deliberately emits nothing."""

    def loop_started(self, flow_input: FlowInput, loop_index: int) -> None:
        return

    def manager_completed(self, output: FlowOutput) -> None:
        return

    def worker_completed(self, output: FlowOutput) -> None:
        return

    def reviewer_completed(self, output: FlowOutput) -> None:
        return

    def loop_completed(self, output: FlowOutput) -> None:
        return


@dataclass
class RecordingSignalAdapter:
    """Test adapter that records the same signal sequence without touching real state."""

    events: list[tuple[str, str, int]] = field(default_factory=list)

    def loop_started(self, flow_input: FlowInput, loop_index: int) -> None:
        self.events.append(("loop_started", flow_input.project_session_id, loop_index))

    def manager_completed(self, output: FlowOutput) -> None:
        self.events.append(("manager_completed", output.project_session_id, output.loop_index))

    def worker_completed(self, output: FlowOutput) -> None:
        self.events.append(("worker_completed", output.project_session_id, output.loop_index))

    def reviewer_completed(self, output: FlowOutput) -> None:
        self.events.append(("reviewer_completed", output.project_session_id, output.loop_index))

    def loop_completed(self, output: FlowOutput) -> None:
        self.events.append(("loop_completed", output.project_session_id, output.loop_index))


class FastApiServiceSignalAdapter:
    """Emit flow_01 progress through the existing FastAPI service facade.

    This intentionally does not write the real workflow DB tables directly.
    It updates visible runtime signals only:

    - agent_registry state/last_seen through services.agents.update_state
    - real stream files through services.streams.active_path
    - runtime run log through services.loop.run_log
    """

    def __init__(self) -> None:
        from api.services.legacy import services

        self.services = services

    def loop_started(self, flow_input: FlowInput, loop_index: int) -> None:
        self._update_agent("manager", "busy", "flow_01 manager: digesting directive and selecting one task")
        self._append_stream(
            "manager",
            {
                "t": "sys",
                "msg": f"flow_01 loop {loop_index} started for {flow_input.project_session_id}",
                "project_session_id": flow_input.project_session_id,
                "power_team_project_id": flow_input.power_team_project_id,
            },
        )
        self._append_run_log(f"flow_01 loop {loop_index} started session={flow_input.project_session_id}")

    def manager_completed(self, output: FlowOutput) -> None:
        self._append_stream(
            "manager",
            {
                "t": "flow",
                "msg": output.manager_message.content,
                "suggestion": output.suggestion.content,
                "status": output.status,
            },
        )
        self._update_agent("manager", "idle")
        self._update_agent("worker", "busy", f"flow_01 worker: {output.suggestion.content[:180]}")

    def worker_completed(self, output: FlowOutput) -> None:
        self._append_stream(
            "worker",
            {
                "t": "flow",
                "msg": output.worker.content,
                "files_changed": output.worker.payload.get("files_changed", []),
                "test_result": output.worker.payload.get("test_result", ""),
            },
        )
        self._update_agent("worker", "idle")
        self._update_agent("reviewer", "busy", "flow_01 reviewer: checking QA, bugs, UX, and risks")

    def reviewer_completed(self, output: FlowOutput) -> None:
        self._append_stream(
            "reviewer",
            {
                "t": "flow",
                "msg": output.reviewer.content,
                "qa_result": output.reviewer.payload.get("qa_result", "unknown"),
            },
        )
        self._update_agent("reviewer", "idle")

    def loop_completed(self, output: FlowOutput) -> None:
        self._append_stream(
            "manager",
            {
                "t": "sys",
                "msg": f"flow_01 loop {output.loop_index} completed",
                "project_session_id": output.project_session_id,
                "status": output.status,
            },
        )
        for role in ("manager", "worker", "reviewer"):
            self._update_agent(role, "idle")
        self._append_run_log(f"flow_01 loop {output.loop_index} completed session={output.project_session_id}")

    def _update_agent(self, role: str, state: str, current_step: str | None = None) -> None:
        kwargs = {"state": state}
        if state == "busy":
            kwargs.update(
                current_step=current_step or f"{role} working",
                current_step_started_at=utc_now(),
                last_stream_at=utc_now(),
            )
        elif state == "idle":
            kwargs.update(current_step=None, current_step_started_at=None)
        self.services.agents.update_state(role, **kwargs)

    def _append_stream(self, role: str, payload: dict) -> None:
        path = self.services.streams.active_path(role)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.open("a", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _append_run_log(self, message: str) -> None:
        path = self.services.loop.run_log
        path.parent.mkdir(parents=True, exist_ok=True)
        path.open("a", encoding="utf-8").write(f"[{utc_now()}] {message}\n")
