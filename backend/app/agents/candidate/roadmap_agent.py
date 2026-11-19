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
  - When nothing reviewed matched, the plan is built from general knowledge: generic
    resource wording, no links, and a `notice` that says so.
  - Retrieval is synchronous (embedding + Chroma HTTP), so it runs in a worker thread.
"""

import asyncio
import json
import logging
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
MAX_CONTENT_CHARS = 500  # cap chunk size for prompt budget
# Weak matches are dropped by search() itself (its default similarity cutoff, 0.30).


_ROADMAP_FALLBACK: dict[str, Any] = {
    "target_role": "target role",
    "total_weeks": 4,
    "weeks": [],
    "summary": "Roadmap unavailable — please try again",
    "_parse_error": True,
}


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

    def __init__(self):
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
        """Query ChromaDB once per skill, merge results, dedupe by chunk."""
        found: list[tuple[dict, dict]] = []  # (prompt resource, citation)
        seen_ids: set[str] = set()

        for skill in skill_gaps:
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
                found.append(
                    (
                        {
                            "title": citation["title"],
                            "url": citation["url"],
                            "content": chunk.text[:MAX_CONTENT_CHARS],
                            "matched_skill": skill,
                            "similarity": round(chunk.similarity, 3),
                        },
                        citation,
                    )
                )

            if len(found) >= MAX_TOTAL_RESOURCES:
                break

        # Sort by similarity descending — best matches first for LLM attention
        found.sort(key=lambda pair: pair[0]["similarity"], reverse=True)
        found = found[:MAX_TOTAL_RESOURCES]

        sources: list[dict] = []
        listed: set[str] = set()
        for _, citation in found:
            key = citation["url"] or citation["title"]
            if key not in listed:
                listed.add(key)
                sources.append(citation)
        return _Context(resources=[r for r, _ in found], sources=sources)

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
