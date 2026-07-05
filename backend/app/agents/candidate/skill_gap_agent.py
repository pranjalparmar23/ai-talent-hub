"""SkillGapAgent — compares candidate skills against JD skills via Groq."""
import json
import logging
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from app.llm.models import get_llm
from app.llm.prompts.skill_prompts import SKILL_GAP_PROMPT
from app.utils.json_parser import parse_llm_json

logger = logging.getLogger(__name__)


# Fallback returned when the LLM output is unparseable
_GAP_FALLBACK: dict[str, Any] = {
    "matching_skills": [],
    "missing_skills": [],
    "gap_percentage": 0.0,
    "priority_missing": [],
    "notes": "Analysis unavailable — please try again",
    "_parse_error": True,
}


class SkillGapAgent:
    """Identifies matching/missing skills between candidate and JD.

    Usage:
        agent = SkillGapAgent()
        result = await agent.analyze(candidate_skills, jd_skills)
        # result["missing_skills"], result["matching_skills"], etc.
    """

    def __init__(self):
        self.llm = get_llm(temperature=0)
        self.prompt = ChatPromptTemplate.from_template(SKILL_GAP_PROMPT)
        self.chain = self.prompt | self.llm

    async def analyze(
        self,
        candidate_skills: list[str],
        jd_skills: list[str],
    ) -> dict[str, Any]:
        """Compare skill lists and return matching/missing analysis.

        Args:
            candidate_skills: e.g. ["Python", "FastAPI", "K8s"]
            jd_skills: e.g. ["Python", "Kubernetes", "AWS"]

        Returns:
            Dict matching the skill-gap schema. On failure returns fallback dict
            with `_parse_error: True`. Never raises.
        """
        # Normalize inputs — handle None, non-list, or dict-shaped inputs
        candidate_skills = self._as_string_list(candidate_skills)
        jd_skills = self._as_string_list(jd_skills)

        # Edge case: JD has no required skills. Nothing to gap against.
        if not jd_skills:
            logger.info("SkillGapAgent: JD has no required skills, returning empty gap")
            return {
                "matching_skills": [],
                "missing_skills": [],
                "gap_percentage": 0.0,
                "priority_missing": [],
                "notes": "No required skills specified in the JD.",
            }

        # Edge case: candidate has no skills. Everything is missing.
        if not candidate_skills:
            logger.info("SkillGapAgent: candidate has no skills, everything missing")
            return {
                "matching_skills": [],
                "missing_skills": list(jd_skills),
                "gap_percentage": 100.0,
                "priority_missing": list(jd_skills)[:5],
                "notes": "No skills listed on the resume — all JD requirements are unmet.",
            }

        try:
            response = await self.chain.ainvoke({
                "candidate_skills": json.dumps(candidate_skills, ensure_ascii=False),
                "jd_skills": json.dumps(jd_skills, ensure_ascii=False),
            })
        except Exception as e:
            logger.exception("Groq call failed in SkillGapAgent")
            return {**_GAP_FALLBACK, "_error_reason": str(e)[:200]}

        parsed = parse_llm_json(response.content, fallback=_GAP_FALLBACK)
        return self._normalize(parsed, jd_skills_count=len(jd_skills))

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _as_string_list(value) -> list[str]:
        """Coerce whatever was passed in to a clean list of non-empty strings."""
        if not isinstance(value, list):
            return []
        return [s.strip() for s in value if isinstance(s, str) and s.strip()]

    def _normalize(self, parsed: dict, *, jd_skills_count: int) -> dict[str, Any]:
        """Enforce shape invariants and recompute gap_percentage from actuals.

        We don't trust the LLM's own gap_percentage number — we recompute it from
        missing/matching counts so it always adds up.
        """
        matching = self._as_string_list(parsed.get("matching_skills"))
        missing = self._as_string_list(parsed.get("missing_skills"))
        priority = self._as_string_list(parsed.get("priority_missing"))

        # Ground truth for gap_percentage: how many required skills are missing
        if jd_skills_count > 0:
            gap_pct = round((len(missing) / jd_skills_count) * 100, 1)
        else:
            gap_pct = 0.0
        gap_pct = max(0.0, min(100.0, gap_pct))

        notes = parsed.get("notes")
        if not isinstance(notes, str):
            notes = None

        result = {
            "matching_skills": matching,
            "missing_skills": missing,
            "gap_percentage": gap_pct,
            "priority_missing": priority[:5],  # cap at 5 for UI
            "notes": notes,
        }

        # Preserve error markers for downstream visibility
        if parsed.get("_parse_error"):
            result["_parse_error"] = True

        return result