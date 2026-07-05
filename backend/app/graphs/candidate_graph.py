"""CandidateGraph — orchestrates the candidate analysis pipeline via LangGraph.

The graph has two entry patterns:
  1. Full pipeline (parse → ATS → gap → roadmap) — for /resume/upload followed by
     analysis, though currently we run analyses on-demand via the run_* methods.
  2. Individual sub-runs (ATS only, skill-gap only, roadmap only) — for the three
     dedicated endpoints. Each takes already-parsed resume_data and jd_data.

Both interview and evaluation live in Phase 4 — imported here for wiring but the
graph doesn't yet include them as nodes.
"""
import logging
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.candidate.ats_agent import ATSAgent
from app.agents.candidate.resume_parser import ResumeParserAgent
from app.agents.candidate.roadmap_agent import RoadmapAgent
from app.agents.candidate.skill_gap_agent import SkillGapAgent
from app.graphs.graph_state import TalentHubState

logger = logging.getLogger(__name__)


class CandidateGraph:
    """Orchestrates the candidate-side agent flow.

    Usage:
        graph = CandidateGraph()

        # Individual analyses (what the routes call today):
        ats = await graph.run_ats_check(resume_data, jd_data, user_id)
        gap = await graph.run_skill_gap(resume_data, jd_data, user_id)
        plan = await graph.run_learning_plan(resume_data, jd_data, user_id)
    """

    def __init__(self):
        self.parser = ResumeParserAgent()
        self.ats = ATSAgent()
        self.skill_gap = SkillGapAgent()
        self.roadmap = RoadmapAgent()
        self.graph = self._build()

    # ── Graph definition ─────────────────────────────────────────────────

    def _build(self):
        """Define the full pipeline as a LangGraph.

        Currently used mostly for its ainvoke() semantics + typed state; individual
        endpoints call the underlying agents directly via the run_* helpers below.
        The full graph exists for future 'run everything in one shot' use cases.
        """
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

    # ── Node implementations ─────────────────────────────────────────────

    async def _parse_resume(self, state: TalentHubState) -> dict[str, Any]:
        parsed = await self.parser.parse(state.get("raw_resume_text", ""))
        return {"resume_data": parsed}

    async def _ats_check(self, state: TalentHubState) -> dict[str, Any]:
        result = await self.ats.analyze(
            state.get("resume_data") or {},
            state.get("jd_data") or {},
        )
        return {
            "ats_score": result.get("ats_score", 0),
            "ats_feedback": result,
        }

    async def _skill_gap_analysis(self, state: TalentHubState) -> dict[str, Any]:
        result = await self.skill_gap.analyze(
            (state.get("resume_data") or {}).get("skills", []),
            (state.get("jd_data") or {}).get("skills_required", []),
        )
        return {
            "skill_gap": result.get("missing_skills", []),
            "skill_gap_details": result,
        }

    async def _generate_roadmap(self, state: TalentHubState) -> dict[str, Any]:
        target_role = (state.get("jd_data") or {}).get("title") or "target role"
        plan = await self.roadmap.generate(
            state.get("skill_gap", []),
            target_role,
        )
        return {"learning_plan": plan}

    # ── Public run methods (called by routes) ────────────────────────────

    async def run_ats_check(
        self,
        *,
        resume_data: dict,
        jd_data: dict,
        user_id: str,
    ) -> dict[str, Any]:
        """Run just the ATS scoring step against already-parsed inputs.

        Returns dict shaped for ATSScoreResponse (minus resume_id/jd_id which the
        route adds).
        """
        result = await self.ats.analyze(resume_data, jd_data)
        return {
            "ats_score": result.get("ats_score", 0),
            "matching_keywords": result.get("matching_keywords", []),
            "missing_keywords": result.get("missing_keywords", []),
            "formatting_issues": result.get("formatting_issues", []),
            "recommendations": result.get("recommendations", []),
            "summary": result.get("summary"),
        }

    async def run_skill_gap(
        self,
        *,
        resume_data: dict,
        jd_data: dict,
        user_id: str,
    ) -> dict[str, Any]:
        """Run just the skill-gap analysis. Returns dict shaped for SkillGapResponse."""
        resume_skills = resume_data.get("skills", []) or []
        jd_skills = jd_data.get("skills_required", []) or []

        result = await self.skill_gap.analyze(resume_skills, jd_skills)

        # Ensure the response shape is always complete even if the LLM dropped fields
        return {
            "missing_skills": result.get("missing_skills", []),
            "matching_skills": result.get("matching_skills", []),
            "gap_percentage": float(result.get("gap_percentage") or 0.0),
        }

    async def run_learning_plan(
        self,
        *,
        resume_data: dict,
        jd_data: dict,
        user_id: str,
    ) -> dict[str, Any]:
        """Run skill-gap → roadmap in one call.

        The roadmap needs to know what skills to teach (gap), so we always compute
        the gap first even if the caller only wants the plan.
        """
        resume_skills = resume_data.get("skills", []) or []
        jd_skills = jd_data.get("skills_required", []) or []
        target_role = jd_data.get("title") or "target role"

        gap = await self.skill_gap.analyze(resume_skills, jd_skills)
        missing = gap.get("missing_skills", []) or []

        plan = await self.roadmap.generate(missing, target_role)

        return {
            "target_role": target_role,
            "weeks": plan.get("weeks", []),
            "summary": plan.get("summary"),
        }