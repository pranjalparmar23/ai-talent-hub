"""Unit tests for HiringRecommendationAgent.

The verdict is pure code, so those tests are exact and need no fake LLM at all.
The explanation (LLM) is tested separately, including the fallback template.
"""

import json

import pytest

from app.agents.recruiter.recommendation_agent import (
    CONSIDER_THRESHOLD,
    HIRE_THRESHOLD,
    HiringRecommendationAgent,
)
from tests.unit.agents._fakes import FakeChain, FakeMsg

decide = HiringRecommendationAgent.decide

CANDIDATE = {
    "name": "Aarav Mehta",
    "email": "aarav@example.com",
    "phone": "+91-99999-00000",
    "location": "Pune",
    "skills": ["Python", "FastAPI"],
    "experience_years": 3,
    "experience": [{"role": "Backend Engineer", "company": "PayCircle"}],
}
MATCH = {
    "match_score": 80.0,
    "skill_match": 75.0,
    "experience_match": 60.0,
    "semantic_match": 90.0,
    "matching_skills": ["Python", "PostgreSQL"],
    "missing_skills": ["Kubernetes"],
}
JD = {"title": "Senior Backend Engineer"}
LLM_OK = FakeMsg(
    json.dumps(
        {
            "reasoning": "Strong skill coverage and a solid interview.",
            "strengths": ["Python depth", "Good communicator"],
            "risks": ["No Kubernetes"],
        }
    )
)


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    a = HiringRecommendationAgent()
    a.chain = FakeChain(response=LLM_OK)
    return a


# ── The decision (no LLM involved) ────────────────────────────────────


class TestDecide:
    def test_combined_blends_match_and_interview(self):
        verdict, combined, _, flags = decide(80, 7)  # 0.6*80 + 0.4*70 = 76
        assert combined == pytest.approx(76.0)
        assert verdict == "HIRE"
        assert flags == []

    @pytest.mark.parametrize(
        "combined,expected",
        [
            (100, "HIRE"),
            (HIRE_THRESHOLD, "HIRE"),
            (HIRE_THRESHOLD - 0.1, "CONSIDER"),
            (CONSIDER_THRESHOLD, "CONSIDER"),
            (CONSIDER_THRESHOLD - 0.1, "REJECT"),
            (0, "REJECT"),
        ],
    )
    def test_thresholds_without_interview(self, combined, expected):
        assert decide(combined, None)[0] == expected

    def test_strong_resume_but_weak_interview_is_not_a_hire(self):
        # 0.6*100 + 0.4*45 = 78 clears the HIRE bar on the blend alone,
        # but the interview (4.5/10) is below the minimum of 5.
        verdict, combined, confidence, flags = decide(100, 4.5)
        assert combined >= HIRE_THRESHOLD
        assert verdict == "CONSIDER"
        assert "downgraded_weak_interview" in flags
        assert confidence <= 60

    def test_interview_exactly_at_bar_is_not_downgraded(self):
        verdict, _, _, flags = decide(100, 5.0)  # 0.6*100 + 0.4*50 = 80
        assert verdict == "HIRE"
        assert "downgraded_weak_interview" not in flags

    def test_great_interview_can_lift_a_borderline_resume(self):
        # 0.6*60 + 0.4*100 = 76 -> clears the HIRE bar
        verdict, combined, _, _ = decide(60, 10)
        assert combined == pytest.approx(76.0)
        assert verdict == "HIRE"
        # ...while the same resume with no interview stays CONSIDER
        assert decide(60, None)[0] == "CONSIDER"

    def test_no_interview_flagged_and_less_confident(self):
        _, _, with_interview, _ = decide(90, 9)
        _, _, without, flags = decide(90, None)
        assert "no_interview" in flags
        assert without < with_interview

    def test_interview_only(self):
        verdict, combined, _, flags = decide(None, 8)
        assert combined == 80
        assert verdict == "HIRE"
        assert "no_match_score" in flags

    def test_no_data_at_all(self):
        verdict, combined, confidence, flags = decide(None, None)
        assert (verdict, combined, confidence) == ("CONSIDER", None, 20)
        assert flags == ["insufficient_data"]

    def test_confidence_higher_when_clear_cut(self):
        _, _, clear, _ = decide(100, 10)  # far above the HIRE line
        _, _, borderline, _ = decide(75, 7.5)  # right on it
        assert clear > borderline

    def test_confidence_bounds(self):
        for match in range(0, 101, 5):
            for interview in (None, 0, 5, 10):
                c = decide(match, interview)[2]
                assert 20 <= c <= 95

    def test_deterministic(self):
        assert decide(66.6, 7.1) == decide(66.6, 7.1)


# ── recommend() end to end ────────────────────────────────────────────


class TestRecommend:
    @pytest.mark.asyncio
    async def test_full_result_with_llm_explanation(self, agent):
        result = await agent.recommend(CANDIDATE, MATCH, 8.0, jd_data=JD)

        assert result["recommendation"] == "HIRE"  # 0.6*80 + 0.4*80 = 80
        assert result["combined_score"] == 80.0
        assert result["match_score"] == 80.0
        assert result["interview_score"] == 8.0
        assert result["reasoning"] == "Strong skill coverage and a solid interview."
        assert result["strengths"] == ["Python depth", "Good communicator"]
        assert result["risks"] == ["No Kubernetes"]
        assert result["reasoning_source"] == "llm"
        assert 20 <= result["confidence"] <= 95

    @pytest.mark.asyncio
    async def test_llm_cannot_change_the_verdict(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                json.dumps(
                    {
                        "recommendation": "REJECT",
                        "reasoning": "Definitely reject.",
                        "strengths": [],
                        "risks": [],
                    }
                )
            )
        )
        result = await agent.recommend(CANDIDATE, MATCH, 8.0)
        assert result["recommendation"] == "HIRE"

    @pytest.mark.asyncio
    async def test_prompt_receives_the_decision_and_evidence(self, agent):
        chain = FakeChain(response=LLM_OK)
        agent.chain = chain

        await agent.recommend(CANDIDATE, MATCH, 8.0, jd_data=JD)

        sent = chain.calls[0]
        assert sent["verdict"] == "HIRE"
        assert sent["role"] == "Senior Backend Engineer"
        assert sent["matching_skills"] == "Python, PostgreSQL"
        assert sent["missing_skills"] == "Kubernetes"
        assert sent["interview_score"] == "8.0 / 10"
        assert sent["match_score"] == 80.0

    @pytest.mark.asyncio
    async def test_no_interview_is_stated_in_prompt(self, agent):
        chain = FakeChain(response=LLM_OK)
        agent.chain = chain
        await agent.recommend(CANDIDATE, MATCH, None)
        assert chain.calls[0]["interview_score"] == "not taken yet"

    @pytest.mark.asyncio
    async def test_bare_number_accepted_as_match_result(self, agent):
        result = await agent.recommend(CANDIDATE, 90, None)
        assert result["match_score"] == 90.0
        assert result["recommendation"] == "HIRE"

    @pytest.mark.asyncio
    async def test_out_of_range_inputs_clamped(self, agent):
        result = await agent.recommend(CANDIDATE, {"match_score": 250}, 99)
        assert result["match_score"] == 100.0
        assert result["interview_score"] == 10.0

    @pytest.mark.asyncio
    async def test_string_numbers_coerced(self, agent):
        result = await agent.recommend(CANDIDATE, {"match_score": "85%"}, "9/10")
        assert result["match_score"] == 85.0
        assert result["interview_score"] == 9.0


class TestPrivacy:
    @pytest.mark.asyncio
    async def test_contact_details_never_reach_the_llm(self, agent):
        chain = FakeChain(response=LLM_OK)
        agent.chain = chain

        await agent.recommend(CANDIDATE, MATCH, 8.0)

        candidate_sent = chain.calls[0]["candidate"]
        for secret in ("Aarav", "aarav@example.com", "99999", "Pune"):
            assert secret not in candidate_sent
        assert "PayCircle" in candidate_sent
        assert "FastAPI" in candidate_sent


# ── Explanation fallback ──────────────────────────────────────────────


class TestFallbackExplanation:
    @pytest.mark.asyncio
    async def test_llm_failure_keeps_verdict_and_uses_template(self, agent):
        agent.chain = FakeChain(exc=Exception("Groq rate limit"))
        result = await agent.recommend(CANDIDATE, MATCH, 8.0)

        assert result["recommendation"] == "HIRE"  # unaffected by the outage
        assert result["reasoning_source"] == "template"
        assert "80/100" in result["reasoning"]
        assert "2 of 3 required skills" in result["reasoning"]
        assert "8/10" in result["reasoning"]
        assert result["strengths"] == ["Has Python", "Has PostgreSQL"]
        assert result["risks"] == ["Missing Kubernetes"]

    @pytest.mark.asyncio
    async def test_unparseable_output_uses_template(self, agent):
        agent.chain = FakeChain(response=FakeMsg("They seem great!"))
        result = await agent.recommend(CANDIDATE, MATCH, 8.0)
        assert result["reasoning_source"] == "template"

    @pytest.mark.asyncio
    async def test_empty_reasoning_uses_template(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg('{"reasoning": "  ", "strengths": ["x"], "risks": []}')
        )
        result = await agent.recommend(CANDIDATE, MATCH, 8.0)
        assert result["reasoning_source"] == "template"

    @pytest.mark.asyncio
    async def test_non_object_json_uses_template(self, agent):
        agent.chain = FakeChain(response=FakeMsg("[1, 2]"))
        result = await agent.recommend(CANDIDATE, MATCH, 8.0)
        assert result["reasoning_source"] == "template"

    @pytest.mark.asyncio
    async def test_missing_lists_filled_from_template(self, agent):
        agent.chain = FakeChain(response=FakeMsg('{"reasoning": "Fine."}'))
        result = await agent.recommend(CANDIDATE, MATCH, 8.0)
        assert result["reasoning"] == "Fine."
        assert result["reasoning_source"] == "llm"
        assert result["strengths"] == ["Has Python", "Has PostgreSQL"]

    @pytest.mark.asyncio
    async def test_lists_capped(self, agent):
        agent.chain = FakeChain(
            response=FakeMsg(
                json.dumps(
                    {"reasoning": "ok", "strengths": list("abcdefgh"), "risks": []}
                )
            )
        )
        result = await agent.recommend(CANDIDATE, MATCH, 8.0)
        assert result["strengths"] == ["a", "b", "c", "d"]

    @pytest.mark.asyncio
    async def test_downgrade_is_explained_in_template(self, agent):
        agent.chain = FakeChain(exc=Exception("down"))
        result = await agent.recommend(CANDIDATE, {"match_score": 100}, 4.5)
        assert result["recommendation"] == "CONSIDER"
        assert "weak interview" in result["reasoning"]

    @pytest.mark.asyncio
    async def test_no_data_skips_the_llm(self, agent):
        chain = FakeChain(response=LLM_OK)
        agent.chain = chain
        result = await agent.recommend({}, None, None)

        assert result["recommendation"] == "CONSIDER"
        assert result["confidence"] == 20
        assert "insufficient_data" in result["flags"]
        assert result["reasoning_source"] == "template"
        assert chain.calls == []

    @pytest.mark.asyncio
    async def test_garbage_inputs_do_not_raise(self, agent):
        result = await agent.recommend(None, "n/a", object())
        assert result["recommendation"] == "CONSIDER"
