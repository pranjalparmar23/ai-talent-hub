from langgraph.graph import StateGraph, END
from app.graphs.graph_state import TalentHubState
from app.agents.candidate.resume_parser import ResumeParserAgent
from app.agents.candidate.ats_agent import ATSAgent
from app.agents.candidate.skill_gap_agent import SkillGapAgent
from app.agents.candidate.roadmap_agent import RoadmapAgent
from app.agents.candidate.interview_agent import InterviewAgent
from app.agents.shared.evaluation_agent import EvaluationAgent


class CandidateGraph:
    def __init__(self):
        self.parser = ResumeParserAgent()
        self.ats = ATSAgent()
        self.skill_gap = SkillGapAgent()
        self.roadmap = RoadmapAgent()
        self.interviewer = InterviewAgent()
        self.evaluator = EvaluationAgent()
        self.graph = self._build()

    def _build(self) -> StateGraph:
        g = StateGraph(TalentHubState)
        g.add_node("parse_resume", self._parse_resume)
        g.add_node("ats_check", self._ats_check)
        g.add_node("skill_gap_analysis", self._skill_gap_analysis)
        g.add_node("generate_roadmap", self._generate_roadmap)
        g.set_entry_point("parse_resume")
        g.add_edge("parse_resume", "ats_check")
        g.add_edge("ats_check", "skill_gap_analysis")
        g.add_edge("skill_gap_analysis", "generate_roadmap")
        g.add_edge("generate_roadmap", END)
        return g.compile()

    async def _parse_resume(self, state: TalentHubState) -> TalentHubState:
        parsed = await self.parser.parse(state.get("raw_resume_text", ""))
        return {**state, "resume_data": parsed}

    async def _ats_check(self, state: TalentHubState) -> TalentHubState:
        result = await self.ats.analyze(state["resume_data"], state.get("jd_data", {}))
        return {**state, "ats_score": result.get("ats_score", 0), "ats_feedback": result}

    async def _skill_gap_analysis(self, state: TalentHubState) -> TalentHubState:
        gaps = await self.skill_gap.analyze(
            state["resume_data"].get("skills", []),
            state.get("jd_data", {}).get("skills", []),
        )
        return {**state, "skill_gap": gaps.get("missing_skills", [])}

    async def _generate_roadmap(self, state: TalentHubState) -> TalentHubState:
        plan = await self.roadmap.generate(state["skill_gap"], state.get("jd_data", {}).get("role", ""))
        return {**state, "learning_plan": plan}

    async def run_ats_check(self, resume_id: str, jd_id: str, user_id: str) -> dict:
        # Load from DB in real implementation
        state: TalentHubState = {"user_id": user_id, "raw_resume_text": "", "jd_data": {}}
        result = await self.graph.ainvoke(state)
        return {"ats_score": result.get("ats_score"), "feedback": result.get("ats_feedback")}

    async def run_skill_gap(self, resume_id: str, jd_id: str, user_id: str) -> dict:
        state: TalentHubState = {"user_id": user_id, "raw_resume_text": "", "jd_data": {}}
        result = await self.graph.ainvoke(state)
        return {"skill_gap": result.get("skill_gap")}

    async def run_learning_plan(self, resume_id: str, jd_id: str, user_id: str) -> dict:
        state: TalentHubState = {"user_id": user_id, "raw_resume_text": "", "jd_data": {}}
        result = await self.graph.ainvoke(state)
        return {"learning_plan": result.get("learning_plan")}
