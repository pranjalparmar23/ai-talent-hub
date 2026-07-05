"""RoadmapAgent — generates a personalized weekly learning plan.

This is the first agent that combines LLM + RAG. Flow:
  1. Take skill gaps and target role from CandidateGraph
  2. Query ChromaDB's learning_resources collection for relevant chunks
  3. Pass gaps + role + retrieved resources to Groq
  4. Return a structured weekly plan grounded in the retrieved resources
"""
import json
import logging
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from app.llm.models import get_llm
from app.llm.prompts.roadmap_prompts import ROADMAP_PROMPT
from app.rag.retriever import RAGRetriever
from app.utils.json_parser import parse_llm_json

logger = logging.getLogger(__name__)


COLLECTION = "learning_resources"

# How many resources to retrieve per skill gap.
# Balance: too few → LLM can't ground, too many → prompt bloat + retrieval noise.
RESOURCES_PER_SKILL = 3
MAX_TOTAL_RESOURCES = 12
SIMILARITY_THRESHOLD = 0.30  # below this = weak match, dropped as noise


_ROADMAP_FALLBACK: dict[str, Any] = {
    "target_role": "target role",
    "total_weeks": 4,
    "weeks": [],
    "summary": "Roadmap unavailable — please try again",
    "_parse_error": True,
}


class RoadmapAgent:
    """Turns skill gaps + RAG context into a weekly learning plan.

    Usage:
        agent = RoadmapAgent()
        plan = await agent.generate(missing_skills, target_role)
        # plan["weeks"], plan["total_weeks"], plan["summary"]
    """

    def __init__(self):
        # Slight temperature for variety — same input twice shouldn't give identical plans
        self.llm = get_llm(temperature=0.3)
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

        # Retrieve resources per skill, dedupe, filter by similarity
        resources = self._retrieve_resources(skill_gaps)
        logger.info(f"RoadmapAgent retrieved {len(resources)} resources for {len(skill_gaps)} gaps")

        try:
            response = await self.chain.ainvoke({
                "skill_gaps": json.dumps(skill_gaps, ensure_ascii=False),
                "target_role": target_role,
                "resources": json.dumps(resources, ensure_ascii=False),
            })
        except Exception as e:
            logger.exception("Groq call failed in RoadmapAgent")
            return {**_ROADMAP_FALLBACK, "target_role": target_role, "_error_reason": str(e)[:200]}

        parsed = parse_llm_json(response.content, fallback={**_ROADMAP_FALLBACK, "target_role": target_role})
        return self._normalize(parsed, target_role=target_role)

    # ── RAG retrieval ────────────────────────────────────────────────────

    def _retrieve_resources(self, skill_gaps: list[str]) -> list[dict]:
        """Query ChromaDB once per skill, merge results, dedupe by content."""
        all_chunks: list[dict] = []
        seen_ids: set[str] = set()

        for skill in skill_gaps:
            query = f"learning resources for {skill}"
            try:
                results = self.retriever.retrieve(
                    collection_name=COLLECTION,
                    query=query,
                    top_k=RESOURCES_PER_SKILL,
                )
            except Exception as e:
                # ChromaDB down or transient error — don't fail the whole roadmap
                logger.warning(f"RAG retrieval failed for skill '{skill}': {e}")
                continue

            for doc in results:
                if doc.id in seen_ids:
                    continue
                if doc.similarity < SIMILARITY_THRESHOLD:
                    continue
                seen_ids.add(doc.id)
                all_chunks.append({
                    "content": doc.document[:500],  # cap chunk size for prompt budget
                    "metadata": doc.metadata,
                    "similarity": round(doc.similarity, 3),
                    "matched_skill": skill,
                })

            if len(all_chunks) >= MAX_TOTAL_RESOURCES:
                break

        # Sort by similarity descending — best matches first for LLM attention
        all_chunks.sort(key=lambda d: d["similarity"], reverse=True)
        return all_chunks[:MAX_TOTAL_RESOURCES]

    # ── Normalization ───────────────────────────────────────────────────

    @staticmethod
    def _clean_skills(value) -> list[str]:
        """Coerce input to a clean list of non-empty skill strings."""
        if not isinstance(value, list):
            return []
        return [s.strip() for s in value if isinstance(s, str) and s.strip()]

    def _normalize(self, parsed: dict, *, target_role: str) -> dict[str, Any]:
        """Enforce shape invariants on LLM output."""
        weeks = parsed.get("weeks")
        if not isinstance(weeks, list):
            weeks = []

        # Clean each week entry — drop malformed ones
        clean_weeks: list[dict] = []
        for i, w in enumerate(weeks, start=1):
            if not isinstance(w, dict):
                continue
            clean_weeks.append({
                "week": int(w.get("week", i)) if str(w.get("week", "")).isdigit() else i,
                "topic": str(w.get("topic", "") or "").strip() or f"Week {i}",
                "goal": str(w.get("goal", "") or "").strip() or None,
                "tasks": [
                    t.strip() for t in (w.get("tasks") or [])
                    if isinstance(t, str) and t.strip()
                ],
                "resources": self._clean_week_resources(w.get("resources")),
                "estimated_hours": self._coerce_hours(w.get("estimated_hours")),
            })

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
    def _clean_week_resources(value) -> list[dict]:
        if not isinstance(value, list):
            return []
        cleaned = []
        for r in value:
            if not isinstance(r, dict):
                continue
            cleaned.append({
                "title": str(r.get("title", "") or "").strip() or "Untitled resource",
                "source": str(r.get("source", "") or "").strip() or None,
                "type": str(r.get("type", "") or "").strip().lower() or "resource",
            })
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