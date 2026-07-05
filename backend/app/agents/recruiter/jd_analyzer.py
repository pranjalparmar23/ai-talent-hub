"""JDAnalyzerAgent — extracts structured JSON from raw job description text.

Used by both candidate flow (candidate uploads a target JD to compare against)
and recruiter flow (Phase 5, recruiter uploads a JD to rank candidates).
"""
import logging
import re

from langchain_core.prompts import ChatPromptTemplate

from app.llm.models import get_llm
from app.llm.prompts.jd_prompts import JD_PARSE_PROMPT
from app.utils.json_parser import parse_llm_json

logger = logging.getLogger(__name__)


# Fallback returned when the LLM output is unparseable
_JD_FALLBACK: dict = {
    "title": None,
    "company": None,
    "location": None,
    "role_level": None,
    "employment_type": None,
    "experience_years_min": None,
    "skills_required": [],
    "skills_preferred": [],
    "responsibilities": [],
    "qualifications": [],
    "_parse_error": True,
}


class JDAnalyzerAgent:
    """Turns raw JD text into structured JSON via Groq.

    Usage:
        agent = JDAnalyzerAgent()
        parsed = await agent.analyze(raw_jd_text)
    """

    def __init__(self):
        self.llm = get_llm(temperature=0)
        self.prompt = ChatPromptTemplate.from_template(JD_PARSE_PROMPT)
        self.chain = self.prompt | self.llm

    async def analyze(self, raw_text: str) -> dict:
        """Parse JD text into structured JSON.

        On LLM failure returns the fallback with `_parse_error: True`. Never raises.
        """
        if not raw_text or not raw_text.strip():
            logger.warning("JDAnalyzerAgent called with empty text")
            return {**_JD_FALLBACK, "_error_reason": "empty_input"}

        if len(raw_text) > 30_000:
            logger.warning(f"JD text truncated from {len(raw_text)} to 30000 chars")
            raw_text = raw_text[:30_000]

        try:
            response = await self.chain.ainvoke({"jd_text": raw_text})
        except Exception as e:
            logger.exception("Groq call failed in JDAnalyzerAgent")
            return {**_JD_FALLBACK, "_error_reason": str(e)[:200]}

        parsed = parse_llm_json(
            response.content,
            fallback={**_JD_FALLBACK, "_raw_output": response.content[:500]},
        )

        return self._normalize(parsed)

    # ── Normalization ────────────────────────────────────────────────────

    def _normalize(self, parsed: dict) -> dict:
        """Enforce schema invariants on LLM output.

        Design philosophy: normalize *shape and formatting*, not *values*.
        We don't restrict role titles or employment types to fixed sets — the
        world has too many variations. We just make sure whatever comes back
        is consistent (lowercased, trimmed, deduplicated) so the frontend can
        display it and the ATS scorer can compare it.
        """
        parsed["skills_required"] = self._clean_string_list(parsed.get("skills_required"))
        parsed["skills_preferred"] = self._clean_string_list(parsed.get("skills_preferred"))
        parsed["responsibilities"] = self._clean_string_list(
            parsed.get("responsibilities"), preserve_case=True
        )
        parsed["qualifications"] = self._clean_string_list(
            parsed.get("qualifications"), preserve_case=True
        )

        parsed["role_level"] = self._normalize_slug(parsed.get("role_level"))
        parsed["employment_type"] = self._normalize_slug(parsed.get("employment_type"))
        parsed["experience_years_min"] = self._normalize_min_years(
            parsed.get("experience_years_min")
        )

        # Optional string fields — just trim whitespace and null out empties
        for key in ("title", "company", "location"):
            val = parsed.get(key)
            if isinstance(val, str):
                stripped = val.strip()
                parsed[key] = stripped if stripped else None
            else:
                parsed[key] = None

        return parsed

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _clean_string_list(value, *, preserve_case: bool = False) -> list[str]:
        """Coerce a value to a deduplicated, trimmed list of non-empty strings.

        - Not a list → returns []
        - Skills: lowercase-normalized for dedup, but original case kept
        - Responsibilities/qualifications: preserve original case (they're sentences)
        """
        if not isinstance(value, list):
            return []

        seen: set[str] = set()
        cleaned: list[str] = []

        for item in value:
            if not isinstance(item, str):
                continue
            trimmed = item.strip()
            if not trimmed:
                continue

            # Dedup key — case-insensitive so "Python" and "python" merge
            dedup_key = trimmed.lower() if not preserve_case else trimmed.lower()
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            cleaned.append(trimmed)

        return cleaned

    @staticmethod
    def _normalize_slug(value) -> str | None:
        """Turn LLM output like 'Senior Level', 'Full_Time', 'PART TIME' into
        clean slugs: 'senior-level', 'full-time', 'part-time'.

        Rules:
        - lowercase
        - trim whitespace
        - collapse whitespace and underscores to single hyphens
        - strip non-alphanumeric characters except hyphens
        - return None on empty/non-string input
        """
        if not isinstance(value, str):
            return None
        slug = value.strip().lower()
        if not slug:
            return None
        # Collapse any run of whitespace or underscore into a single hyphen
        slug = re.sub(r"[\s_]+", "-", slug)
        # Drop anything that isn't a letter, digit, or hyphen
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        # Collapse multiple hyphens and trim edges
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug or None

    @staticmethod
    def _normalize_min_years(value) -> int | None:
        """Coerce various number formats to a non-negative integer.

        Handles: 3, 3.0, 3.5 (→ 3), "3", "3+", "3 years", "5+ years experience".
        Returns None if the value can't be interpreted as a number.
        """
        if value is None:
            return None
        if isinstance(value, bool):  # bool is a subclass of int — reject explicitly
            return None
        if isinstance(value, (int, float)):
            return max(0, int(value))
        if isinstance(value, str):
            match = re.search(r"\d+(?:\.\d+)?", value)
            if match:
                try:
                    return max(0, int(float(match.group(0))))
                except ValueError:
                    return None
        return None