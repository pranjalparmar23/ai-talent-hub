"""Unit tests for ATSAgent.

Tests focus on:
  - Correct parsing of scored responses
  - Score clamping (LLM occasionally returns 150 or -10)
  - Score coercion (string "85" → int 85)
  - Fallback shape and list-field coercion
  - Edge cases (empty resume, empty JD)
"""

import pytest

from app.agents.candidate.ats_agent import ATSAgent
from tests.unit.agents._fakes import FakeChain, FakeMsg


@pytest.fixture
def agent():
    return ATSAgent()


@pytest.fixture
def resume_data():
    return {
        "skills": ["Python", "FastAPI", "PostgreSQL"],
        "experience_years": 3,
        "experience": [
            {
                "role": "Backend Engineer",
                "company": "TechCorp",
                "highlights": ["Built APIs"],
            }
        ],
        "education": [{"degree": "B.Tech", "institution": "NIT"}],
        "projects": [],
    }


@pytest.fixture
def jd_data():
    return {
        "title": "Senior Backend Engineer",
        "role_level": "senior",
        "experience_years_min": 5,
        "skills_required": ["Python", "Kubernetes", "AWS", "PostgreSQL", "Redis"],
        "skills_preferred": ["FastAPI"],
    }


# ── Happy path ────────────────────────────────────────────────────────


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_parses_full_response(self, agent, resume_data, jd_data):
        agent.chain = FakeChain(
            response=FakeMsg(
                """
        {
            "ats_score": 55,
            "matching_keywords": ["Python", "PostgreSQL"],
            "missing_keywords": ["Kubernetes", "AWS", "Redis"],
            "formatting_issues": [],
            "recommendations": ["Add Kubernetes experience", "Include AWS in skills"],
            "summary": "Partial match — strong Python, missing cloud skills."
        }
        """
            )
        )

        result = await agent.analyze(resume_data, jd_data)

        assert result["ats_score"] == 55
        assert "Python" in result["matching_keywords"]
        assert "Kubernetes" in result["missing_keywords"]
        assert len(result["recommendations"]) == 2
        assert not result.get("_parse_error")

    @pytest.mark.asyncio
    async def test_handles_markdown_wrapped_response(self, agent, resume_data, jd_data):
        agent.chain = FakeChain(
            response=FakeMsg(
                """```json
{"ats_score": 72, "matching_keywords": ["Python"], "missing_keywords": [],
 "formatting_issues": [], "recommendations": [], "summary": "Good fit."}
```"""
            )
        )

        result = await agent.analyze(resume_data, jd_data)

        assert result["ats_score"] == 72
        assert result["summary"] == "Good fit."


# ── Score clamping ────────────────────────────────────────────────────


class TestScoreClamping:
    @pytest.mark.asyncio
    async def test_clamps_score_above_100(self, agent, resume_data, jd_data):
        agent.chain = FakeChain(
            response=FakeMsg(
                """
        {"ats_score": 150, "matching_keywords": [], "missing_keywords": [],
         "formatting_issues": [], "recommendations": [], "summary": ""}
        """
            )
        )

        result = await agent.analyze(resume_data, jd_data)

        assert result["ats_score"] == 100

    @pytest.mark.asyncio
    async def test_clamps_negative_score(self, agent, resume_data, jd_data):
        agent.chain = FakeChain(
            response=FakeMsg(
                """
        {"ats_score": -20, "matching_keywords": [], "missing_keywords": [],
         "formatting_issues": [], "recommendations": [], "summary": ""}
        """
            )
        )

        result = await agent.analyze(resume_data, jd_data)

        assert result["ats_score"] == 0

    @pytest.mark.asyncio
    async def test_coerces_string_score(self, agent, resume_data, jd_data):
        """LLM sometimes returns "85" as a string."""
        agent.chain = FakeChain(
            response=FakeMsg(
                """
        {"ats_score": "85", "matching_keywords": [], "missing_keywords": [],
         "formatting_issues": [], "recommendations": [], "summary": ""}
        """
            )
        )

        result = await agent.analyze(resume_data, jd_data)

        assert result["ats_score"] == 85

    @pytest.mark.asyncio
    async def test_defaults_invalid_score_to_zero(self, agent, resume_data, jd_data):
        agent.chain = FakeChain(
            response=FakeMsg(
                """
        {"ats_score": "not a number", "matching_keywords": [], "missing_keywords": [],
         "formatting_issues": [], "recommendations": [], "summary": ""}
        """
            )
        )

        result = await agent.analyze(resume_data, jd_data)

        assert result["ats_score"] == 0


# ── List field coercion ──────────────────────────────────────────────


class TestListCoercion:
    @pytest.mark.asyncio
    async def test_coerces_non_list_keywords_to_empty(
        self, agent, resume_data, jd_data
    ):
        """LLM occasionally returns strings where lists should be."""
        agent.chain = FakeChain(
            response=FakeMsg(
                """
        {"ats_score": 50, "matching_keywords": "Python and FastAPI",
         "missing_keywords": [], "formatting_issues": [],
         "recommendations": [], "summary": ""}
        """
            )
        )

        result = await agent.analyze(resume_data, jd_data)

        assert result["matching_keywords"] == []


# ── Fallback / degradation ────────────────────────────────────────────


class TestFallback:
    @pytest.mark.asyncio
    async def test_empty_resume_returns_fallback(self, agent, jd_data):
        result = await agent.analyze({}, jd_data)

        assert result["_parse_error"] is True
        assert result["ats_score"] == 0

    @pytest.mark.asyncio
    async def test_empty_jd_returns_fallback(self, agent, resume_data):
        result = await agent.analyze(resume_data, {})

        assert result["_parse_error"] is True

    @pytest.mark.asyncio
    async def test_malformed_llm_response_returns_fallback(
        self, agent, resume_data, jd_data
    ):
        agent.chain = FakeChain(response=FakeMsg("I don't know how to score this"))

        result = await agent.analyze(resume_data, jd_data)

        assert result["_parse_error"] is True
        # Fallback shape still complete
        assert result["ats_score"] == 0
        assert isinstance(result["matching_keywords"], list)
        assert isinstance(result["recommendations"], list)

    @pytest.mark.asyncio
    async def test_llm_api_error_returns_fallback(self, agent, resume_data, jd_data):
        agent.chain = FakeChain(exc=Exception("Groq timeout"))

        result = await agent.analyze(resume_data, jd_data)

        assert result["_parse_error"] is True
        assert "Groq timeout" in result.get("_error_reason", "")


# ── Input trimming ────────────────────────────────────────────────────


class TestInputTrimming:
    @pytest.mark.asyncio
    async def test_trims_experience_to_top_5(self, agent, jd_data):
        """Resume with 10 experiences should send only top 5 to the LLM."""
        big_resume = {
            "skills": ["Python"],
            "experience_years": 5,
            "experience": [
                {
                    "role": f"Role {i}",
                    "company": f"Co {i}",
                    "highlights": ["A", "B", "C", "D"],
                }
                for i in range(10)
            ],
            "education": [],
            "projects": [],
        }
        fake = FakeChain(
            response=FakeMsg(
                '{"ats_score": 50, "matching_keywords": [], "missing_keywords": [], "formatting_issues": [], "recommendations": [], "summary": ""}'
            )
        )
        agent.chain = fake

        await agent.analyze(big_resume, jd_data)

        # Verify the LLM saw a trimmed resume
        import json

        called_resume = json.loads(fake.calls[0]["resume"])
        assert len(called_resume["experience"]) == 5
        # Highlights capped at 3 per role
        assert len(called_resume["experience"][0]["highlights"]) == 3
