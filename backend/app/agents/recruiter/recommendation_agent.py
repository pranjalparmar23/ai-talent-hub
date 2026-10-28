"""HiringRecommendationAgent — HIRE / CONSIDER / REJECT with a written rationale.

The verdict is computed in code, NOT by the LLM:

    combined = 0.6 * resume_match + 0.4 * (interview_score * 10)     # both 0-100
             = resume_match                                          # no interview yet

    combined >= 75  -> HIRE
    combined >= 50  -> CONSIDER
    otherwise       -> REJECT
    (a HIRE with an interview score under 5/10 is downgraded to CONSIDER)

Why: a hiring decision has to be consistent, explainable and not swayed by how a
resume is worded. The same inputs always give the same verdict, the thresholds
live in one place, and the recommendation still works if Groq is down. The LLM
only writes the explanation (reasoning / strengths / risks); if it fails, a
template built from the same numbers is used instead.

`confidence` is a heuristic: how clear-cut the call is (distance from the nearest
threshold) reduced when evidence is thin (no interview score yet).
"""

import json
import logging
import re
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from app.llm.models import get_llm
from app.llm.prompts.recommendation_prompts import RECOMMENDATION_PROMPT
from app.utils.json_parser import parse_llm_json

logger = logging.getLogger(__name__)


MATCH_WEIGHT = 0.6
INTERVIEW_WEIGHT = 0.4
HIRE_THRESHOLD = 75.0
CONSIDER_THRESHOLD = 50.0
MIN_INTERVIEW_FOR_HIRE = 5.0  # out of 10
MAX_LIST_ITEMS = 4

HIRE, CONSIDER, REJECT = "HIRE", "CONSIDER", "REJECT"


class HiringRecommendationAgent:
    """Usage:
    agent = HiringRecommendationAgent()
    rec = await agent.recommend(candidate_data, match_result, interview_score,
                                jd_data=jd_data)
    # rec["recommendation"], rec["confidence"], rec["reasoning"], ...

    `match_result` is MatchingAgent's output (a bare 0-100 number also works).
    `interview_score` is the 0-10 overall score from a mock interview, or None.
    """

    def __init__(self):
        self.llm = get_llm(temperature=0.2, max_tokens=600)
        self.prompt = ChatPromptTemplate.from_template(RECOMMENDATION_PROMPT)
        self.chain = self.prompt | self.llm

    async def recommend(
        self,
        candidate_data: dict,
        match_result: dict | float | None,
        interview_score: float | None = None,
        *,
        jd_data: dict | None = None,
    ) -> dict[str, Any]:
        """Never raises. The verdict never depends on the LLM."""
        match = (
            match_result
            if isinstance(match_result, dict)
            else {"match_score": match_result}
        )
        match_score = self._clamp(self._to_number(match.get("match_score")), 0, 100)
        interview = self._clamp(self._to_number(interview_score), 0, 10)

        verdict, combined, confidence, flags = self.decide(match_score, interview)

        explanation = await self._explain(
            verdict,
            match_score,
            interview,
            match,
            candidate_data if isinstance(candidate_data, dict) else {},
            jd_data if isinstance(jd_data, dict) else {},
            flags,
        )

        return {
            "recommendation": verdict,
            "confidence": confidence,
            "combined_score": None if combined is None else round(combined, 1),
            "match_score": match_score,
            "interview_score": interview,
            "flags": flags,
            **explanation,
        }

    # ── The decision (pure, deterministic) ─────────────────────────────────

    @staticmethod
    def decide(
        match_score: float | None, interview_score: float | None
    ) -> tuple[str, float | None, int, list[str]]:
        """Return (verdict, combined_score, confidence, flags)."""
        flags: list[str] = []

        if match_score is None and interview_score is None:
            return CONSIDER, None, 20, ["insufficient_data"]

        if interview_score is None:
            combined = match_score
            flags.append("no_interview")
        elif match_score is None:
            combined = interview_score * 10
            flags.append("no_match_score")
        else:
            combined = MATCH_WEIGHT * match_score + INTERVIEW_WEIGHT * (
                interview_score * 10
            )

        if combined >= HIRE_THRESHOLD:
            verdict = HIRE
        elif combined >= CONSIDER_THRESHOLD:
            verdict = CONSIDER
        else:
            verdict = REJECT

        if (
            verdict == HIRE
            and interview_score is not None
            and interview_score < MIN_INTERVIEW_FOR_HIRE
        ):
            verdict = CONSIDER
            flags.append("downgraded_weak_interview")

        # Confidence: distance to the nearest threshold (capped), less when evidence is thin.
        margin = min(abs(combined - HIRE_THRESHOLD), abs(combined - CONSIDER_THRESHOLD))
        confidence = 50 + 40 * (min(margin, 25.0) / 25.0)
        if "no_interview" in flags:
            confidence -= 15
        if "no_match_score" in flags:
            confidence -= 10
        if "downgraded_weak_interview" in flags:
            confidence = min(confidence, 60)

        return verdict, combined, int(max(20, min(95, round(confidence)))), flags

    # ── The explanation (LLM, with a template fallback) ────────────────────

    async def _explain(
        self,
        verdict: str,
        match_score: float | None,
        interview: float | None,
        match: dict,
        candidate: dict,
        jd: dict,
        flags: list[str],
    ) -> dict[str, Any]:
        matching = self._string_list(match.get("matching_skills"))
        missing = self._string_list(match.get("missing_skills"))
        template = self._template(
            verdict, match_score, interview, matching, missing, flags
        )

        if "insufficient_data" in flags:
            return {**template, "reasoning_source": "template"}

        try:
            response = await self.chain.ainvoke(
                {
                    "role": jd.get("title") or "the role",
                    "verdict": verdict,
                    "match_score": (
                        "unavailable" if match_score is None else match_score
                    ),
                    "breakdown": json.dumps(
                        {
                            "skills": match.get("skill_match"),
                            "experience": match.get("experience_match"),
                            "semantic_fit": match.get("semantic_match"),
                        }
                    ),
                    "matching_skills": ", ".join(matching) or "none",
                    "missing_skills": ", ".join(missing) or "none",
                    "interview_score": (
                        "not taken yet" if interview is None else f"{interview} / 10"
                    ),
                    "candidate": json.dumps(self._candidate_view(candidate)),
                }
            )
            raw = getattr(response, "content", response)
            parsed = parse_llm_json(str(raw), fallback={})
        except Exception as e:
            logger.error(f"HiringRecommendationAgent LLM call failed: {e}")
            return {**template, "reasoning_source": "template"}

        if not isinstance(parsed, dict):
            return {**template, "reasoning_source": "template"}

        reasoning = str(parsed.get("reasoning") or "").strip()
        if not reasoning:
            return {**template, "reasoning_source": "template"}

        return {
            "reasoning": reasoning,
            "strengths": self._string_list(parsed.get("strengths"))[:MAX_LIST_ITEMS]
            or template["strengths"],
            "risks": self._string_list(parsed.get("risks"))[:MAX_LIST_ITEMS]
            or template["risks"],
            "reasoning_source": "llm",
        }

    @staticmethod
    def _template(
        verdict: str,
        match_score: float | None,
        interview: float | None,
        matching: list[str],
        missing: list[str],
        flags: list[str],
    ) -> dict[str, Any]:
        """Explanation built purely from the numbers — used when the LLM is down."""
        if "insufficient_data" in flags:
            return {
                "reasoning": "There is not enough data (no match score or interview "
                "score) to make a recommendation.",
                "strengths": [],
                "risks": ["No scoring data available"],
            }

        sentences = []
        if match_score is not None:
            detail = ""
            total = len(matching) + len(missing)
            if total:
                detail = f", covering {len(matching)} of {total} required skills"
            sentences.append(
                f"The resume matches the role at {match_score:g}/100{detail}."
            )
        if interview is not None:
            sentences.append(f"The mock interview scored {interview:g}/10.")
        else:
            sentences.append("No mock interview score is available yet.")
        if "downgraded_weak_interview" in flags:
            sentences.append("A weak interview result rules out a HIRE recommendation.")
        sentences.append(f"Overall recommendation: {verdict}.")

        return {
            "reasoning": " ".join(sentences),
            "strengths": [f"Has {s}" for s in matching[:MAX_LIST_ITEMS]],
            "risks": [f"Missing {s}" for s in missing[:MAX_LIST_ITEMS]],
        }

    @staticmethod
    def _candidate_view(candidate: dict) -> dict:
        """Anonymised: no name, email, phone, links or location."""
        roles = []
        for job in (candidate.get("experience") or [])[:3]:
            if isinstance(job, dict):
                roles.append({"role": job.get("role"), "company": job.get("company")})
        return {
            "skills": candidate.get("skills") or [],
            "experience_years": candidate.get("experience_years"),
            "roles": roles,
        }

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]

    @staticmethod
    def _to_number(value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            match = re.search(r"\d+(?:\.\d+)?", value)
            return float(match.group(0)) if match else None
        return None

    @staticmethod
    def _clamp(value: float | None, low: float, high: float) -> float | None:
        return None if value is None else max(low, min(high, value))
