"""Unit tests for JDAnalyzerAgent (LLM replaced by FakeChain)."""

import json

import pytest

from app.agents.recruiter.jd_analyzer import JDAnalyzerAgent
from tests.unit.agents._fakes import FakeChain, FakeMsg

JD_TEXT = "Senior Backend Engineer. Python, PostgreSQL, Docker. 5+ years."


def _llm_json(**overrides):
    data = {
        "title": "Senior Backend Engineer",
        "company": "Lumen Cloud",
        "location": "Remote India",
        "role_level": "senior",
        "employment_type": "full-time",
        "experience_years_min": 5,
        "skills_required": ["Python", "PostgreSQL"],
        "skills_preferred": ["Kafka"],
        "responsibilities": ["Build ingestion services"],
        "qualifications": ["BS in Computer Science"],
    }
    data.update(overrides)
    return FakeMsg(json.dumps(data))


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    return JDAnalyzerAgent()


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_parses_a_clean_response(self, agent):
        agent.chain = FakeChain(response=_llm_json())
        result = await agent.analyze(JD_TEXT)
        assert result["title"] == "Senior Backend Engineer"
        assert result["company"] == "Lumen Cloud"
        assert result["role_level"] == "senior"
        assert result["employment_type"] == "full-time"
        assert result["experience_years_min"] == 5
        assert result["skills_required"] == ["Python", "PostgreSQL"]
        assert result["skills_preferred"] == ["Kafka"]
        assert not result.get("_parse_error")

    @pytest.mark.asyncio
    async def test_jd_text_is_sent_to_the_llm(self, agent):
        chain = FakeChain(response=_llm_json())
        agent.chain = chain
        await agent.analyze(JD_TEXT)
        assert chain.calls == [{"jd_text": JD_TEXT}]

    @pytest.mark.asyncio
    async def test_markdown_fenced_json_is_accepted(self, agent):
        fenced = "```json\n" + _llm_json().content + "\n```"
        agent.chain = FakeChain(response=FakeMsg(fenced))
        result = await agent.analyze(JD_TEXT)
        assert result["title"] == "Senior Backend Engineer"

    @pytest.mark.asyncio
    async def test_text_around_the_json_is_ignored(self, agent):
        wrapped = "Here is the analysis: " + _llm_json().content + " Hope it helps!"
        agent.chain = FakeChain(response=FakeMsg(wrapped))
        assert (await agent.analyze(JD_TEXT))["company"] == "Lumen Cloud"


class TestInputGuards:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["", "   \n\t", None])
    async def test_empty_input_skips_the_llm(self, agent, text):
        chain = FakeChain(response=_llm_json())
        agent.chain = chain
        result = await agent.analyze(text)
        assert result["_parse_error"] is True
        assert result["_error_reason"] == "empty_input"
        assert chain.calls == []

    @pytest.mark.asyncio
    async def test_very_long_text_is_truncated(self, agent):
        chain = FakeChain(response=_llm_json())
        agent.chain = chain
        await agent.analyze("a" * 50_000)
        assert len(chain.calls[0]["jd_text"]) == 30_000

    @pytest.mark.asyncio
    async def test_text_at_the_limit_is_not_truncated(self, agent):
        chain = FakeChain(response=_llm_json())
        agent.chain = chain
        await agent.analyze("a" * 30_000)
        assert len(chain.calls[0]["jd_text"]) == 30_000


class TestFailures:
    @pytest.mark.asyncio
    async def test_llm_exception_gives_fallback(self, agent):
        agent.chain = FakeChain(exc=RuntimeError("Groq rate limit " + "x" * 500))
        result = await agent.analyze(JD_TEXT)
        assert result["_parse_error"] is True
        assert result["skills_required"] == []
        assert result["_error_reason"].startswith("Groq rate limit")
        assert len(result["_error_reason"]) <= 200

    @pytest.mark.asyncio
    async def test_unparseable_output_gives_fallback_with_raw_text(self, agent):
        agent.chain = FakeChain(response=FakeMsg("I cannot help with that." * 50))
        result = await agent.analyze(JD_TEXT)
        assert result["_parse_error"] is True
        assert result["skills_required"] == []
        assert len(result["_raw_output"]) <= 500

    @pytest.mark.asyncio
    @pytest.mark.parametrize("content", ['["python", "go"]', '"hello"', "42", "null"])
    async def test_valid_json_that_is_not_an_object_gives_fallback(
        self, agent, content
    ):
        agent.chain = FakeChain(response=FakeMsg(content))
        result = await agent.analyze(JD_TEXT)
        assert result["_parse_error"] is True
        assert result["_error_reason"] == "not_a_json_object"
        assert result["skills_required"] == []

    @pytest.mark.asyncio
    async def test_fallback_is_never_shared_between_calls(self, agent):
        agent.chain = FakeChain(exc=RuntimeError("down"))
        first = await agent.analyze(JD_TEXT)
        first["skills_required"].append("mutated")
        second = await agent.analyze(JD_TEXT)
        assert second["skills_required"] == []


class TestNormalization:
    @pytest.mark.asyncio
    async def test_skill_lists_are_trimmed_and_deduplicated(self, agent):
        agent.chain = FakeChain(
            response=_llm_json(
                skills_required=[" Python ", "python", "PYTHON", "", "  ", "Go"],
                skills_preferred=["Kafka", "kafka", 7, None, "Redis"],
            )
        )
        result = await agent.analyze(JD_TEXT)
        assert result["skills_required"] == ["Python", "Go"]
        assert result["skills_preferred"] == ["Kafka", "Redis"]

    @pytest.mark.asyncio
    async def test_sentences_keep_their_case_but_are_deduplicated(self, agent):
        agent.chain = FakeChain(
            response=_llm_json(
                responsibilities=["Own the K8s model", "own the k8s model", " Mentor "],
                qualifications=["BS in CS", "bs in cs"],
            )
        )
        result = await agent.analyze(JD_TEXT)
        assert result["responsibilities"] == ["Own the K8s model", "Mentor"]
        assert result["qualifications"] == ["BS in CS"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["python", {"a": 1}, 5, None])
    async def test_non_list_skill_fields_become_empty_lists(self, agent, bad):
        agent.chain = FakeChain(
            response=_llm_json(skills_required=bad, skills_preferred=bad)
        )
        result = await agent.analyze(JD_TEXT)
        assert result["skills_required"] == []
        assert result["skills_preferred"] == []

    @pytest.mark.asyncio
    async def test_missing_fields_are_filled_in(self, agent):
        agent.chain = FakeChain(response=FakeMsg('{"title": "Dev"}'))
        result = await agent.analyze(JD_TEXT)
        assert result["skills_required"] == []
        assert result["responsibilities"] == []
        assert result["experience_years_min"] is None
        assert result["role_level"] is None
        assert result["company"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Senior Level", "senior-level"),
            ("Full_Time", "full-time"),
            ("PART TIME", "part-time"),
            ("  mid  ", "mid"),
            ("Lead!!", "lead"),
            ("--x--", "x"),
            ("", None),
            ("   ", None),
            ("!!!", None),
            (5, None),
            (None, None),
        ],
    )
    async def test_role_level_and_employment_type_become_slugs(
        self, agent, raw, expected
    ):
        agent.chain = FakeChain(response=_llm_json(role_level=raw, employment_type=raw))
        result = await agent.analyze(JD_TEXT)
        assert result["role_level"] == expected
        assert result["employment_type"] == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (3, 3),
            (3.0, 3),
            (3.9, 3),
            ("3", 3),
            ("3+", 3),
            ("5+ years experience", 5),
            ("2.5 years", 2),
            (0, 0),
            (-4, 0),
            (True, None),
            ("none", None),
            ("", None),
            (None, None),
            ([3], None),
        ],
    )
    async def test_minimum_years_become_a_non_negative_integer(
        self, agent, raw, expected
    ):
        agent.chain = FakeChain(response=_llm_json(experience_years_min=raw))
        assert (await agent.analyze(JD_TEXT))["experience_years_min"] == expected

    @pytest.mark.asyncio
    async def test_text_fields_are_trimmed_and_blank_becomes_none(self, agent):
        agent.chain = FakeChain(
            response=_llm_json(title="  Staff Engineer ", company="   ", location=9)
        )
        result = await agent.analyze(JD_TEXT)
        assert result["title"] == "Staff Engineer"
        assert result["company"] is None
        assert result["location"] is None
