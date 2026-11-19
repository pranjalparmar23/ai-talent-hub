"""RoadmapAgent — generates a personalized weekly learning plan.

This is the first agent that combines LLM + RAG. Flow:
  1. Take skill gaps and target role from CandidateGraph
  2. Query ChromaDB's learning_knowledge collection for relevant chunks, through
     retrieve_with_fallback() (Phase 7)
  3. Pass gaps + role + retrieved resources to Groq
  4. Return a structured weekly plan grounded in the retrieved resources, with the
     reviewed sources it links to

Design notes:
  - Links in the plan can only come from retrieved chunks. A link the model made up is
    dropped, and a link that was retrieved always gets its real title.
  - Which chunks reach the prompt is decided by a rank score: the match (similarity)
    first, nudged by how far the source is trusted and how recently the page was
    confirmed (task 12). Every skill gap with a match keeps at least its best chunk.
  - When nothing reviewed matched, the plan is built from general knowledge: generic
    resource wording, no links, and a `notice` that says so.
  - Retrieval is synchronous (embedding + Chroma HTTP), so it runs in a worker thread.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from app.ingest.schema import LEARNING_COLLECTION
from app.llm.models import get_llm
from app.llm.prompts.roadmap_prompts import ROADMAP_PROMPT
from app.mcp import security
from app.rag.fallback import (
    GENERAL_KNOWLEDGE_PROMPT_NOTE,
    GENERAL_KNOWLEDGE_USER_NOTE,
    retrieve_with_fallback,
)
from app.rag.retriever import RAGRetriever
from app.utils.json_parser import parse_llm_json

logger = logging.getLogger(__name__)


COLLECTION = LEARNING_COLLECTION

# How many resources to retrieve per skill gap.
# Balance: too few → LLM can't ground, too many → prompt bloat + retrieval noise.
RESOURCES_PER_SKILL = 3
MAX_TOTAL_RESOURCES = 12
MAX_SKILLS_SEARCHED = 12  # one search each; later gaps get generic entries
MAX_CONTENT_CHARS = 500  # cap chunk size for prompt budget
# Weak matches are dropped by search() itself (its default similarity cutoff, 0.30).

# ── Ranking (Phase 7, task 12) ───────────────────────────────────────────
# rank score = similarity x (BASE + TRUST x trust + FRESH x freshness)
# Similarity stays the main term, so trust and freshness only separate near ties: the
# official page of a tool beats a loose match from a list, never a clearly better one.
# The weights are starting values. The golden set (task 13) is where to tune them.
RANK_BASE_WEIGHT = 0.6
RANK_TRUST_WEIGHT = 0.3
RANK_FRESH_WEIGHT = 0.1

# How far each source is trusted, from 0 to 1. A source that is not listed gets
# DEFAULT_TRUST, so adding a list never breaks ranking, but a test makes sure every
# link source has an entry on purpose.
SOURCE_TRUST: dict[str, float] = {
    "official-docs": 1.0,  # published by the makers of the tool
    "roadmap-sh": 0.8,  # curated by a well-known community project
    "freecodecamp": 0.8,
    "tech-interview-handbook": 0.7,
}
DEFAULT_TRUST = 0.5

# A page confirmed today scores 1, and the score falls evenly to 0 at this age. It
# matches the 90 day expiry, so a page that is about to expire counts as stale.
FRESH_HORIZON_SECONDS = 90 * 24 * 3600
NEUTRAL_FRESHNESS = 0.5  # when a chunk has no usable date


def source_trust(metadata: Mapping[str, Any]) -> float:
    """0 to 1: how far the chunk's source is trusted."""
    return SOURCE_TRUST.get(str(metadata.get("source_id", "")), DEFAULT_TRUST)


def freshness(metadata: Mapping[str, Any], now: float) -> float:
    """0 to 1: 1 for a page confirmed just now, 0 at FRESH_HORIZON_SECONDS old.

    `fetched_at_ts` is refreshed by every ingest run (a page that still exists is
    re-dated), so it says when the page was last confirmed, not when it was written.
    A missing or odd date is neutral. A date in the future counts as just now.
    """
    raw = metadata.get("fetched_at_ts")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return NEUTRAL_FRESHNESS
    age = max(0.0, now - raw)
    return max(0.0, 1.0 - age / FRESH_HORIZON_SECONDS)


def rank_score(similarity: float, metadata: Mapping[str, Any], now: float) -> float:
    """What orders chunks before the cap. Higher is better."""
    return similarity * (
        RANK_BASE_WEIGHT
        + RANK_TRUST_WEIGHT * source_trust(metadata)
        + RANK_FRESH_WEIGHT * freshness(metadata, now)
    )


_ROADMAP_FALLBACK: dict[str, Any] = {
    "target_role": "target role",
    "total_weeks": 4,
    "weeks": [],
    "summary": "Roadmap unavailable — please try again",
    "_parse_error": True,
}


@dataclass(frozen=True)
class _Candidate:
    """One retrieved chunk, with its rank score, before the cap is applied."""

    score: float
    skill: str
    chunk_id: str
    resource: dict  # for the prompt
    citation: dict


def _select(candidates: list[_Candidate], limit: int) -> list[_Candidate]:
    """Choose which candidates reach the prompt, best first.

    Every skill gap keeps its best chunk, as far as the limit allows (the best of the
    skills first if there are more skills than room). The rest of the room goes to the
    best of what is left. Without this, a skill with a few strong matches could push
    out another skill's only match.
    """
    best: dict[str, _Candidate] = {}
    for c in candidates:
        if c.skill not in best or c.score > best[c.skill].score:
            best[c.skill] = c

    chosen = sorted(best.values(), key=lambda c: c.score, reverse=True)[:limit]
    taken = {c.chunk_id for c in chosen}
    rest = sorted(
        (c for c in candidates if c.chunk_id not in taken),
        key=lambda c: c.score,
        reverse=True,
    )
    chosen += rest[: max(0, limit - len(chosen))]
    return sorted(chosen, key=lambda c: c.score, reverse=True)


@dataclass(frozen=True)
class _Context:
    """What retrieval found for all skill gaps together."""

    resources: list[dict]  # for the prompt, best match first
    sources: list[dict]  # one citation per page, in the same order

    @property
    def grounded(self) -> bool:
        return bool(self.resources)

    def prompt_text(self) -> str:
        """The `resources` input of the prompt. Outside text, so it is marked as data."""
        if not self.resources:
            return GENERAL_KNOWLEDGE_PROMPT_NOTE
        return security.wrap_untrusted(
            COLLECTION, json.dumps(self.resources, ensure_ascii=False)
        )

    def titles_by_url(self) -> dict[str, str]:
        return {c["url"]: c["title"] for c in self.sources if c.get("url")}


class RoadmapAgent:
    """Turns skill gaps + RAG context into a weekly learning plan.

    Usage:
        agent = RoadmapAgent()
        plan = await agent.generate(missing_skills, target_role)
        # plan["weeks"], plan["total_weeks"], plan["summary"]
        # plan["sources"]: reviewed sources the plan links to
        # plan["notice"]: set when the plan rests on no reviewed source
    """

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock  # Unix seconds; tests replace it to fix "now" for freshness
        # Slight temperature for variety — same input twice shouldn't give identical plans
        self.llm = get_llm(temperature=0.3, max_tokens=4096)
        self.prompt = ChatPromptTemplate.from_template(ROADMAP_PROMPT)
        self.chain = self.prompt | self.llm
        self.retriever = RAGRetriever()

    async def generate(
        self,
        skill_gaps: list[str],
        target_role: str,
    ) -> dict[str, Any]:
        """Generate a learning roadmap for the given gaps.

        Args:
            skill_gaps: e.g. ["Kubernetes", "AWS", "Terraform"]
            target_role: e.g. "Senior Backend Engineer"

        Returns:
            Dict matching the roadmap schema. On failure returns fallback with
            `_parse_error: True`. Never raises.
        """
        skill_gaps = self._clean_skills(skill_gaps)
        target_role = (target_role or "target role").strip() or "target role"

        # Edge case: no gaps means no roadmap needed
        if not skill_gaps:
            logger.info("RoadmapAgent: no skill gaps, returning empty plan")
            return {
                "target_role": target_role,
                "total_weeks": 0,
                "weeks": [],
                "summary": "No skill gaps identified — resume already matches the JD requirements.",
            }

        # Retrieve resources per skill and dedupe (weak matches are dropped by search()).
        # Blocking work (embedding + Chroma HTTP), so it runs off the event loop.
        context = await asyncio.to_thread(self._retrieve_resources, skill_gaps)
        logger.info(
            "RoadmapAgent retrieved %d resources for %d gaps (grounded=%s)",
            len(context.resources),
            len(skill_gaps),
            context.grounded,
        )

        try:
            response = await self.chain.ainvoke(
                {
                    "skill_gaps": json.dumps(skill_gaps, ensure_ascii=False),
                    "target_role": target_role,
                    "resources": context.prompt_text(),
                }
            )
        except Exception as e:
            logger.exception("Groq call failed in RoadmapAgent")
            return {
                **_ROADMAP_FALLBACK,
                "target_role": target_role,
                "_error_reason": str(e)[:200],
            }

        parsed = parse_llm_json(
            response.content, fallback={**_ROADMAP_FALLBACK, "target_role": target_role}
        )
        plan = self._normalize(
            parsed, target_role=target_role, titles_by_url=context.titles_by_url()
        )
        self._add_sources(plan, context)
        return plan

    # ── RAG retrieval ────────────────────────────────────────────────────

    def _retrieve_resources(self, skill_gaps: list[str]) -> _Context:
        """Query ChromaDB once per skill, merge results, rank them, apply the cap."""
        if len(skill_gaps) > MAX_SKILLS_SEARCHED:
            logger.info(
                "RoadmapAgent: %d gaps, searching the first %d",
                len(skill_gaps),
                MAX_SKILLS_SEARCHED,
            )
        now = self.clock()
        candidates: list[_Candidate] = []
        seen_ids: set[str] = set()

        for skill in skill_gaps[:MAX_SKILLS_SEARCHED]:
            query = f"learning resources for {skill}"
            try:
                retrieval = retrieve_with_fallback(
                    query,
                    COLLECTION,
                    retriever=self.retriever,
                    top_k=RESOURCES_PER_SKILL,
                )
            except Exception as e:
                # Nothing here may fail the whole roadmap
                logger.warning(f"RAG retrieval failed for a skill: {e}")
                continue

            for chunk in retrieval.chunks:
                if chunk.id in seen_ids:
                    continue
                seen_ids.add(chunk.id)
                citation = chunk.citation
                candidates.append(
                    _Candidate(
                        score=rank_score(chunk.similarity, chunk.metadata, now),
                        skill=skill,
                        chunk_id=chunk.id,
                        resource={
                            "title": citation["title"],
                            "url": citation["url"],
                            "content": chunk.text[:MAX_CONTENT_CHARS],
                            "matched_skill": skill,
                            "similarity": round(chunk.similarity, 3),
                        },
                        citation=citation,
                    )
                )

        # Best rank first, so the model sees the strongest resources first
        chosen = _select(candidates, MAX_TOTAL_RESOURCES)

        sources: list[dict] = []
        listed: set[str] = set()
        for c in chosen:
            key = c.citation["url"] or c.citation["title"]
            if key not in listed:
                listed.add(key)
                sources.append(c.citation)
        return _Context(resources=[c.resource for c in chosen], sources=sources)

    @staticmethod
    def _add_sources(plan: dict[str, Any], context: _Context) -> None:
        """Add `sources` (reviewed pages the plan links to) and `notice` to the plan."""
        used = {
            r["url"]
            for week in plan.get("weeks", [])
            for r in week.get("resources", [])
            if r.get("url")
        }
        plan["sources"] = [c for c in context.sources if c.get("url") in used]
        if plan.get("weeks") and not plan["sources"]:
            plan["notice"] = GENERAL_KNOWLEDGE_USER_NOTE
        else:
            plan["notice"] = None

    # ── Normalization ───────────────────────────────────────────────────

    @staticmethod
    def _clean_skills(value) -> list[str]:
        """Coerce input to a clean list of non-empty skill strings."""
        if not isinstance(value, list):
            return []
        return [s.strip() for s in value if isinstance(s, str) and s.strip()]

    def _normalize(
        self,
        parsed: dict,
        *,
        target_role: str,
        titles_by_url: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Enforce shape invariants on LLM output.

        `titles_by_url` is every link that was retrieved, with its real title. A
        resource may only carry one of those links.
        """
        titles_by_url = titles_by_url or {}
        weeks = parsed.get("weeks")
        if not isinstance(weeks, list):
            weeks = []

        # Clean each week entry — drop malformed ones
        clean_weeks: list[dict] = []
        for i, w in enumerate(weeks, start=1):
            if not isinstance(w, dict):
                continue
            clean_weeks.append(
                {
                    "week": (
                        int(w.get("week", i)) if str(w.get("week", "")).isdigit() else i
                    ),
                    "topic": str(w.get("topic", "") or "").strip() or f"Week {i}",
                    "goal": str(w.get("goal", "") or "").strip() or None,
                    "tasks": [
                        t.strip()
                        for t in (w.get("tasks") or [])
                        if isinstance(t, str) and t.strip()
                    ],
                    "resources": self._clean_week_resources(
                        w.get("resources"), titles_by_url
                    ),
                    "estimated_hours": self._coerce_hours(w.get("estimated_hours")),
                }
            )

        total_weeks = len(clean_weeks)

        summary = parsed.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            summary = None

        result = {
            "target_role": target_role,
            "total_weeks": total_weeks,
            "weeks": clean_weeks,
            "summary": summary,
        }

        if parsed.get("_parse_error"):
            result["_parse_error"] = True

        return result

    @staticmethod
    def _clean_week_resources(value, titles_by_url: dict[str, str]) -> list[dict]:
        """Keep `{title, url, type}` entries.

        - A link that was retrieved gets its real title. This stops a wrong title
          from sitting on a real link.
        - A link that was NOT retrieved is made up, so its entry is dropped.
        - An entry with no link is a generic one ("official documentation").
        """
        if not isinstance(value, list):
            return []
        cleaned = []
        for r in value:
            if not isinstance(r, dict):
                continue
            url = str(r.get("url", "") or "").strip()
            if url:
                title = titles_by_url.get(url)
                if title is None:
                    continue
            else:
                title = str(r.get("title", "") or "").strip() or "Untitled resource"
            cleaned.append(
                {
                    "title": title,
                    "url": url,
                    "type": str(r.get("type", "") or "").strip().lower() or "resource",
                }
            )
        return cleaned

    @staticmethod
    def _coerce_hours(value) -> int | None:
        if value is None:
            return None
        try:
            hours = int(float(value))
            return max(0, min(40, hours))  # cap at 40 — sanity
        except (TypeError, ValueError):
            return None
