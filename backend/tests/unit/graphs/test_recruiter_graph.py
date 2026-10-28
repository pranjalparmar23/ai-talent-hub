"""Unit tests for RecruiterGraph.

The service (database) and the embedder are faked; the matching, ranking and
verdict logic are the real agents, so these tests check the pipeline wiring
end to end. The two LLM-backed agents get a FakeChain.
"""

import json
import uuid

import pytest
from fastapi import HTTPException

from app.database.schemas import (
    CandidateRankingResponse,
    HiringRecommendationResponse,
    RecruiterQuestionsResponse,
)
from app.graphs.recruiter_graph import RecruiterGraph
from tests.unit.agents._fakes import FakeChain, FakeMsg

RECRUITER_ID = str(uuid.uuid4())
JD_ID = str(uuid.uuid4())
JD = {
    "title": "Backend Engineer",
    "skills_required": ["Python", "PostgreSQL", "Docker"],
    "skills_preferred": ["Kafka"],
    "experience_years_min": 3,
}


class FakeEmbedder:
    def embed_batch(self, texts):
        return [[1.0, 0.0] for _ in texts]


class FakeService:
    def __init__(self, candidates=None):
        self.candidates = {c["candidate_id"]: c for c in candidates or []}
        self.jd_calls: list[tuple] = []
        self.pool_calls = 0

    async def get_jd_data(self, jd_id, user_id):
        self.jd_calls.append((jd_id, user_id))
        if user_id != RECRUITER_ID:
            raise HTTPException(status_code=404, detail="JD not found")
        return {"jd_id": jd_id, "title": "Backend Engineer", "parsed_data": JD}

    async def load_candidate_pool(self, limit=500):
        self.pool_calls += 1
        return list(self.candidates.values())

    async def load_candidate(self, candidate_id):
        if candidate_id not in self.candidates:
            raise HTTPException(status_code=404, detail="Candidate not found")
        return self.candidates[candidate_id]


def candidate(name, skills, years=5, interview=None, cid=None):
    return {
        "candidate_id": cid or str(uuid.uuid4()),
        "resume_id": str(uuid.uuid4()),
        "name": name,
        "interview_score": interview,
        "resume_data": {
            "name": name,
            "email": f"{name.lower()}@example.com",
            "skills": skills,
            "years_of_experience": years,
        },
    }


STRONG = candidate("Strong", ["Python", "PostgreSQL", "Docker", "Kafka"], 6, 8.0)
MIDDLE = candidate("Middle", ["Python", "PostgreSQL"], 4, None)
WEAK = candidate("Weak", ["Excel"], 1, 2.0)

QUESTIONS_JSON = json.dumps(
    {
        "questions": [
            {
                "question": "How do you tune a slow Postgres query?",
                "category": "technical",
                "difficulty": "medium",
                "rationale": "Core JD skill",
            }
        ]
    }
)
EXPLANATION_JSON = json.dumps(
    {"reasoning": "Strong fit.", "strengths": ["Python"], "risks": ["None"]}
)


@pytest.fixture
def make_graph(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    def build(candidates=None):
        graph = RecruiterGraph()
        graph.service = FakeService(candidates)
        graph.matcher.embedder = FakeEmbedder()
        graph.question_gen.chain = FakeChain(response=FakeMsg(QUESTIONS_JSON))
        graph.recommender.chain = FakeChain(response=FakeMsg(EXPLANATION_JSON))
        return graph

    return build


# ── Ranking pipeline ───────────────────────────────────────────────────────


class TestRunFullPipeline:
    async def test_ranks_best_candidate_first(self, make_graph):
        graph = make_graph([WEAK, STRONG, MIDDLE])
        result = await graph.run_full_pipeline(JD_ID, RECRUITER_ID)
        names = [c["name"] for c in result["ranked_candidates"]]
        assert names == ["Strong", "Middle", "Weak"]
        assert [c["rank"] for c in result["ranked_candidates"]] == [1, 2, 3]

    async def test_response_shape_validates_against_schema(self, make_graph):
        graph = make_graph([STRONG, MIDDLE])
        result = await graph.run_full_pipeline(JD_ID, RECRUITER_ID)
        parsed = CandidateRankingResponse(**result)
        assert parsed.jd_id == JD_ID
        assert parsed.jd_title == "Backend Engineer"
        assert parsed.total_candidates == 2

    async def test_rows_carry_scores_skills_and_interview_score(self, make_graph):
        graph = make_graph([STRONG])
        [row] = (await graph.run_full_pipeline(JD_ID, RECRUITER_ID))[
            "ranked_candidates"
        ]
        assert row["candidate_id"] == STRONG["candidate_id"]
        assert row["resume_id"] == STRONG["resume_id"]
        assert row["interview_score"] == 8.0
        assert set(row["matching_skills"]) >= {"Python", "PostgreSQL", "Docker"}
        assert row["missing_skills"] == []
        assert row["match_score"] > 70

    async def test_missing_skills_reported(self, make_graph):
        graph = make_graph([MIDDLE])
        [row] = (await graph.run_full_pipeline(JD_ID, RECRUITER_ID))[
            "ranked_candidates"
        ]
        assert "Docker" in row["missing_skills"]

    async def test_resume_contents_never_leak_into_the_response(self, make_graph):
        graph = make_graph([STRONG])
        result = await graph.run_full_pipeline(JD_ID, RECRUITER_ID)
        row = result["ranked_candidates"][0]
        assert "resume_data" not in row
        assert "strong@example.com" not in json.dumps(result)

    async def test_top_n_limits_rows_but_not_total(self, make_graph):
        graph = make_graph([STRONG, MIDDLE, WEAK])
        result = await graph.run_full_pipeline(JD_ID, RECRUITER_ID, top_n=2)
        assert [c["name"] for c in result["ranked_candidates"]] == ["Strong", "Middle"]
        assert result["total_candidates"] == 3

    async def test_empty_pool_returns_empty_ranking(self, make_graph):
        graph = make_graph([])
        result = await graph.run_full_pipeline(JD_ID, RECRUITER_ID)
        assert result == {
            "jd_id": JD_ID,
            "jd_title": "Backend Engineer",
            "total_candidates": 0,
            "ranked_candidates": [],
        }

    async def test_ownership_is_checked_with_the_callers_id(self, make_graph):
        graph = make_graph([STRONG])
        await graph.run_full_pipeline(JD_ID, RECRUITER_ID)
        assert graph.service.jd_calls == [(JD_ID, RECRUITER_ID)]

    async def test_foreign_jd_is_404_and_pool_never_loaded(self, make_graph):
        graph = make_graph([STRONG])
        with pytest.raises(HTTPException) as caught:
            await graph.run_full_pipeline(JD_ID, str(uuid.uuid4()))
        assert caught.value.status_code == 404
        assert graph.service.pool_calls == 0

    async def test_semantic_outage_still_ranks(self, make_graph):
        graph = make_graph([STRONG, WEAK])

        class Broken:
            def embed_batch(self, texts):
                raise RuntimeError("model unavailable")

        graph.matcher.embedder = Broken()
        result = await graph.run_full_pipeline(JD_ID, RECRUITER_ID)
        assert [c["name"] for c in result["ranked_candidates"]] == ["Strong", "Weak"]
        assert result["ranked_candidates"][0]["semantic_available"] is False


# ── Interview questions ────────────────────────────────────────────────────


class TestGenerateQuestions:
    async def test_returns_questions_matching_the_schema(self, make_graph):
        graph = make_graph([MIDDLE])
        result = await graph.generate_questions(
            JD_ID, MIDDLE["candidate_id"], RECRUITER_ID
        )
        parsed = RecruiterQuestionsResponse(**result)
        assert parsed.candidate_id == MIDDLE["candidate_id"]
        assert parsed.questions[0].category == "technical"
        assert parsed.parse_error is False

    async def test_skill_gaps_reach_the_prompt(self, make_graph):
        graph = make_graph([MIDDLE])
        await graph.generate_questions(JD_ID, MIDDLE["candidate_id"], RECRUITER_ID)
        prompt_vars = graph.question_gen.chain.calls[0]
        assert "Docker" in json.dumps(prompt_vars)

    async def test_personal_details_are_not_sent_to_the_llm(self, make_graph):
        graph = make_graph([MIDDLE])
        await graph.generate_questions(JD_ID, MIDDLE["candidate_id"], RECRUITER_ID)
        sent = json.dumps(graph.question_gen.chain.calls[0])
        assert "middle@example.com" not in sent

    async def test_llm_failure_gives_empty_list_and_flag(self, make_graph):
        graph = make_graph([MIDDLE])
        graph.question_gen.chain = FakeChain(exc=RuntimeError("rate limited"))
        result = await graph.generate_questions(
            JD_ID, MIDDLE["candidate_id"], RECRUITER_ID
        )
        assert result["questions"] == []
        assert result["parse_error"] is True

    async def test_unknown_candidate_is_404(self, make_graph):
        graph = make_graph([MIDDLE])
        with pytest.raises(HTTPException) as caught:
            await graph.generate_questions(JD_ID, str(uuid.uuid4()), RECRUITER_ID)
        assert caught.value.status_code == 404

    async def test_foreign_jd_is_404(self, make_graph):
        graph = make_graph([MIDDLE])
        with pytest.raises(HTTPException) as caught:
            await graph.generate_questions(
                JD_ID, MIDDLE["candidate_id"], str(uuid.uuid4())
            )
        assert caught.value.status_code == 404
        assert graph.question_gen.chain.calls == []


# ── Hiring recommendation ──────────────────────────────────────────────────


class TestGetRecommendation:
    async def test_strong_candidate_is_hire(self, make_graph):
        graph = make_graph([STRONG])
        result = await graph.get_recommendation(
            JD_ID, STRONG["candidate_id"], RECRUITER_ID
        )
        parsed = HiringRecommendationResponse(**result)
        assert parsed.recommendation == "HIRE"
        assert parsed.interview_score == 8.0
        assert parsed.reasoning_source == "llm"
        assert parsed.jd_id == JD_ID

    async def test_weak_candidate_is_rejected(self, make_graph):
        graph = make_graph([WEAK])
        result = await graph.get_recommendation(
            JD_ID, WEAK["candidate_id"], RECRUITER_ID
        )
        assert result["recommendation"] == "REJECT"

    async def test_no_interview_is_flagged_not_penalised_to_zero(self, make_graph):
        graph = make_graph([MIDDLE])
        result = await graph.get_recommendation(
            JD_ID, MIDDLE["candidate_id"], RECRUITER_ID
        )
        assert "no_interview" in result["flags"]
        assert result["interview_score"] is None
        assert result["combined_score"] == result["match_score"]

    async def test_llm_failure_falls_back_to_template(self, make_graph):
        graph = make_graph([STRONG])
        graph.recommender.chain = FakeChain(exc=RuntimeError("down"))
        result = await graph.get_recommendation(
            JD_ID, STRONG["candidate_id"], RECRUITER_ID
        )
        assert result["recommendation"] == "HIRE"
        assert result["reasoning_source"] == "template"

    async def test_unknown_candidate_is_404(self, make_graph):
        graph = make_graph([STRONG])
        with pytest.raises(HTTPException) as caught:
            await graph.get_recommendation(JD_ID, str(uuid.uuid4()), RECRUITER_ID)
        assert caught.value.status_code == 404

    async def test_foreign_jd_is_404(self, make_graph):
        graph = make_graph([STRONG])
        with pytest.raises(HTTPException) as caught:
            await graph.get_recommendation(
                JD_ID, STRONG["candidate_id"], str(uuid.uuid4())
            )
        assert caught.value.status_code == 404
