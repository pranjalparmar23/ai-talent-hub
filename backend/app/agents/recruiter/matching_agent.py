"""MatchingAgent — scores how well a candidate's resume fits a job description.

Deliberately NOT an LLM call. A recruiter ranks many candidates against one JD;
one Groq call per candidate would be slow, rate-limited and non-deterministic
(the same resume could rank differently on two runs). Instead the score is
computed in code from three signals:

    skills      (weight 0.50)  required/preferred skill coverage
    experience  (weight 0.20)  candidate years vs the JD's minimum years
    semantic    (weight 0.30)  local embedding similarity (all-MiniLM-L6-v2)

A signal that cannot be computed (JD lists no skills, no experience bar,
embeddings unavailable) is dropped and the remaining weights are renormalised,
so a missing signal never drags a candidate down or props them up.

Use compute_matches() for many candidates: the JD is embedded once and all
resumes go through the model in a single batch.
"""

import asyncio
import logging
import math
import re
from typing import Any

from app.rag.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


WEIGHTS = {"skills": 0.50, "experience": 0.20, "semantic": 0.30}

# Required skills count for most of the skill score; "nice to have" is a tiebreaker.
REQUIRED_SHARE = 0.80

# MiniLM cosine similarity is not a 0-1 "percent match": unrelated text sits
# around 0.0-0.2 and a genuinely close resume/JD pair around 0.5-0.7. These two
# constants stretch that band onto 0-100. They are heuristics — tune them
# against real resumes if semantic scores look too high or too low.
SEMANTIC_FLOOR = 0.20
SEMANTIC_CEIL = 0.70

# all-MiniLM-L6-v2 only reads ~256 word-pieces, so keep the texts compact and
# put the most informative parts first.
MAX_TEXT_CHARS = 1500

_ALIASES = {
    "k8s": "kubernetes",
    "js": "javascript",
    "ts": "typescript",
    "postgres": "postgresql",
    "psql": "postgresql",
    "golang": "go",
    "nodejs": "node.js",
    "node": "node.js",
    "reactjs": "react",
    "react.js": "react",
    "vuejs": "vue",
    "vue.js": "vue",
    "mongo": "mongodb",
    "tf": "terraform",
    "amazon web services": "aws",
    "gcp": "google cloud",
}

_PARENS = re.compile(r"\([^)]*\)")
_SPACES = re.compile(r"\s+")


class MatchingAgent:
    """Usage:
    agent = MatchingAgent()
    result = await agent.compute_match(resume_data, jd_data)
    results = await agent.compute_matches([r1, r2, r3], jd_data)  # batch
    """

    def __init__(self):
        self.embedder = EmbeddingService  # swappable in tests

    # ── Public API ─────────────────────────────────────────────────────────

    async def compute_match(self, resume_data: dict, jd_data: dict) -> dict[str, Any]:
        return (await self.compute_matches([resume_data], jd_data))[0]

    async def compute_matches(
        self, resumes: list[dict], jd_data: dict
    ) -> list[dict[str, Any]]:
        """Score every resume against one JD. Never raises; result order matches
        the input order."""
        jd = jd_data if isinstance(jd_data, dict) else {}
        resumes = [r if isinstance(r, dict) else {} for r in resumes or []]
        if not resumes:
            return []

        semantic = await self._semantic_scores(
            [self._resume_text(r) for r in resumes], self._jd_text(jd)
        )
        return [self._score(r, jd, s) for r, s in zip(resumes, semantic)]

    # ── Scoring ────────────────────────────────────────────────────────────

    def _score(self, resume: dict, jd: dict, semantic: float | None) -> dict[str, Any]:
        required = self._string_list(jd.get("skills_required"))
        preferred = self._string_list(jd.get("skills_preferred"))
        candidate = self._candidate_skills(resume)

        matched_req, missing_req = self._partition(required, candidate)
        matched_pref, _ = self._partition(preferred, candidate)

        skill_match = self._skill_score(
            len(matched_req), len(required), len(matched_pref), len(preferred)
        )
        candidate_years = self._to_number(resume.get("experience_years")) or 0.0
        min_years = self._to_number(jd.get("experience_years_min"))
        experience_match = self._experience_score(candidate_years, min_years)

        components = {
            "skills": skill_match,
            "experience": experience_match,
            "semantic": semantic,
        }
        available = {k: v for k, v in components.items() if v is not None}

        if available:
            total_weight = sum(WEIGHTS[k] for k in available)
            match_score = sum(WEIGHTS[k] * v for k, v in available.items()) / (
                total_weight
            )
        else:
            match_score = 0.0

        return {
            "match_score": round(match_score, 1),
            "skill_match": self._round(skill_match),
            "experience_match": self._round(experience_match),
            "semantic_match": self._round(semantic),
            "matching_skills": matched_req,
            "missing_skills": missing_req,
            "matching_preferred": matched_pref,
            "summary": self._summary(
                available,
                len(matched_req),
                len(required),
                candidate_years,
                min_years,
                semantic,
            ),
            "semantic_available": semantic is not None,
        }

    @staticmethod
    def _skill_score(
        matched_req: int, total_req: int, matched_pref: int, total_pref: int
    ) -> float | None:
        req = (matched_req / total_req) if total_req else None
        pref = (matched_pref / total_pref) if total_pref else None
        if req is not None and pref is not None:
            return 100.0 * (REQUIRED_SHARE * req + (1 - REQUIRED_SHARE) * pref)
        if req is not None:
            return 100.0 * req
        if pref is not None:
            return 100.0 * pref
        return None  # JD lists no skills at all — nothing to measure

    @staticmethod
    def _experience_score(candidate_years: float, min_years: float | None):
        if not min_years or min_years <= 0:
            return None  # no experience bar stated
        return max(0.0, min(100.0, candidate_years / min_years * 100.0))

    @staticmethod
    def _summary(
        available: dict[str, float],
        matched: int,
        total: int,
        candidate_years: float,
        min_years: float | None,
        semantic: float | None,
    ) -> str:
        if not available:
            return "Not enough information to score this match."
        parts = []
        if total:
            parts.append(f"Matches {matched} of {total} required skills")
        if min_years and min_years > 0:
            parts.append(
                f"{candidate_years:g} years experience vs {min_years:g} required"
            )
        if semantic is not None:
            fit = (
                "strong" if semantic >= 70 else "moderate" if semantic >= 40 else "weak"
            )
            parts.append(f"{fit} overall profile fit")
        return "; ".join(parts) + "."

    # ── Skills ─────────────────────────────────────────────────────────────

    @classmethod
    def _candidate_skills(cls, resume: dict) -> list[str]:
        """Skills list plus the tech stacks of the candidate's projects."""
        skills = cls._string_list(resume.get("skills"))
        for project in resume.get("projects") or []:
            if isinstance(project, dict):
                skills.extend(cls._string_list(project.get("tech_stack")))
        return [cls._canon(s) for s in skills]

    @classmethod
    def _partition(
        cls, jd_skills: list[str], candidate_canon: list[str]
    ) -> tuple[list[str], list[str]]:
        matched, missing = [], []
        for skill in jd_skills:
            (matched if cls._has_skill(skill, candidate_canon) else missing).append(
                skill
            )
        return matched, missing

    @classmethod
    def _has_skill(cls, jd_skill: str, candidate_canon: list[str]) -> bool:
        """True if some candidate skill equals the JD skill, or is a more specific
        form of it ("aws lambda" satisfies "aws"; the reverse does not hold)."""
        target = cls._canon(jd_skill)
        if not target:
            return False
        pattern = re.compile(rf"(?<![a-z0-9+#.]){re.escape(target)}(?![a-z0-9+#.])")
        return any(c == target or pattern.search(c) for c in candidate_canon)

    @staticmethod
    def _canon(skill: str) -> str:
        """Canonical comparison form: lowercase, no parentheticals, aliases
        resolved, naive singular. Applied identically to both sides."""
        s = _SPACES.sub(" ", _PARENS.sub(" ", str(skill)).lower()).strip()
        if s in _ALIASES:
            s = _ALIASES[s]
        tokens = []
        for token in s.split(" "):
            token = _ALIASES.get(token, token)
            if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
                token = token[:-1]
            tokens.append(token)
        return " ".join(tokens)

    # ── Semantic similarity ────────────────────────────────────────────────

    async def _semantic_scores(
        self, resume_texts: list[str], jd_text: str
    ) -> list[float | None]:
        """0-100 similarity per resume, or None where it cannot be computed."""
        n = len(resume_texts)
        indexes = [i for i, t in enumerate(resume_texts) if t]
        if not jd_text or not indexes:
            return [None] * n

        try:
            vectors = await asyncio.to_thread(
                self.embedder.embed_batch,
                [jd_text] + [resume_texts[i] for i in indexes],
            )
        except Exception as e:
            logger.warning(f"MatchingAgent: embeddings unavailable, skipping: {e}")
            return [None] * n

        scores: list[float | None] = [None] * n
        jd_vector = vectors[0]
        for i, vector in zip(indexes, vectors[1:]):
            similarity = self._cosine(jd_vector, vector)
            stretched = (similarity - SEMANTIC_FLOOR) / (SEMANTIC_CEIL - SEMANTIC_FLOOR)
            scores[i] = max(0.0, min(1.0, stretched)) * 100.0
        return scores

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        return dot / norm if norm else 0.0

    @classmethod
    def _resume_text(cls, resume: dict) -> str:
        parts = [str(resume.get("summary") or "")]
        parts.append(", ".join(cls._string_list(resume.get("skills"))))
        for job in (resume.get("experience") or [])[:4]:
            if isinstance(job, dict):
                parts.append(str(job.get("role") or ""))
                parts.extend(cls._string_list(job.get("highlights"))[:3])
        for project in (resume.get("projects") or [])[:3]:
            if isinstance(project, dict):
                parts.append(str(project.get("description") or ""))
        return cls._compact(parts)

    @classmethod
    def _jd_text(cls, jd: dict) -> str:
        parts = [str(jd.get("title") or "")]
        parts.append(", ".join(cls._string_list(jd.get("skills_required"))))
        parts.extend(cls._string_list(jd.get("responsibilities"))[:4])
        return cls._compact(parts)

    # ── Small helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _compact(parts: list[str]) -> str:
        text = _SPACES.sub(" ", " ".join(p.strip() for p in parts if p and p.strip()))
        return text.strip()[:MAX_TEXT_CHARS]

    @staticmethod
    def _string_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]

    @staticmethod
    def _to_number(value) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            match = re.search(r"\d+(?:\.\d+)?", value)
            return float(match.group(0)) if match else None
        return None

    @staticmethod
    def _round(value: float | None) -> float | None:
        return None if value is None else round(value, 1)
