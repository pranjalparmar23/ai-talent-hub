"""Unit tests for SkillGapAgent.

Tests focus on:
  - Fuzzy match handling in LLM output
  - Gap percentage recomputed in code (not trusted from LLM)
  - Edge cases: empty JD skills, empty candidate skills
  - priority_missing capped at 5
  - Fallback shape
"""

import pytest

from app.agents.candidate.skill_gap_agent import SkillGapAgent
from tests.unit.agents._fakes import FakeChain, FakeMsg


@pytest.fixture
def agent():
    return SkillGapAgent()


# ── Happy path ────────────────────────────────────────────────────────


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_parses_full_response(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                """
        {
            "matching_skills": ["Python", "PostgreSQL"],
            "missing_skills": ["Kubernetes", "AWS"],
            "gap_percentage": 50,
            "priority_missing": ["Kubernetes", "AWS"],
            "notes": "Strong core, missing cloud."
        }
        """
            )
        )

        result = await agent.analyze(
            candidate_skills=["Python", "PostgreSQL", "FastAPI"],
            jd_skills=["Python", "PostgreSQL", "Kubernetes", "AWS"],
        )

        assert result["matching_skills"] == ["Python", "PostgreSQL"]
        assert result["missing_skills"] == ["Kubernetes", "AWS"]
        assert result["notes"] == "Strong core, missing cloud."


# ── Gap percentage recomputation ─────────────────────────────────────


class TestGapRecomputation:
    """SkillGapAgent recomputes gap_percentage from actual list counts,
    ignoring whatever the LLM said. LLMs are bad at arithmetic."""

    @pytest.mark.asyncio
    async def test_recomputes_when_llm_math_is_wrong(self, agent):
        """LLM claims 60% but math says 25%."""
        agent.chain = FakeChain(
            response=FakeMsg(
                """
        {"matching_skills": ["A", "B", "C"],
         "missing_skills": ["D"],
         "gap_percentage": 60,
         "priority_missing": ["D"],
         "notes": ""}
        """
            )
        )

        result = await agent.analyze(
            candidate_skills=["a", "b", "c"],
            jd_skills=["A", "B", "C", "D"],  # 4 required, 1 missing
        )

        # Should be 1/4 = 25%, not the LLM's 60
        assert result["gap_percentage"] == 25.0

    @pytest.mark.asyncio
    async def test_perfect_match_gives_zero_gap(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                """
        {"matching_skills": ["Python", "FastAPI"],
         "missing_skills": [],
         "gap_percentage": 0,
         "priority_missing": [], "notes": ""}
        """
            )
        )

        result = await agent.analyze(
            candidate_skills=["Python", "FastAPI"],
            jd_skills=["Python", "FastAPI"],
        )

        assert result["gap_percentage"] == 0.0


# ── Edge cases (no LLM call) ─────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_jd_skills_returns_zero_gap(self, agent):
        """No JD skills = nothing to gap against."""
        result = await agent.analyze(
            candidate_skills=["Python"],
            jd_skills=[],
        )

        assert result["gap_percentage"] == 0.0
        assert result["missing_skills"] == []
        assert result["matching_skills"] == []

    @pytest.mark.asyncio
    async def test_empty_candidate_returns_100_percent_gap(self, agent):
        """No candidate skills = everything missing."""
        result = await agent.analyze(
            candidate_skills=[],
            jd_skills=["Python", "Docker", "AWS"],
        )

        assert result["gap_percentage"] == 100.0
        assert result["missing_skills"] == ["Python", "Docker", "AWS"]
        assert result["priority_missing"] == ["Python", "Docker", "AWS"]

    @pytest.mark.asyncio
    async def test_priority_missing_capped_at_5(self, agent):
        """LLM occasionally ignores the 3-5 cap. We enforce it."""
        agent.chain = FakeChain(
            response=FakeMsg(
                """
        {"matching_skills": [],
         "missing_skills": ["a", "b", "c", "d", "e", "f", "g", "h"],
         "gap_percentage": 100,
         "priority_missing": ["a", "b", "c", "d", "e", "f", "g", "h"],
         "notes": ""}
        """
            )
        )

        result = await agent.analyze(
            candidate_skills=["z"],
            jd_skills=["a", "b", "c", "d", "e", "f", "g", "h"],
        )

        assert len(result["priority_missing"]) == 5


# ── Input coercion ────────────────────────────────────────────────────


class TestInputCoercion:
    @pytest.mark.asyncio
    async def test_none_inputs_treated_as_empty(self, agent):
        """Graph might pass None if the LLM parse failed upstream."""
        result = await agent.analyze(candidate_skills=None, jd_skills=None)

        # Both empty → gap_percentage = 0 (nothing to gap)
        assert result["gap_percentage"] == 0.0

    @pytest.mark.asyncio
    async def test_mixed_content_filtered(self, agent):
        """Non-string items and empty strings dropped from inputs."""
        # Both branches short-circuit before LLM if either list ends up empty
        # We provide clean data on JD side and messy on candidate
        agent.chain = FakeChain(
            response=FakeMsg(
                """
        {"matching_skills": ["Python"], "missing_skills": ["AWS"],
         "gap_percentage": 50, "priority_missing": ["AWS"], "notes": ""}
        """
            )
        )

        result = await agent.analyze(
            candidate_skills=["Python", "", None, "  ", " FastAPI "],
            jd_skills=["Python", "AWS"],
        )

        # Should have called LLM (both lists non-empty after cleaning)
        assert result["gap_percentage"] == 50.0


# ── Fallback ─────────────────────────────────────────────────────────


class TestFallback:
    @pytest.mark.asyncio
    async def test_malformed_llm_response_returns_fallback(self, agent):
        agent.chain = FakeChain(response=FakeMsg("cannot analyze"))

        result = await agent.analyze(
            candidate_skills=["Python"],
            jd_skills=["Kubernetes"],
        )

        assert result.get("_parse_error") is True
        # Shape guarantees
        assert isinstance(result["matching_skills"], list)
        assert isinstance(result["missing_skills"], list)
        assert isinstance(result["gap_percentage"], float)

    @pytest.mark.asyncio
    async def test_llm_api_error_returns_fallback(self, agent):
        agent.chain = FakeChain(exc=Exception("API down"))

        result = await agent.analyze(
            candidate_skills=["Python"],
            jd_skills=["Kubernetes"],
        )

        assert result.get("_parse_error") is True
        assert "API down" in result.get("_error_reason", "")
