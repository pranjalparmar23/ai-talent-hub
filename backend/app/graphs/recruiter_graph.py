"""RecruiterGraph — orchestrates the recruiter-side pipeline.

Ranking is a LangGraph pipeline:

    load_jd → load_candidates → match_candidates → rank_candidates → END
                    └─ (no candidates) ─────────────────────────────→ END

Questions and recommendations are about one candidate at a time, so they are
plain methods that reuse the same loaders and the same matching step.

Everything that scores is deterministic code (MatchingAgent, RankingAgent and
the verdict inside HiringRecommendationAgent). LLMs only write the interview
questions and the explanation text.

Usage:
    graph = RecruiterGraph()
    ranking = await graph.run_full_pipeline(jd_id, recruiter_id, top_n=20)
    questions = await graph.generate_questions(jd_id, candidate_id, recruiter_id)
    verdict = await graph.get_recommendation(jd_id, candidate_id, recruiter_id)
"""

import asyncio
import logging
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.recruiter.matching_agent import MatchingAgent
from app.agents.recruiter.question_agent import QuestionGenerationAgent
from app.agents.recruiter.ranking_agent import RankingAgent
from app.agents.recruiter.recommendation_agent import HiringRecommendationAgent
from app.graphs.graph_state import TalentHubState
from app.services.recruiter_service import RecruiterService

logger = logging.getLogger(__name__)


# Fields of a match result that are copied onto a ranking row. Resume contents
# are deliberately not copied: the ranking response must not carry resume text.
_MATCH_FIELDS = (
    "match_score",
    "skill_match",
    "experience_match",
    "semantic_match",
    "semantic_available",
    "matching_skills",
    "missing_skills",
    "matching_preferred",
    "summary",
)


class RecruiterGraph:
    # NOTE: keep __init__ argument-free; tests replace the attributes below.
    def __init__(self):
        self.service = RecruiterService()
        self.matcher = MatchingAgent()
        self.ranker = RankingAgent()
        self.question_gen = QuestionGenerationAgent()
        self.recommender = HiringRecommendationAgent()
        self.graph = self._build()

    # ── Graph ──────────────────────────────────────────────────────────────

    def _build(self):
        g = StateGraph(TalentHubState)
        g.add_node("load_jd", self._load_jd)
        g.add_node("load_candidates", self._load_candidates)
        g.add_node("match_candidates", self._match_candidates)
        g.add_node("rank_candidates", self._rank_candidates)
        g.set_entry_point("load_jd")
        g.add_edge("load_jd", "load_candidates")
        g.add_conditional_edges(
            "load_candidates",
            lambda state: "match" if state.get("candidates") else "done",
            {"match": "match_candidates", "done": END},
        )
        g.add_edge("match_candidates", "rank_candidates")
        g.add_edge("rank_candidates", END)
        return g.compile()

    async def _load_jd(self, state: TalentHubState) -> dict:
        jd = await self.service.get_jd_data(state["jd_id"], state["user_id"])
        return {"jd_data": jd["parsed_data"], "jd_title": jd["title"]}

    async def _load_candidates(self, state: TalentHubState) -> dict:
        return {"candidates": await self.service.load_candidate_pool()}

    async def _match_candidates(self, state: TalentHubState) -> dict:
        candidates = state["candidates"]
        matches = await self.matcher.compute_matches(
            [c["resume_data"] for c in candidates], state["jd_data"]
        )
        return {
            "candidate_scores": [
                self._ranking_row(candidate, match)
                for candidate, match in zip(candidates, matches)
            ]
        }

    async def _rank_candidates(self, state: TalentHubState) -> dict:
        ranked = self.ranker.rank(
            state.get("candidate_scores", []), top_n=state.get("top_n")
        )
        return {"ranked_candidates": ranked}

    @staticmethod
    def _ranking_row(candidate: dict, match: dict) -> dict[str, Any]:
        row: dict[str, Any] = {
            "candidate_id": candidate["candidate_id"],
            "resume_id": candidate.get("resume_id"),
            "name": candidate.get("name"),
            "interview_score": candidate.get("interview_score"),
        }
        row.update({key: match.get(key) for key in _MATCH_FIELDS})
        return row

    # ── Public API ─────────────────────────────────────────────────────────

    async def run_full_pipeline(
        self, jd_id: str, user_id: str, top_n: int | None = None
    ) -> dict:
        """Rank every candidate against one JD (must belong to `user_id`)."""
        result = await self.graph.ainvoke(
            {"user_id": user_id, "jd_id": jd_id, "top_n": top_n}
        )
        return {
            "jd_id": jd_id,
            "jd_title": result.get("jd_title"),
            "total_candidates": len(result.get("candidates") or []),
            "ranked_candidates": result.get("ranked_candidates") or [],
        }

    async def generate_questions(
        self, jd_id: str, candidate_id: str, user_id: str
    ) -> dict:
        """Interview questions tailored to this JD and this candidate's gaps."""
        jd, candidate, match = await self._pair(jd_id, candidate_id, user_id)
        result = await self.question_gen.generate(
            jd["parsed_data"],
            candidate["resume_data"],
            match.get("missing_skills") or [],
        )
        return {
            "jd_id": jd["jd_id"],
            "candidate_id": candidate["candidate_id"],
            "questions": result.get("questions") or [],
            "parse_error": bool(result.get("_parse_error")),
        }

    async def get_recommendation(
        self, jd_id: str, candidate_id: str, user_id: str
    ) -> dict:
        """HIRE / CONSIDER / REJECT with reasoning, for one candidate."""
        jd, candidate, match = await self._pair(jd_id, candidate_id, user_id)
        recommendation = await self.recommender.recommend(
            candidate["resume_data"],
            match,
            candidate.get("interview_score"),
            jd_data=jd["parsed_data"],
        )
        return {
            "jd_id": jd["jd_id"],
            "candidate_id": candidate["candidate_id"],
            **recommendation,
        }

    async def _pair(self, jd_id: str, candidate_id: str, user_id: str):
        """Load the JD (ownership-checked) and candidate, then score the pair."""
        jd, candidate = await asyncio.gather(
            self.service.get_jd_data(jd_id, user_id),
            self.service.load_candidate(candidate_id),
        )
        match = await self.matcher.compute_match(
            candidate["resume_data"], jd["parsed_data"]
        )
        return jd, candidate, match
