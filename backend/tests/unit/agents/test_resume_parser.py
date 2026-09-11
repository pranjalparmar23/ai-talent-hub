"""Unit tests for ResumeParserAgent.

Uses mocked LLM responses — no real Groq calls. Tests focus on:
  - Correct JSON parsing on happy path
  - Graceful degradation on malformed LLM output
  - Input validation (empty, oversized, whitespace)
  - Fallback dict shape matches the real schema
"""

import pytest

from app.agents.candidate.resume_parser import ResumeParserAgent
from tests.unit.agents._fakes import FakeChain, FakeMsg


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def agent():
    return ResumeParserAgent()


@pytest.fixture
def valid_llm_response():
    """A realistic LLM response — clean JSON, all fields present."""
    return '''
    {
        "name": "Aarav Mehta",
        "email": "aarav@example.com",
        "phone": "+91-9876543210",
        "location": "Bengaluru",
        "linkedin": "linkedin.com/in/aarav",
        "github": "github.com/aarav",
        "summary": "Backend engineer with 3 years experience",
        "skills": ["Python", "FastAPI", "PostgreSQL"],
        "experience_years": 3,
        "experience": [
            {
                "company": "TechCorp",
                "role": "Backend Engineer",
                "start_date": "2023-01",
                "end_date": "Present",
                "location": "Bangalore",
                "highlights": ["Built REST APIs", "Scaled to 5k RPS"]
            }
        ],
        "education": [
            {
                "institution": "NIT Warangal",
                "degree": "B.Tech",
                "field": "CS",
                "start_date": "2018",
                "end_date": "2022",
                "gpa": "8.4"
            }
        ],
        "projects": [],
        "certifications": []
    }
    '''


@pytest.fixture
def markdown_wrapped_response(valid_llm_response):
    """LLM output wrapped in markdown fences — common Groq behavior."""
    return f"```json\n{valid_llm_response}\n```"



# ── Happy path ────────────────────────────────────────────────────────

class TestHappyPath:
    @pytest.mark.asyncio
    async def test_parses_clean_json(self, agent, valid_llm_response):
        agent.chain = FakeChain(response=FakeMsg(valid_llm_response))
        result = await agent.parse("Some resume text")

        assert result["name"] == "Aarav Mehta"
        assert result["email"] == "aarav@example.com"
        assert result["experience_years"] == 3
        assert "Python" in result["skills"]
        assert not result.get("_parse_error")

    @pytest.mark.asyncio
    async def test_parses_markdown_wrapped_json(self, agent, markdown_wrapped_response):
        """Groq loves to wrap JSON in ```json ... ``` fences."""
        agent.chain = FakeChain(response=FakeMsg(markdown_wrapped_response))
        result = await agent.parse("Some resume text")

        assert result["name"] == "Aarav Mehta"
        assert not result.get("_parse_error")

    @pytest.mark.asyncio
    async def test_parses_preamble_and_postamble(self, agent):
        """LLM sometimes adds 'Here you go:' before and 'Hope this helps!' after."""
        content = (
            'Sure! Here is the parsed resume: '
            '{"name": "Test", "skills": ["Python"], "experience_years": 2} '
            '— hope this helps!'
        )
        agent.chain = FakeChain(response=FakeMsg(content))
        result = await agent.parse("Some resume text")

        assert result["name"] == "Test"
        assert result["skills"] == ["Python"]


# ── Fallback / degradation ────────────────────────────────────────────

class TestFallback:
    @pytest.mark.asyncio
    async def test_empty_text_returns_fallback(self, agent):
        result = await agent.parse("")

        assert result["_parse_error"] is True
        assert result.get("_error_reason") == "empty_input"
        # Shape must still match the schema — downstream code iterates these
        assert result["skills"] == []
        assert result["experience"] == []
        assert result["experience_years"] == 0

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_fallback(self, agent):
        result = await agent.parse("   \n\t  ")

        assert result["_parse_error"] is True

    @pytest.mark.asyncio
    async def test_malformed_llm_response_returns_fallback(self, agent):
        """LLM returns something that isn't parseable JSON."""
        agent.chain = FakeChain(response=FakeMsg("I cannot parse this resume"))
        result = await agent.parse("Some resume text")

        assert result["_parse_error"] is True
        assert "_raw_output" in result
        # Still has the full schema shape
        assert result["skills"] == []
        assert result["name"] is None

    @pytest.mark.asyncio
    async def test_llm_api_error_returns_fallback(self, agent):
        """Groq rate limit or network failure — must not crash the request."""
        agent.chain = FakeChain(exc=Exception("Groq rate limit"))
        result = await agent.parse("Some resume text")

        assert result["_parse_error"] is True
        assert "Groq rate limit" in result.get("_error_reason", "")


# ── Input handling ────────────────────────────────────────────────────

class TestInputHandling:
    @pytest.mark.asyncio
    async def test_truncates_oversized_input(self, agent, valid_llm_response):
        """A 100k-char resume should be truncated to 50k before sending to Groq."""
        huge_text = "x" * 100_000
        fake = FakeChain(response=FakeMsg(valid_llm_response))
        agent.chain = fake

        await agent.parse(huge_text)

        # Verify the LLM only saw the truncated version
        assert len(fake.calls) == 1
        assert len(fake.calls[0]["resume_text"]) == 50_000


# ── Fallback dict shape ───────────────────────────────────────────────

class TestFallbackShape:
    @pytest.mark.asyncio
    async def test_fallback_matches_real_schema(self, agent):
        """Fallback dict must have every key downstream code expects."""
        result = await agent.parse("")

        required_keys = {
            "name", "email", "phone", "location", "linkedin", "github",
            "summary", "skills", "experience_years", "experience",
            "education", "projects", "certifications",
        }
        assert required_keys.issubset(result.keys())

        # All list fields must be lists (never None)
        assert isinstance(result["skills"], list)
        assert isinstance(result["experience"], list)
        assert isinstance(result["education"], list)
        assert isinstance(result["projects"], list)
        assert isinstance(result["certifications"], list)