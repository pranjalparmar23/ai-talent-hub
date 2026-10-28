"""InterviewService — orchestrates a mock-interview session.

Per request:   load session (ownership-checked) → read history → LLM → persist.

Storage:
  - Postgres `interview_sessions` is the durable source of truth (history + feedback).
  - Redis (MemoryAgent) is the fast cache; if it is empty, stale or down we fall
    back to the Postgres copy, so an expired key never loses a conversation.

Interview length is fixed at MAX_QUESTIONS candidate answers. No schema change is
needed: completion is derived from the number of candidate answers in the history.
"""

import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select

from app.agents.candidate.interview_agent import InterviewAgent
from app.agents.shared.evaluation_agent import EvaluationAgent
from app.agents.shared.memory_agent import MemoryAgent
from app.database.models import InterviewSession
from app.database.postgres import AsyncSessionLocal

logger = logging.getLogger(__name__)


MAX_QUESTIONS = 6
CLOSING_MESSAGE = (
    "That's all the questions I have — thank you for your time. "
    "Your feedback report is ready whenever you are."
)


class InterviewService:
    # NOTE: __init__ must stay argument-free — routes use Depends(InterviewService),
    # and FastAPI would turn any extra parameter into a query-string field.
    def __init__(self):
        self.interviewer = InterviewAgent()
        self.evaluator = EvaluationAgent()
        self.memory_factory = MemoryAgent

    # ── Public API ─────────────────────────────────────────────────────────

    async def start_session(self, company: str, role: str, user_id: str) -> dict:
        company, role = company.strip(), role.strip()
        user_uuid = self._parse_uuid(user_id, status.HTTP_401_UNAUTHORIZED)

        # Generate the opening question first so a failure never leaves an orphan row.
        result = await self.interviewer.start_session(
            company, role, max_questions=MAX_QUESTIONS
        )
        question = result["question"]

        session_id = uuid.uuid4()
        history = [{"role": "ai", "content": question}]
        await self._create_row(session_id, user_uuid, company, role, history)
        await self._remember(self.memory_factory(str(session_id)), False, history, [])

        return {
            "session_id": str(session_id),
            "question": question,
            "question_number": 1,
            "max_questions": MAX_QUESTIONS,
        }

    async def process_response(
        self, session_id: str, response: str, user_id: str
    ) -> dict:
        row = await self._get_row(session_id, user_id)
        if row["feedback"] is not None:
            raise self._conflict()

        text = (response or "").strip()
        if not text:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="Response must not be empty."
            )

        memory = self.memory_factory(session_id)
        history, in_sync = await self._load_history(memory, row)
        answers = self._count_answers(history)
        if answers >= MAX_QUESTIONS:
            raise self._conflict()

        history.append({"role": "human", "content": text})
        answers += 1
        new_messages = [("human", text)]

        if answers >= MAX_QUESTIONS:
            question, complete = CLOSING_MESSAGE, True
        else:
            question = await self.interviewer.followup(
                history,
                text,
                company=row["company"],
                role=row["role"],
                question_number=answers + 1,
                max_questions=MAX_QUESTIONS,
            )
            complete = False

        history.append({"role": "ai", "content": question})
        new_messages.append(("ai", question))

        await self._save_history(row["id"], history)
        await self._remember(memory, in_sync, history, new_messages)

        return {
            "question": question,
            "session_complete": complete,
            "question_number": min(answers + 1, MAX_QUESTIONS),
            "max_questions": MAX_QUESTIONS,
        }

    async def generate_feedback(self, session_id: str, user_id: str) -> dict:
        row = await self._get_row(session_id, user_id)

        # Feedback is generated once and cached — re-opening the page is free.
        if row["feedback"] is not None:
            return {**row["feedback"], "parse_error": False}

        memory = self.memory_factory(session_id)
        history, _ = await self._load_history(memory, row)
        if self._count_answers(history) == 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Answer at least one question before requesting feedback.",
            )

        result = await self.evaluator.evaluate_interview(
            history, company=row["company"], role=row["role"]
        )
        parse_error = bool(result.pop("_parse_error", False))
        if not parse_error:
            await self._save_feedback(row["id"], result)

        return {**result, "parse_error": parse_error}

    async def get_session(self, session_id: str, user_id: str) -> dict:
        row = await self._get_row(session_id, user_id)
        history, _ = await self._load_history(self.memory_factory(session_id), row)
        answers = self._count_answers(history)
        return {
            "session_id": session_id,
            "company": row["company"],
            "role": row["role"],
            "messages": [
                {
                    "role": "interviewer" if m["role"] == "ai" else "candidate",
                    "content": m["content"],
                }
                for m in history
            ],
            "question_number": min(answers + 1, MAX_QUESTIONS),
            "max_questions": MAX_QUESTIONS,
            "session_complete": answers >= MAX_QUESTIONS or row["feedback"] is not None,
            "feedback_ready": row["feedback"] is not None,
        }

    # ── History (Redis cache over Postgres truth) ──────────────────────────

    async def _load_history(
        self, memory: MemoryAgent, row: dict[str, Any]
    ) -> tuple[list[dict], bool]:
        """Return (history, cache_in_sync). The longer of the two copies wins, so a
        failed/expired cache can never roll the conversation back."""
        db_history = list(row["history"] or [])
        try:
            cached = await memory.get_history()
        except Exception as e:
            logger.warning(f"Redis unavailable for session {row['id']}: {e}")
            return db_history, False

        if len(cached) >= len(db_history) and cached:
            return cached, True
        return db_history, False

    async def _remember(
        self,
        memory: MemoryAgent,
        in_sync: bool,
        full_history: list[dict],
        new_messages: list[tuple[str, str]],
    ) -> None:
        """Best-effort cache write. In sync → append; otherwise re-seed wholesale."""
        try:
            if in_sync:
                for role, content in new_messages:
                    if role == "human":
                        await memory.add_user_message(content)
                    else:
                        await memory.add_ai_message(content)
            else:
                await memory.load(full_history)
        except Exception as e:
            logger.warning(f"Could not update Redis history cache: {e}")

    @staticmethod
    def _count_answers(history: list[dict]) -> int:
        return sum(1 for m in history if m.get("role") == "human")

    # ── Postgres access (thin, so unit tests can override them) ────────────

    async def _create_row(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        company: str,
        role: str,
        history: list[dict],
    ) -> None:
        async with AsyncSessionLocal() as db:
            db.add(
                InterviewSession(
                    id=session_id,
                    user_id=user_id,
                    company=company,
                    role=role,
                    history=history,
                )
            )
            await db.commit()

    async def _get_row(self, session_id: str, user_id: str) -> dict[str, Any]:
        """Load a session owned by `user_id`. Someone else's session looks exactly
        like a missing one (404) so IDs cannot be probed."""
        not_found = HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Interview session not found."
        )
        try:
            sid = uuid.UUID(str(session_id))
            uid = uuid.UUID(str(user_id))
        except ValueError:
            raise not_found

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(InterviewSession).where(
                    InterviewSession.id == sid, InterviewSession.user_id == uid
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise not_found
            return {
                "id": sid,
                "company": row.company or "",
                "role": row.role or "",
                "history": list(row.history or []),
                "feedback": row.feedback,
            }

    async def _save_history(self, session_id: uuid.UUID, history: list[dict]) -> None:
        async with AsyncSessionLocal() as db:
            row = await db.get(InterviewSession, session_id)
            if row is not None:
                row.history = history  # new list object → JSON change detected
                await db.commit()

    async def _save_feedback(self, session_id: uuid.UUID, feedback: dict) -> None:
        async with AsyncSessionLocal() as db:
            row = await db.get(InterviewSession, session_id)
            if row is not None:
                row.feedback = feedback
                await db.commit()

    # ── Misc ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_uuid(value: str, error_status: int) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except ValueError:
            raise HTTPException(error_status, detail="Invalid identifier.")

    @staticmethod
    def _conflict() -> HTTPException:
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This interview is already complete — view your feedback.",
        )
