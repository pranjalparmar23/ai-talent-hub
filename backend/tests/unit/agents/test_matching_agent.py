"""Unit tests for MatchingAgent.

The match is computed in code (no LLM), so these are exact-value tests. The
embedding model is replaced by a tiny fake so scores are deterministic and no
model download is needed.
"""

import pytest

from app.agents.recruiter.matching_agent import (
    SEMANTIC_CEIL,
    SEMANTIC_FLOOR,
    MatchingAgent,
)


class FakeEmbedder:
    """Maps each text to a vector via a lookup; first call arg is the JD."""

    def __init__(self, vectors=None, exc=None):
        self.vectors = vectors  # list returned from embed_batch, in order
        self.exc = exc
        self.calls: list[list[str]] = []

    def embed_batch(self, texts):
        self.calls.append(list(texts))
        if self.exc:
            raise self.exc
        return self.vectors if self.vectors is not None else [[1.0, 0.0]] * len(texts)


@pytest.fixture
def agent():
    a = MatchingAgent()
    a.embedder = FakeEmbedder()  # identical vectors -> cosine 1.0 -> semantic 100
    return a


JD = {
    "title": "Senior Backend Engineer",
    "skills_required": ["Python", "PostgreSQL", "Kubernetes", "AWS"],
    "skills_preferred": ["Kafka", "Terraform"],
    "experience_years_min": 5,
    "responsibilities": ["Design distributed systems"],
}

RESUME = {
    "name": "Aarav",
    "summary": "Backend engineer building payment APIs.",
    "skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
    "experience_years": 3,
    "experience": [{"role": "Backend Engineer", "highlights": ["Built APIs"]}],
    "projects": [],
}


# ── Core scoring ──────────────────────────────────────────────────────


class TestScoring:
    @pytest.mark.asyncio
    async def test_breakdown_and_weighted_score(self, agent):
        r = await agent.compute_match(RESUME, JD)

        # required: 2/4 matched, preferred: 0/2 -> 100 * (0.8*0.5 + 0.2*0) = 40
        assert r["skill_match"] == 40.0
        # 3 years of 5 required
        assert r["experience_match"] == 60.0
        assert r["semantic_match"] == 100.0
        # 0.5*40 + 0.2*60 + 0.3*100 = 62
        assert r["match_score"] == 62.0
        assert r["matching_skills"] == ["Python", "PostgreSQL"]
        assert r["missing_skills"] == ["Kubernetes", "AWS"]
        assert r["matching_preferred"] == []
        assert r["semantic_available"] is True

    @pytest.mark.asyncio
    async def test_perfect_candidate_scores_100(self, agent):
        resume = {
            **RESUME,
            "skills": [
                "Python",
                "PostgreSQL",
                "Kubernetes",
                "AWS",
                "Kafka",
                "Terraform",
            ],
            "experience_years": 8,
        }
        r = await agent.compute_match(resume, JD)
        assert r["skill_match"] == 100.0
        assert r["experience_match"] == 100.0
        assert r["match_score"] == 100.0
        assert r["missing_skills"] == []

    @pytest.mark.asyncio
    async def test_experience_capped_at_100(self, agent):
        r = await agent.compute_match({**RESUME, "experience_years": 20}, JD)
        assert r["experience_match"] == 100.0

    @pytest.mark.asyncio
    async def test_preferred_skills_lift_the_score(self, agent):
        base = await agent.compute_match(RESUME, JD)
        boosted = await agent.compute_match(
            {**RESUME, "skills": RESUME["skills"] + ["Kafka", "Terraform"]}, JD
        )
        assert boosted["skill_match"] > base["skill_match"]
        assert boosted["matching_preferred"] == ["Kafka", "Terraform"]

    @pytest.mark.asyncio
    async def test_scores_always_within_0_100(self, agent):
        r = await agent.compute_match({"skills": [], "experience_years": 0}, JD)
        for key in ("match_score", "skill_match", "experience_match"):
            assert 0.0 <= r[key] <= 100.0
        # no resume text at all -> no semantic score (None, not 0)
        assert r["semantic_match"] is None


# ── Skill matching rules ──────────────────────────────────────────────


class TestSkillMatching:
    @pytest.mark.asyncio
    async def test_case_aliases_and_plurals_normalised(self, agent):
        jd = {"skills_required": ["Kubernetes", "PostgreSQL", "Node.js", "REST APIs"]}
        resume = {"skills": ["k8s", "postgres", "nodejs", "RESTful API"]}
        r = await agent.compute_match(resume, jd)
        # "RESTful API" is not an alias of "REST APIs" — stays missing, the rest match
        assert r["matching_skills"] == ["Kubernetes", "PostgreSQL", "Node.js"]
        assert r["missing_skills"] == ["REST APIs"]

    @pytest.mark.asyncio
    async def test_more_specific_candidate_skill_satisfies_jd_skill(self, agent):
        r = await agent.compute_match(
            {"skills": ["AWS Lambda"]}, {"skills_required": ["AWS"]}
        )
        assert r["matching_skills"] == ["AWS"]

    @pytest.mark.asyncio
    async def test_less_specific_candidate_skill_does_not_satisfy(self, agent):
        r = await agent.compute_match(
            {"skills": ["AWS"]}, {"skills_required": ["AWS Lambda"]}
        )
        assert r["missing_skills"] == ["AWS Lambda"]

    @pytest.mark.asyncio
    async def test_no_substring_false_positives(self, agent):
        jd = {"skills_required": ["Go", "C", "SQL", "Java"]}
        resume = {"skills": ["MongoDB", "C++", "PostgreSQL", "JavaScript"]}
        r = await agent.compute_match(resume, jd)
        assert r["matching_skills"] == []
        assert r["missing_skills"] == ["Go", "C", "SQL", "Java"]

    @pytest.mark.asyncio
    async def test_parenthetical_detail_ignored(self, agent):
        r = await agent.compute_match(
            {"skills": ["AWS (EC2, S3)"]}, {"skills_required": ["AWS"]}
        )
        assert r["matching_skills"] == ["AWS"]

    @pytest.mark.asyncio
    async def test_project_tech_stack_counts(self, agent):
        resume = {"skills": [], "projects": [{"tech_stack": ["Kafka", "Docker"]}]}
        r = await agent.compute_match(resume, {"skills_required": ["Kafka"]})
        assert r["matching_skills"] == ["Kafka"]


# ── Missing signals are dropped, not punished ─────────────────────────


class TestMissingSignals:
    @pytest.mark.asyncio
    async def test_no_experience_bar_is_excluded(self, agent):
        jd = {**JD, "experience_years_min": None}
        r = await agent.compute_match(RESUME, jd)
        assert r["experience_match"] is None
        # weights renormalised over skills (0.5) and semantic (0.3)
        assert r["match_score"] == round((0.5 * 40 + 0.3 * 100) / 0.8, 1)

    @pytest.mark.asyncio
    async def test_no_jd_skills_uses_remaining_signals(self, agent):
        jd = {"title": "Engineer", "experience_years_min": 2}
        r = await agent.compute_match(RESUME, jd)
        assert r["skill_match"] is None
        assert r["experience_match"] == 100.0

    @pytest.mark.asyncio
    async def test_embedding_failure_degrades_gracefully(self, agent):
        agent.embedder = FakeEmbedder(exc=RuntimeError("model missing"))
        r = await agent.compute_match(RESUME, JD)
        assert r["semantic_match"] is None
        assert r["semantic_available"] is False
        assert r["match_score"] == round((0.5 * 40 + 0.2 * 60) / 0.7, 1)

    @pytest.mark.asyncio
    async def test_nothing_to_score_returns_zero(self, agent):
        agent.embedder = FakeEmbedder(exc=RuntimeError("x"))
        r = await agent.compute_match({}, {})
        assert r["match_score"] == 0.0
        assert r["summary"] == "Not enough information to score this match."

    @pytest.mark.asyncio
    async def test_garbage_inputs_do_not_raise(self, agent):
        r = await agent.compute_match(
            {"skills": "python", "experience_years": "lots", "projects": [None, 3]},
            {"skills_required": None, "experience_years_min": True},
        )
        assert 0.0 <= r["match_score"] <= 100.0

    @pytest.mark.asyncio
    async def test_non_dict_inputs_do_not_raise(self, agent):
        r = await agent.compute_match(None, None)
        assert r["match_score"] == 0.0


# ── Semantic calibration ──────────────────────────────────────────────


class TestSemantic:
    @pytest.mark.asyncio
    async def test_cosine_stretched_between_floor_and_ceiling(self, agent):
        mid = (SEMANTIC_FLOOR + SEMANTIC_CEIL) / 2
        # unit JD vector; resume vector with cosine == mid
        agent.embedder = FakeEmbedder(vectors=[[1.0, 0.0], [mid, (1 - mid**2) ** 0.5]])
        r = await agent.compute_match(RESUME, JD)
        assert r["semantic_match"] == 50.0

    @pytest.mark.asyncio
    async def test_unrelated_text_clamped_to_zero(self, agent):
        agent.embedder = FakeEmbedder(vectors=[[1.0, 0.0], [0.0, 1.0]])
        r = await agent.compute_match(RESUME, JD)
        assert r["semantic_match"] == 0.0

    @pytest.mark.asyncio
    async def test_zero_vector_is_safe(self, agent):
        agent.embedder = FakeEmbedder(vectors=[[1.0, 0.0], [0.0, 0.0]])
        r = await agent.compute_match(RESUME, JD)
        assert r["semantic_match"] == 0.0


# ── Batch API ─────────────────────────────────────────────────────────


class TestBatch:
    @pytest.mark.asyncio
    async def test_one_embedding_call_for_whole_batch(self, agent):
        results = await agent.compute_matches([RESUME, RESUME, RESUME], JD)
        assert len(results) == 3
        assert len(agent.embedder.calls) == 1
        # JD first, then three resumes
        assert len(agent.embedder.calls[0]) == 4

    @pytest.mark.asyncio
    async def test_order_preserved(self, agent):
        strong = {**RESUME, "skills": ["Python", "PostgreSQL", "Kubernetes", "AWS"]}
        weak = {"skills": [], "experience_years": 0}
        results = await agent.compute_matches([weak, strong], JD)
        assert results[0]["match_score"] < results[1]["match_score"]

    @pytest.mark.asyncio
    async def test_resume_without_text_gets_no_semantic_score(self, agent):
        results = await agent.compute_matches([{}, RESUME], JD)
        assert results[0]["semantic_match"] is None
        assert results[1]["semantic_match"] == 100.0
        assert len(agent.embedder.calls[0]) == 2  # JD + the one non-empty resume

    @pytest.mark.asyncio
    async def test_empty_batch(self, agent):
        assert await agent.compute_matches([], JD) == []

    @pytest.mark.asyncio
    async def test_deterministic(self, agent):
        a = await agent.compute_match(RESUME, JD)
        b = await agent.compute_match(RESUME, JD)
        assert a == b


class TestSummary:
    @pytest.mark.asyncio
    async def test_summary_mentions_each_signal(self, agent):
        r = await agent.compute_match(RESUME, JD)
        assert "Matches 2 of 4 required skills" in r["summary"]
        assert "3 years experience vs 5 required" in r["summary"]
        assert "strong overall profile fit" in r["summary"]
