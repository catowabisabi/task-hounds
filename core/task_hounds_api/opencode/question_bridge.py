"""Bridge OpenCode question-tool requests to Task Hounds API/UI."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from task_hounds_api.db import ROOT
from task_hounds_api.db.ops import question as db_question
from task_hounds_api.opencode.log_rotation import rotate_if_needed

AUTO_ANSWER_SECONDS = 900
POLL_SECONDS = 0.5
_LOG_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(event: str, data: dict[str, Any]) -> None:
    path = ROOT / "core" / "runtime" / "logs" / "opencode" / "questions.jsonl"
    record = {
        "ts": _utc_now().isoformat(),
        "event": event,
        **data,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _LOG_LOCK:
            rotate_if_needed(path, incoming_bytes=len(line.encode("utf-8")) + 1)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
    except Exception:
        pass


def _request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 5,
) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}


def _query(directory: str | None) -> str:
    if not directory:
        return ""
    return "?" + urllib.parse.urlencode({"directory": directory})


def list_server_questions(host: str, port: int, directory: str | None) -> list[dict]:
    value = _request_json(
        "GET",
        f"http://{host}:{port}/question{_query(directory)}",
    )
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        data = value.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    return []


def _first_answers(questions: list[dict[str, Any]]) -> list[list[str]]:
    answers: list[list[str]] = []
    for question in questions:
        options = question.get("options")
        first = options[0] if isinstance(options, list) and options else None
        label = first.get("label") if isinstance(first, dict) else first
        answers.append([str(label)] if label is not None else [""])
    return answers


def answer(request_id: str, answers: list[list[str]], source: str = "human") -> dict:
    row = db_question.get(request_id)
    if not row:
        raise KeyError(f"Unknown OpenCode question: {request_id}")
    if row["status"] != "pending":
        return row
    if not db_question.claim(request_id):
        return db_question.get(request_id) or row
    url = (
        f"http://{row['host']}:{row['port']}/question/"
        f"{urllib.parse.quote(request_id, safe='')}/reply"
        f"{_query(row.get('workspace_path'))}"
    )
    try:
        _request_json("POST", url, {"answers": answers})
    except Exception as exc:
        db_question.release(request_id, str(exc))
        _audit("question.answer_failed", {
            "request_id": request_id,
            "role": row["role"],
            "source": source,
            "answers": answers,
            "error": str(exc),
        })
        raise
    status = "auto_answered" if source == "timeout" else "answered"
    db_question.finish(
        request_id,
        status=status,
        answers=answers,
        source=source,
    )
    _audit("question.answered", {
        "request_id": request_id,
        "opencode_session_id": row["opencode_session_id"],
        "project_session_id": row.get("project_session_id"),
        "role": row["role"],
        "source": source,
        "questions": row["questions"],
        "answers": answers,
    })
    return db_question.get(request_id) or row


class QuestionListener:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        opencode_session_id: str,
        project_session_id: str | None,
        role: str,
        workspace: str | Path | None,
    ) -> None:
        self.host = host
        self.port = port
        self.opencode_session_id = opencode_session_id
        self.project_session_id = project_session_id
        self.role = role
        self.workspace = str(workspace) if workspace else None
        self._stop = threading.Event()
        self._waiting = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"opencode-question-{role}-{opencode_session_id[-8:]}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def is_waiting(self) -> bool:
        return self._waiting.is_set()

    def _run(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            try:
                requests = list_server_questions(self.host, self.port, self.workspace)
                self._capture(requests)
                self._auto_answer_expired()
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                continue
            except Exception as exc:
                _audit("question.listener_error", {
                    "opencode_session_id": self.opencode_session_id,
                    "role": self.role,
                    "error": str(exc),
                })

    def _capture(self, requests: list[dict]) -> None:
        found = False
        for request in requests:
            if str(request.get("sessionID") or "") != self.opencode_session_id:
                continue
            found = True
            request_id = str(request.get("id") or request.get("requestID") or "")
            questions = request.get("questions")
            if not request_id or not isinstance(questions, list):
                continue
            asked = _utc_now()
            deadline = asked + timedelta(seconds=AUTO_ANSWER_SECONDS)
            inserted = db_question.upsert_pending(
                request_id=request_id,
                opencode_session_id=self.opencode_session_id,
                project_session_id=self.project_session_id,
                role=self.role,
                host=self.host,
                port=self.port,
                workspace_path=self.workspace,
                questions=questions,
                asked_at=asked.isoformat(),
                deadline_at=deadline.isoformat(),
            )
            if inserted:
                _audit("question.asked", {
                    "request_id": request_id,
                    "opencode_session_id": self.opencode_session_id,
                    "project_session_id": self.project_session_id,
                    "role": self.role,
                    "questions": questions,
                    "deadline_at": deadline.isoformat(),
                })
        if found:
            self._waiting.set()
        else:
            self._waiting.clear()

    def _auto_answer_expired(self) -> None:
        now = _utc_now()
        for row in db_question.list_pending(self.project_session_id):
            if row["opencode_session_id"] != self.opencode_session_id:
                continue
            try:
                deadline = datetime.fromisoformat(row["deadline_at"])
            except (TypeError, ValueError):
                continue
            if deadline <= now:
                answer(row["request_id"], _first_answers(row["questions"]), source="timeout")
