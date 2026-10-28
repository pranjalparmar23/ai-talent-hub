"""Unit tests for InterviewService.

The agents, Redis and the four thin Postgres methods are all replaced with
in-memory fakes, so these tests exercise the orchestration logic only:
completion rules, ownership, cache-vs-DB history handling, feedback caching.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.agents.shared.memory_agent import MemoryAgent
from app.services.interview_service import (
    CLOSING_MESSAGE,
    MAX_QUESTIONS,
    InterviewService,
)
from tests.unit.agents.test_memory_agent import FakeRedis

USER_ID = str(uuid.uuid4())
OTHER_USER_ID = str(uuid.uuid4())


class FakeInterviewer:
    def __init__(self):
        self.followup_calls: list[dict] = []

    async def start_session(self, company, role, max_questions=6):
        return {"question": f"Opening for {role} at {company}", "used_rag": False}

    async def followup(self, history, response, **kwargs):
        self.followup_calls.append({"history": list(history), **kwargs})
        return f"Follow-up #{kwargs['question_number']}"


class FakeEvaluator:
    def __init__(self, parse_error=False):
        self.calls = 0
        self.parse_error = parse_error

    async def evaluate_interview(self, history, *, company="", role=""):
        self.calls += 1
        result = {
            "scores": {
                "communication": 8.0,
                "technical_knowledge": 6.0,
                "problem_solving": 7.0,
                "confidence": 7.0,
            },
            "overall_score": 7.0,
            "strengths": ["Clear"],
            "improvements": ["Be specific"],
            "summary": "Good.",
        }
        if self.parse_error:
            result["_parse_error"] = True
        return result


class BrokenRedis:
    """Every call fails, like an unreachable Redis."""

    def __getattr__(self, name):
        async def _fail(*args, **kwargs):
            raise ConnectionError("redis down")

        return _fail


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def svc(monkeypatch, redis):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    service = InterviewService()
    service.interviewer = FakeInterviewer()
    service.evaluator = FakeEvaluator()
    service.memory_factory = lambda sid: MemoryAgent(sid, client=redis)

    rows: dict[uuid.UUID, dict] = {}
    service.rows = rows  # exposed for assertions

    async def create_row(session_id, user_id, company, role, history):
        rows[session_id] = {
            "id": session_id,
            "user_id": str(user_id),
            "company": company,
            "role": role,
            "history": list(history),
            "feedback": None,
        }

    async def get_row(session_id, user_id):
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            raise HTTPException(404, "Interview session not found.")
        row = rows.get(sid)
        if row is None or row["user_id"] != str(user_id):
            raise HTTPException(404, "Interview session not found.")
        return {k: v for k, v in row.items() if k != "user_id"}

    async def save_history(session_id, history):
        rows[session_id]["history"] = list(history)

    async def save_feedback(session_id, feedback):
        rows[session_id]["feedback"] = feedback

    service._create_row = create_row
    service._get_row = get_row
    service._save_history = save_history
    service._save_feedback = save_feedback
    return service


async def _start(svc):
    return await svc.start_session("Amazon", "SDE II", USER_ID)


async def _answer(svc, sid, text="my answer"):
    return await svc.process_response(sid, text, USER_ID)


# ── start ─────────────────────────────────────────────────────────────


class TestStart:
    @pytest.mark.asyncio
    async def test_creates_session_and_returns_first_question(self, svc, redis):
        result = await _start(svc)

        assert result["question"] == "Opening for SDE II at Amazon"
        assert result["question_number"] == 1
        assert result["max_questions"] == MAX_QUESTIONS
        sid = uuid.UUID(result["session_id"])
        assert svc.rows[sid]["history"] == [
            {"role": "ai", "content": result["question"]}
        ]
        # Redis cache seeded too
        cached = await MemoryAgent(result["session_id"], client=redis).get_history()
        assert cached == svc.rows[sid]["history"]

    @pytest.mark.asyncio
    async def test_company_and_role_trimmed(self, svc):
        result = await svc.start_session("  Google ", " SWE  ", USER_ID)
        sid = uuid.UUID(result["session_id"])
        assert svc.rows[sid]["company"] == "Google"
        assert svc.rows[sid]["role"] == "SWE"

    @pytest.mark.asyncio
    async def test_bad_user_id_rejected(self, svc):
        with pytest.raises(HTTPException) as exc:
            await svc.start_session("A", "B", "not-a-uuid")
        assert exc.value.status_code == 401


# ── respond ───────────────────────────────────────────────────────────


class TestRespond:
    @pytest.mark.asyncio
    async def test_returns_followup_and_persists(self, svc):
        sid = (await _start(svc))["session_id"]

        result = await _answer(svc, sid, "I build APIs.")

        assert result["question"] == "Follow-up #2"
        assert result["session_complete"] is False
        assert result["question_number"] == 2
        history = svc.rows[uuid.UUID(sid)]["history"]
        assert [m["role"] for m in history] == ["ai", "human", "ai"]
        assert history[1]["content"] == "I build APIs."

    @pytest.mark.asyncio
    async def test_followup_gets_context(self, svc):
        sid = (await _start(svc))["session_id"]
        await _answer(svc, sid)

        call = svc.interviewer.followup_calls[0]
        assert call["company"] == "Amazon"
        assert call["role"] == "SDE II"
        assert call["question_number"] == 2
        assert call["max_questions"] == MAX_QUESTIONS
        # history handed to the agent already includes the latest answer
        assert call["history"][-1] == {"role": "human", "content": "my answer"}

    @pytest.mark.asyncio
    async def test_blank_response_rejected(self, svc):
        sid = (await _start(svc))["session_id"]
        with pytest.raises(HTTPException) as exc:
            await _answer(svc, sid, "   ")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_final_answer_completes_interview_without_llm_call(self, svc):
        sid = (await _start(svc))["session_id"]

        results = [await _answer(svc, sid) for _ in range(MAX_QUESTIONS)]

        assert [r["session_complete"] for r in results] == [False] * (
            MAX_QUESTIONS - 1
        ) + [True]
        assert results[-1]["question"] == CLOSING_MESSAGE
        # the closing message is not LLM-generated
        assert len(svc.interviewer.followup_calls) == MAX_QUESTIONS - 1

    @pytest.mark.asyncio
    async def test_answer_after_completion_is_conflict(self, svc):
        sid = (await _start(svc))["session_id"]
        for _ in range(MAX_QUESTIONS):
            await _answer(svc, sid)

        with pytest.raises(HTTPException) as exc:
            await _answer(svc, sid)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_answer_after_feedback_is_conflict(self, svc):
        sid = (await _start(svc))["session_id"]
        await _answer(svc, sid)
        await svc.generate_feedback(sid, USER_ID)

        with pytest.raises(HTTPException) as exc:
            await _answer(svc, sid)
        assert exc.value.status_code == 409


# ── ownership / ids ───────────────────────────────────────────────────


class TestAccessControl:
    @pytest.mark.asyncio
    async def test_other_users_session_is_404(self, svc):
        sid = (await _start(svc))["session_id"]

        for call in (
            svc.process_response(sid, "hi", OTHER_USER_ID),
            svc.generate_feedback(sid, OTHER_USER_ID),
            svc.get_session(sid, OTHER_USER_ID),
        ):
            with pytest.raises(HTTPException) as exc:
                await call
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_real_get_row_rejects_malformed_ids_before_touching_db(self):
        # Bound to the real class method (no fakes): bad UUIDs must 404 immediately.
        for sid, uid in (("not-a-uuid", USER_ID), (str(uuid.uuid4()), "nope")):
            with pytest.raises(HTTPException) as exc:
                await InterviewService._get_row(object(), sid, uid)
            assert exc.value.status_code == 404


# ── cache vs database ─────────────────────────────────────────────────


class TestHistoryRecovery:
    @pytest.mark.asyncio
    async def test_expired_redis_falls_back_to_db_and_reseeds(self, svc, redis):
        sid = (await _start(svc))["session_id"]
        await _answer(svc, sid, "first answer")
        redis.lists.clear()  # simulate TTL expiry

        result = await _answer(svc, sid, "second answer")

        # conversation continued from the DB copy, not from scratch
        assert result["question_number"] == 3
        call = svc.interviewer.followup_calls[-1]
        assert [m["content"] for m in call["history"] if m["role"] == "human"] == [
            "first answer",
            "second answer",
        ]
        cached = await MemoryAgent(sid, client=redis).get_history()
        assert cached == svc.rows[uuid.UUID(sid)]["history"]

    @pytest.mark.asyncio
    async def test_redis_outage_does_not_break_the_interview(self, svc):
        sid = (await _start(svc))["session_id"]
        svc.memory_factory = lambda s: MemoryAgent(s, client=BrokenRedis())

        result = await _answer(svc, sid, "still works")

        assert result["question"] == "Follow-up #2"
        history = svc.rows[uuid.UUID(sid)]["history"]
        assert history[-2]["content"] == "still works"

    @pytest.mark.asyncio
    async def test_stale_cache_loses_to_longer_db_history(self, svc, redis):
        sid = (await _start(svc))["session_id"]
        await _answer(svc, sid, "a1")
        # cache falls behind the database (e.g. a failed cache write earlier)
        redis.lists[f"interview:history:{sid}"] = redis.lists[
            f"interview:history:{sid}"
        ][:1]

        await _answer(svc, sid, "a2")

        humans = [
            m["content"]
            for m in svc.rows[uuid.UUID(sid)]["history"]
            if m["role"] == "human"
        ]
        assert humans == ["a1", "a2"]


# ── feedback ──────────────────────────────────────────────────────────


class TestFeedback:
    @pytest.mark.asyncio
    async def test_requires_at_least_one_answer(self, svc):
        sid = (await _start(svc))["session_id"]
        with pytest.raises(HTTPException) as exc:
            await svc.generate_feedback(sid, USER_ID)
        assert exc.value.status_code == 400
        assert svc.evaluator.calls == 0

    @pytest.mark.asyncio
    async def test_generated_saved_and_cached(self, svc):
        sid = (await _start(svc))["session_id"]
        await _answer(svc, sid)

        first = await svc.generate_feedback(sid, USER_ID)
        second = await svc.generate_feedback(sid, USER_ID)

        assert first["overall_score"] == 7.0
        assert first["parse_error"] is False
        assert second["scores"] == first["scores"]
        assert svc.evaluator.calls == 1  # second call served from the DB copy
        assert svc.rows[uuid.UUID(sid)]["feedback"] is not None

    @pytest.mark.asyncio
    async def test_early_finish_allowed_before_all_questions(self, svc):
        sid = (await _start(svc))["session_id"]
        await _answer(svc, sid)
        await _answer(svc, sid)
        result = await svc.generate_feedback(sid, USER_ID)
        assert result["overall_score"] == 7.0

    @pytest.mark.asyncio
    async def test_parse_error_is_flagged_and_not_cached(self, svc):
        svc.evaluator = FakeEvaluator(parse_error=True)
        sid = (await _start(svc))["session_id"]
        await _answer(svc, sid)

        first = await svc.generate_feedback(sid, USER_ID)
        assert first["parse_error"] is True
        assert svc.rows[uuid.UUID(sid)]["feedback"] is None

        await svc.generate_feedback(sid, USER_ID)
        assert svc.evaluator.calls == 2  # retried, since nothing was cached


# ── get_session ───────────────────────────────────────────────────────


class TestGetSession:
    @pytest.mark.asyncio
    async def test_fresh_session(self, svc):
        sid = (await _start(svc))["session_id"]
        state = await svc.get_session(sid, USER_ID)

        assert state["company"] == "Amazon"
        assert state["role"] == "SDE II"
        assert state["messages"] == [
            {"role": "interviewer", "content": "Opening for SDE II at Amazon"}
        ]
        assert state["question_number"] == 1
        assert state["session_complete"] is False
        assert state["feedback_ready"] is False

    @pytest.mark.asyncio
    async def test_complete_and_feedback_flags(self, svc):
        sid = (await _start(svc))["session_id"]
        for _ in range(MAX_QUESTIONS):
            await _answer(svc, sid)
        state = await svc.get_session(sid, USER_ID)
        assert state["session_complete"] is True
        assert state["question_number"] == MAX_QUESTIONS
        assert state["feedback_ready"] is False

        await svc.generate_feedback(sid, USER_ID)
        assert (await svc.get_session(sid, USER_ID))["feedback_ready"] is True

    @pytest.mark.asyncio
    async def test_feedback_before_finishing_marks_session_complete(self, svc):
        sid = (await _start(svc))["session_id"]
        await _answer(svc, sid)
        await svc.generate_feedback(sid, USER_ID)
        assert (await svc.get_session(sid, USER_ID))["session_complete"] is True
