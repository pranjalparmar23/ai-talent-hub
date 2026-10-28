"""QuestionGenerationAgent — interview questions for a recruiter, tailored to
one candidate.

Given the JD, the candidate's parsed resume and the skills the candidate is
missing, asks Groq for a structured question set (technical / skill-gap /
experience / behavioral) with a rationale for each, so the recruiter knows what
a good answer looks like.

Design notes:
  - Never raises. On any failure returns {"questions": [], "_parse_error": True}.
  - The candidate's name and contact details are stripped before the LLM sees the
    resume: they are irrelevant to the questions and removing them keeps names
    from influencing the output.
  - Output is normalised in code (categories/difficulty forced into the allowed
    sets, duplicates removed, count capped) rather than trusted from the model.
"""

import json
import logging
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from app.llm.models import get_llm
from app.llm.prompts.question_prompts import QUESTION_GEN_PROMPT
from app.utils.json_parser import parse_llm_json

logger = logging.getLogger(__name__)


CATEGORIES = ("technical", "skill-gap", "experience", "behavioral")
DIFFICULTIES = ("easy", "medium", "hard")
DEFAULT_COUNT = 10
MAX_COUNT = 15
MAX_GAPS = 8


class QuestionGenerationAgent:
    """Usage:
    agent = QuestionGenerationAgent()
    result = await agent.generate(jd_data, resume_data, skill_gaps)
    # result["questions"] -> [{question, category, difficulty, rationale}, ...]
    """

    def __init__(self):
        self.llm = get_llm(temperature=0.4, max_tokens=2500)
        self.prompt = ChatPromptTemplate.from_template(QUESTION_GEN_PROMPT)
        self.chain = self.prompt | self.llm

    async def generate(
        self,
        jd_data: dict,
        resume_data: dict,
        skill_gaps: list[str] | None = None,
        *,
        count: int = DEFAULT_COUNT,
    ) -> dict[str, Any]:
        jd = jd_data if isinstance(jd_data, dict) else {}
        resume = resume_data if isinstance(resume_data, dict) else {}
        if not jd and not resume:
            return self._fallback("empty_input")

        count = max(1, min(int(count), MAX_COUNT))
        gaps = self._clean_gaps(skill_gaps)

        try:
            response = await self.chain.ainvoke(
                {
                    "count": count,
                    "jd": json.dumps(self._jd_view(jd)),
                    "resume": json.dumps(self._resume_view(resume)),
                    "skill_gaps": ", ".join(gaps) if gaps else "None identified",
                }
            )
            raw = getattr(response, "content", response)
        except Exception as e:
            logger.error(f"QuestionGenerationAgent LLM call failed: {e}")
            return self._fallback(str(e)[:200])

        parsed = parse_llm_json(str(raw), fallback={})
        questions = self._normalize(parsed, count)
        if not questions:
            return self._fallback("unparseable_output")
        return {"questions": questions}

    # ── Normalisation ──────────────────────────────────────────────────────

    @classmethod
    def _normalize(cls, parsed: Any, count: int) -> list[dict[str, Any]]:
        """Accept {"questions": [...]} or a bare list; return clean question dicts."""
        if isinstance(parsed, dict):
            items = parsed.get("questions")
        else:
            items = parsed
        if not isinstance(items, list):
            return []

        seen: set[str] = set()
        questions: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, str):
                item = {"question": item}
            if not isinstance(item, dict):
                continue

            text = str(item.get("question") or "").strip()
            key = text.lower()
            if not text or key in seen:
                continue
            seen.add(key)

            rationale = str(item.get("rationale") or "").strip()
            questions.append(
                {
                    "question": text,
                    "category": cls._pick(
                        item.get("category"), CATEGORIES, "technical"
                    ),
                    "difficulty": cls._pick(
                        item.get("difficulty"), DIFFICULTIES, "medium"
                    ),
                    "rationale": rationale or None,
                }
            )
            if len(questions) >= count:
                break
        return questions

    @staticmethod
    def _pick(value: Any, allowed: tuple[str, ...], default: str) -> str:
        slug = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
        return slug if slug in allowed else default

    # ── What the LLM is allowed to see ─────────────────────────────────────

    @staticmethod
    def _resume_view(resume: dict) -> dict:
        """Compact, anonymised view: no name, email, phone, links or location."""
        roles = []
        for job in (resume.get("experience") or [])[:4]:
            if isinstance(job, dict):
                roles.append(
                    {
                        "role": job.get("role"),
                        "company": job.get("company"),
                        "highlights": (job.get("highlights") or [])[:3],
                    }
                )
        projects = []
        for project in (resume.get("projects") or [])[:3]:
            if isinstance(project, dict):
                projects.append(
                    {
                        "name": project.get("name"),
                        "description": project.get("description"),
                        "tech_stack": project.get("tech_stack") or [],
                    }
                )
        return {
            "summary": resume.get("summary"),
            "skills": resume.get("skills") or [],
            "experience_years": resume.get("experience_years"),
            "roles": roles,
            "projects": projects,
        }

    @staticmethod
    def _jd_view(jd: dict) -> dict:
        return {
            "title": jd.get("title"),
            "role_level": jd.get("role_level"),
            "experience_years_min": jd.get("experience_years_min"),
            "skills_required": jd.get("skills_required") or [],
            "skills_preferred": jd.get("skills_preferred") or [],
            "responsibilities": (jd.get("responsibilities") or [])[:6],
        }

    @staticmethod
    def _clean_gaps(skill_gaps: Any) -> list[str]:
        if not isinstance(skill_gaps, list):
            return []
        seen: set[str] = set()
        gaps: list[str] = []
        for gap in skill_gaps:
            if not isinstance(gap, str) or not gap.strip():
                continue
            if gap.strip().lower() in seen:
                continue
            seen.add(gap.strip().lower())
            gaps.append(gap.strip())
        return gaps[:MAX_GAPS]

    @staticmethod
    def _fallback(reason: str) -> dict[str, Any]:
        return {"questions": [], "_parse_error": True, "_error_reason": reason}
