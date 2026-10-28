"""Unit tests for QuestionGenerationAgent."""

import json

import pytest

from app.agents.recruiter.question_agent import (
    DEFAULT_COUNT,
    MAX_GAPS,
    QuestionGenerationAgent,
)
from tests.unit.agents._fakes import FakeChain, FakeMsg

JD = {
    "title": "Senior Backend Engineer",
    "skills_required": ["Python", "Kubernetes"],
    "responsibilities": ["Design distributed systems"],
}
RESUME = {
    "name": "Aarav Mehta",
    "email": "aarav@example.com",
    "phone": "+91-99999-00000",
    "linkedin": "linkedin.com/in/aarav",
    "location": "Pune",
    "summary": "Backend engineer.",
    "skills": ["Python", "FastAPI"],
    "experience_years": 3,
    "experience": [
        {
            "role": "Backend Engineer",
            "company": "PayCircle",
            "highlights": ["Built APIs"],
        }
    ],
    "projects": [
        {"name": "Ledger", "description": "Double-entry ledger", "tech_stack": ["Go"]}
    ],
}


def _response(*questions):
    return FakeMsg(json.dumps({"questions": list(questions)}))


def _q(text="How would you debug a slow query?", **extra):
    return {"question": text, "category": "technical", "difficulty": "medium", **extra}


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    return QuestionGenerationAgent()


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_returns_normalised_questions(self, agent):
        agent.chain = FakeChain(
            response=_response(
                _q(
                    "Explain Kubernetes pods.",
                    category="skill-gap",
                    difficulty="hard",
                    rationale="Look for scheduling and lifecycle.",
                ),
                _q(
                    "Tell me about a conflict at work.",
                    category="behavioral",
                    difficulty="easy",
                ),
            )
        )
        result = await agent.generate(JD, RESUME, ["Kubernetes"])

        assert "_parse_error" not in result
        assert [q["category"] for q in result["questions"]] == [
            "skill-gap",
            "behavioral",
        ]
        assert (
            result["questions"][0]["rationale"] == "Look for scheduling and lifecycle."
        )
        assert result["questions"][1]["rationale"] is None

    @pytest.mark.asyncio
    async def test_bare_list_and_markdown_fences_accepted(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                "```json\n" + json.dumps([_q("Q one?"), "Q two?"]) + "\n```"
            )
        )
        result = await agent.generate(JD, RESUME, [])
        assert [q["question"] for q in result["questions"]] == ["Q one?", "Q two?"]

    @pytest.mark.asyncio
    async def test_prompt_inputs(self, agent):
        chain = FakeChain(response=_response(_q()))
        agent.chain = chain

        await agent.generate(JD, RESUME, ["Kubernetes", "AWS"], count=6)

        sent = chain.calls[0]
        assert sent["count"] == 6
        assert sent["skill_gaps"] == "Kubernetes, AWS"
        assert "Senior Backend Engineer" in sent["jd"]

    @pytest.mark.asyncio
    async def test_no_gaps_is_stated_explicitly(self, agent):
        chain = FakeChain(response=_response(_q()))
        agent.chain = chain
        await agent.generate(JD, RESUME, None)
        assert chain.calls[0]["skill_gaps"] == "None identified"

    @pytest.mark.asyncio
    async def test_default_count(self, agent):
        chain = FakeChain(response=_response(_q()))
        agent.chain = chain
        await agent.generate(JD, RESUME, [])
        assert chain.calls[0]["count"] == DEFAULT_COUNT


class TestPrivacy:
    @pytest.mark.asyncio
    async def test_contact_details_never_reach_the_llm(self, agent):
        chain = FakeChain(response=_response(_q()))
        agent.chain = chain

        await agent.generate(JD, RESUME, [])

        resume_sent = chain.calls[0]["resume"]
        for secret in ("Aarav", "aarav@example.com", "99999", "linkedin", "Pune"):
            assert secret not in resume_sent
        # ...while the useful parts are there
        assert "PayCircle" in resume_sent
        assert "FastAPI" in resume_sent
        assert "Ledger" in resume_sent


class TestNormalisation:
    @pytest.mark.asyncio
    async def test_unknown_category_and_difficulty_defaulted(self, agent):
        agent.chain = FakeChain(
            response=_response(_q(category="trivia", difficulty="impossible"))
        )
        result = await agent.generate(JD, RESUME, [])
        assert result["questions"][0]["category"] == "technical"
        assert result["questions"][0]["difficulty"] == "medium"

    @pytest.mark.asyncio
    async def test_category_spelling_variants_recognised(self, agent):
        agent.chain = FakeChain(
            response=_response(
                _q("A?", category="skill_gap"),
                _q("B?", category="Skill Gap"),
                _q("C?", category="BEHAVIORAL"),
            )
        )
        result = await agent.generate(JD, RESUME, [])
        assert [q["category"] for q in result["questions"]] == [
            "skill-gap",
            "skill-gap",
            "behavioral",
        ]

    @pytest.mark.asyncio
    async def test_duplicates_blanks_and_junk_dropped(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                json.dumps(
                    {
                        "questions": [
                            _q("Same question?"),
                            _q("same question?"),
                            _q("   "),
                            {"category": "technical"},
                            None,
                            42,
                            _q("Different question?"),
                        ]
                    }
                )
            )
        )
        result = await agent.generate(JD, RESUME, [])
        assert [q["question"] for q in result["questions"]] == [
            "Same question?",
            "Different question?",
        ]

    @pytest.mark.asyncio
    async def test_capped_to_requested_count(self, agent):
        agent.chain = FakeChain(
            response=_response(*[_q(f"Question {i}?") for i in range(12)])
        )
        result = await agent.generate(JD, RESUME, [], count=5)
        assert len(result["questions"]) == 5

    @pytest.mark.asyncio
    async def test_count_clamped(self, agent):
        chain = FakeChain(response=_response(_q()))
        agent.chain = chain
        await agent.generate(JD, RESUME, [], count=500)
        assert chain.calls[0]["count"] == 15
        await agent.generate(JD, RESUME, [], count=0)
        assert chain.calls[1]["count"] == 1

    @pytest.mark.asyncio
    async def test_gaps_deduplicated_and_capped(self, agent):
        chain = FakeChain(response=_response(_q()))
        agent.chain = chain
        gaps = ["AWS", "aws", " ", None, *[f"Skill{i}" for i in range(20)]]

        await agent.generate(JD, RESUME, gaps)

        sent = chain.calls[0]["skill_gaps"].split(", ")
        assert sent[0] == "AWS"
        assert sent.count("AWS") == 1
        assert len(sent) == MAX_GAPS


class TestFallback:
    @pytest.mark.asyncio
    async def test_llm_exception(self, agent):
        agent.chain = FakeChain(exc=Exception("Groq rate limit"))
        result = await agent.generate(JD, RESUME, [])
        assert result["questions"] == []
        assert result["_parse_error"] is True
        assert "rate limit" in result["_error_reason"]

    @pytest.mark.asyncio
    async def test_unparseable_output(self, agent):
        agent.chain = FakeChain(response=FakeMsg("Here are some great questions!"))
        result = await agent.generate(JD, RESUME, [])
        assert result["_parse_error"] is True
        assert result["questions"] == []

    @pytest.mark.asyncio
    async def test_json_with_no_usable_questions(self, agent):
        agent.chain = FakeChain(response=FakeMsg('{"questions": [{"category": "x"}]}'))
        result = await agent.generate(JD, RESUME, [])
        assert result["_parse_error"] is True

    @pytest.mark.asyncio
    async def test_wrong_json_shape(self, agent):
        agent.chain = FakeChain(response=FakeMsg('{"questions": "none"}'))
        result = await agent.generate(JD, RESUME, [])
        assert result["_parse_error"] is True

    @pytest.mark.asyncio
    async def test_empty_input_skips_the_llm(self, agent):
        chain = FakeChain(response=_response(_q()))
        agent.chain = chain
        result = await agent.generate({}, {}, [])
        assert result["_parse_error"] is True
        assert result["_error_reason"] == "empty_input"
        assert chain.calls == []

    @pytest.mark.asyncio
    async def test_non_dict_inputs_do_not_raise(self, agent):
        agent.chain = FakeChain(response=_response(_q()))
        result = await agent.generate(None, RESUME, "not a list")
        assert len(result["questions"]) == 1
