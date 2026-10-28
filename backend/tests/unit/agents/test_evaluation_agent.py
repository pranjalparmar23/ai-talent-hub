"""Unit tests for EvaluationAgent.

Focus:
  - overall_score is computed in code, never trusted from the LLM
  - scores are clamped to 0-10; missing dimensions are filled sensibly
  - every failure mode returns the fallback shape (never raises)
"""

import pytest

from app.agents.shared.evaluation_agent import (
    DIMENSIONS,
    EvaluationAgent,
)
from tests.unit.agents._fakes import FakeChain, FakeMsg

HISTORY = [
    {"role": "ai", "content": "Tell me about a hard bug."},
    {"role": "human", "content": "I fixed a race condition in our queue consumer."},
]


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    return EvaluationAgent()


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_flat_json_normalized(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                """
        {"communication": 8, "technical_knowledge": 6, "problem_solving": 7,
         "confidence": 7, "overall": 10,
         "strengths": ["Clear structure"], "improvements": ["Quantify impact"],
         "summary": "Solid answer."}
        """
            )
        )

        result = await agent.evaluate_interview(
            HISTORY, company="Amazon", role="SDE II"
        )

        assert result["scores"] == {
            "communication": 8.0,
            "technical_knowledge": 6.0,
            "problem_solving": 7.0,
            "confidence": 7.0,
        }
        # mean of the four = 7.0 — the LLM's own "overall": 10 is ignored
        assert result["overall_score"] == 7.0
        assert result["strengths"] == ["Clear structure"]
        assert result["improvements"] == ["Quantify impact"]
        assert result["summary"] == "Solid answer."
        assert "_parse_error" not in result

    @pytest.mark.asyncio
    async def test_nested_scores_object_accepted(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                '{"scores": {"communication": 5, "technical_knowledge": 5,'
                ' "problem_solving": 5, "confidence": 5}}'
            )
        )
        result = await agent.evaluate_interview(HISTORY)
        assert result["overall_score"] == 5.0

    @pytest.mark.asyncio
    async def test_markdown_fenced_json_parsed(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                '```json\n{"communication": 9, "technical_knowledge": 9,'
                ' "problem_solving": 9, "confidence": 9}\n```'
            )
        )
        result = await agent.evaluate_interview(HISTORY)
        assert result["overall_score"] == 9.0

    @pytest.mark.asyncio
    async def test_prompt_receives_dialogue_and_context(self, agent):
        chain = FakeChain(
            response=FakeMsg(
                '{"communication": 1, "technical_knowledge": 1,'
                ' "problem_solving": 1, "confidence": 1}'
            )
        )
        agent.chain = chain

        await agent.evaluate_interview(HISTORY, company="Amazon", role="SDE II")

        sent = chain.calls[0]
        assert sent["company"] == "Amazon"
        assert sent["role"] == "SDE II"
        assert "Interviewer: Tell me about a hard bug." in sent["history"]
        assert "Candidate: I fixed a race condition" in sent["history"]


class TestNormalization:
    @pytest.mark.asyncio
    async def test_scores_clamped_to_0_10(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                '{"communication": 15, "technical_knowledge": -3,'
                ' "problem_solving": 5, "confidence": 5}'
            )
        )
        result = await agent.evaluate_interview(HISTORY)
        assert result["scores"]["communication"] == 10.0
        assert result["scores"]["technical_knowledge"] == 0.0

    @pytest.mark.asyncio
    async def test_missing_dimension_filled_with_mean_of_others(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                '{"communication": 8, "technical_knowledge": 6,'
                ' "problem_solving": "n/a"}'
            )
        )
        result = await agent.evaluate_interview(HISTORY)
        assert result["scores"]["problem_solving"] == 7.0
        assert result["scores"]["confidence"] == 7.0

    @pytest.mark.asyncio
    async def test_string_numbers_coerced(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                '{"communication": "7.5", "technical_knowledge": "7.5",'
                ' "problem_solving": "7.5", "confidence": "7.5"}'
            )
        )
        result = await agent.evaluate_interview(HISTORY)
        assert result["overall_score"] == 7.5

    @pytest.mark.asyncio
    async def test_lists_capped_and_cleaned(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                '{"communication": 5, "technical_knowledge": 5,'
                ' "problem_solving": 5, "confidence": 5,'
                ' "strengths": ["a", "b", "", null, "c", "d", "e", "f"],'
                ' "improvements": "not a list"}'
            )
        )
        result = await agent.evaluate_interview(HISTORY)
        assert result["strengths"] == ["a", "b", "c", "d"]
        assert result["improvements"] == []

    @pytest.mark.asyncio
    async def test_missing_summary_is_none(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                '{"communication": 5, "technical_knowledge": 5,'
                ' "problem_solving": 5, "confidence": 5}'
            )
        )
        result = await agent.evaluate_interview(HISTORY)
        assert result["summary"] is None


class TestFallback:
    @pytest.mark.asyncio
    async def test_unparseable_output(self, agent):
        agent.chain = FakeChain(response=FakeMsg("I think they did great!"))
        result = await agent.evaluate_interview(HISTORY)

        assert result["_parse_error"] is True
        assert result["overall_score"] == 0.0
        assert set(result["scores"]) == set(DIMENSIONS)

    @pytest.mark.asyncio
    async def test_json_without_any_scores(self, agent):
        agent.chain = FakeChain(response=FakeMsg('{"summary": "hmm"}'))
        result = await agent.evaluate_interview(HISTORY)
        assert result["_parse_error"] is True

    @pytest.mark.asyncio
    async def test_non_object_json(self, agent):
        agent.chain = FakeChain(response=FakeMsg("[1, 2, 3]"))
        result = await agent.evaluate_interview(HISTORY)
        assert result["_parse_error"] is True

    @pytest.mark.asyncio
    async def test_llm_exception(self, agent):
        agent.chain = FakeChain(exc=Exception("Groq rate limit"))
        result = await agent.evaluate_interview(HISTORY)
        assert result["_parse_error"] is True

    @pytest.mark.asyncio
    async def test_fallback_is_not_shared_between_calls(self, agent):
        agent.chain = FakeChain(exc=Exception("boom"))
        first = await agent.evaluate_interview(HISTORY)
        first["scores"]["communication"] = 99
        first["strengths"].append("mutated")

        second = await agent.evaluate_interview(HISTORY)
        assert second["scores"]["communication"] == 0.0
        assert second["strengths"] == []

    @pytest.mark.asyncio
    async def test_empty_history_still_safe(self, agent):
        chain = FakeChain(exc=Exception("boom"))
        agent.chain = chain
        await agent.evaluate_interview([])
        assert chain.calls[0]["history"] == "(empty transcript)"
