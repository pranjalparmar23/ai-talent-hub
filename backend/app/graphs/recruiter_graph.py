from langgraph.graph import StateGraph, END
from app.graphs.graph_state import TalentHubState
from app.agents.recruiter.jd_analyzer import JDAnalyzerAgent
from app.agents.recruiter.matching_agent import MatchingAgent
from app.agents.recruiter.ranking_agent import RankingAgent
from app.agents.recruiter.question_agent import QuestionGenerationAgent
from app.agents.recruiter.recommendation_agent import HiringRecommendationAgent


class RecruiterGraph:
    def __init__(self):
        self.jd_analyzer = JDAnalyzerAgent()
        self.matcher = MatchingAgent()
        self.ranker = RankingAgent()
        self.question_gen = QuestionGenerationAgent()
        self.recommender = HiringRecommendationAgent()
        self.graph = self._build()

    def _build(self) -> StateGraph:
        g = StateGraph(TalentHubState)
        g.add_node("analyze_jd", self._analyze_jd)
        g.add_node("match_candidates", self._match_candidates)
        g.add_node("rank_candidates", self._rank_candidates)
        g.set_entry_point("analyze_jd")
        g.add_edge("analyze_jd", "match_candidates")
        g.add_edge("match_candidates", "rank_candidates")
        g.add_edge("rank_candidates", END)
        return g.compile()

    async def _analyze_jd(self, state: TalentHubState) -> TalentHubState:
        jd_data = await self.jd_analyzer.analyze(state.get("raw_jd_text", ""))
        return {**state, "jd_data": jd_data}

    async def _match_candidates(self, state: TalentHubState) -> TalentHubState:
        # In production, load candidates from DB
        scores = []
        return {**state, "candidate_scores": scores}

    async def _rank_candidates(self, state: TalentHubState) -> TalentHubState:
        ranked = self.ranker.rank(state.get("candidate_scores", []))
        return {**state, "ranked_candidates": ranked}

    async def run_full_pipeline(self, jd_id: str, user_id: str) -> dict:
        state: TalentHubState = {"user_id": user_id, "raw_jd_text": ""}
        result = await self.graph.ainvoke(state)
        return {"ranked_candidates": result.get("ranked_candidates")}

    async def generate_questions(self, jd_id: str, candidate_id: str) -> dict:
        return {"questions": []}

    async def get_recommendation(self, jd_id: str, candidate_id: str) -> dict:
        return {"recommendation": ""}
