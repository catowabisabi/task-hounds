"""HTTP surface for pending OpenCode interactive questions."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from task_hounds_api.db.ops import question as db_question
from task_hounds_api.opencode import question_bridge

router = APIRouter(prefix="/api/opencode/questions", tags=["opencode-questions"])


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[list[str]]


@router.get("")
def pending_questions(project_session_id: str | None = None) -> dict:
    return {"questions": db_question.list_pending(project_session_id)}


@router.post("/{request_id}/answer")
def answer_question(request_id: str, body: AnswerRequest) -> dict:
    row = db_question.get(request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Question not found")
    if len(body.answers) != len(row["questions"]):
        raise HTTPException(
            status_code=400,
            detail="answers must contain one entry for each OpenCode question",
        )
    try:
        result = question_bridge.answer(request_id, body.answers, source="human")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenCode rejected the answer: {exc}") from exc
    return {"ok": True, "question": result}
