"""ATSAgent — scores a resume against a job description using Groq."""
import json
import logging
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from app.llm.models import get_llm
from app.llm.prompts.ats_prompts import ATS_CHECK_PROMPT
from app.utils.json_parser import parse_llm_json

logger = logging.getLogger(__name__)


# Fallback returned when the LLM output is unparseable — keeps the pipeline moving.
_ATS_FALLBACK: dict[str, Any] = {
    "ats_score": 0,
    "matching_keywords": [],
    "missing_keywords": [],
    "formatting_issues": [],
    "recommendations": ["Analysis unavailable — please try again"],
    "summary": "Analysis failed. Please retry.",
    "_parse_error": True,
}


class ATSAgent:
    """Compares a parsed resume against a parsed JD and returns an ATS score + feedback.

    Usage:
        agent = ATSAgent()
        result = await agent.analyze(resume_data, jd_data)
        # result["ats_score"] in [0, 100]
    """

    def __init__(self):
        self.llm = get_llm(temperature=0)
        self.prompt = ChatPromptTemplate.from_template(ATS_CHECK_PROMPT)
        self.chain = self.prompt | self.llm

    async def analyze(self, resume_data: dict, jd_data: dict) -> dict:
        """Score the resume against the JD.

        Args:
            resume_data: Output of ResumeParserAgent (or dict with skills/experience_years).
            jd_data: Output of JDAnalyzerAgent (or dict with skills_required/experience_years_min).

        Returns:
            Dict matching the ATS schema in ats_prompts.py. On failure returns the
            fallback dict with `_parse_error: True` — never raises.
        """
        if not resume_data or not jd_data:
            logger.warning("ATSAgent called with empty resume_data or jd_data")
            return {**_ATS_FALLBACK, "_error_reason": "empty_input"}

        # Trim resume_data to what the LLM actually uses. Full parsed resumes can
        # be 2000+ tokens; we don't need to send raw work highlights for scoring.
        trimmed_resume = self._trim_resume_for_scoring(resume_data)
        trimmed_jd = self._trim_jd_for_scoring(jd_data)

        try:
            response = await self.chain.ainvoke({
                "resume": json.dumps(trimmed_resume, ensure_ascii=False),
                "job_description": json.dumps(trimmed_jd, ensure_ascii=False),
            })
        except Exception as e:
            logger.exception("Groq call failed in ATSAgent")
            return {**_ATS_FALLBACK, "_error_reason": str(e)[:200]}

        parsed = parse_llm_json(response.content, fallback=_ATS_FALLBACK)

        # Enforce score bounds even if the LLM ignored the rubric
        raw_score = parsed.get("ats_score", 0)
        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            score = 0
        parsed["ats_score"] = max(0, min(100, score))

        # Ensure list fields are actually lists (LLMs sometimes return strings)
        for key in ("matching_keywords", "missing_keywords", "formatting_issues", "recommendations"):
            if not isinstance(parsed.get(key), list):
                parsed[key] = []

        return parsed

    # ── Internal helpers ─────────────────────────────────────────────────

    def _trim_resume_for_scoring(self, resume: dict) -> dict:
        """Send only the fields the scoring rubric actually consumes."""
        return {
            "skills": resume.get("skills", []),
            "experience_years": resume.get("experience_years", 0),
            "experience": [
                {"role": e.get("role"), "company": e.get("company"), "highlights": e.get("highlights", [])[:3]}
                for e in resume.get("experience", [])[:5]  # cap to 5 most recent
            ],
            "education": resume.get("education", []),
            "projects": [
                {"name": p.get("name"), "tech_stack": p.get("tech_stack", [])}
                for p in resume.get("projects", [])[:5]
            ],
        }

    def _trim_jd_for_scoring(self, jd: dict) -> dict:
        """Send only fields the scoring rubric consumes."""
        return {
            "title": jd.get("title"),
            "role_level": jd.get("role_level"),
            "experience_years_min": jd.get("experience_years_min"),
            "skills_required": jd.get("skills_required", []),
            "skills_preferred": jd.get("skills_preferred", []),
        }