"""EvaluationAgent — scores a mock interview transcript via Groq.

Output shape matches the InterviewFeedbackResponse schema:
    {
      "scores": {communication, technical_knowledge, problem_solving, confidence},
      "overall_score": float,        # computed in code, never trusted from the LLM
      "strengths": [str], "improvements": [str], "summary": str
    }
On any failure returns a zeroed fallback with `_parse_error: True`. Never raises.
"""

import copy
import logging
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from app.llm.models import get_llm
from app.llm.prompts.evaluation_prompts import EVALUATION_PROMPT
from app.utils.json_parser import parse_llm_json

logger = logging.getLogger(__name__)


DIMENSIONS = ("communication", "technical_knowledge", "problem_solving", "confidence")
MAX_LIST_ITEMS = 4

_FEEDBACK_FALLBACK: dict[str, Any] = {
    "scores": {d: 0.0 for d in DIMENSIONS},
    "overall_score": 0.0,
    "strengths": [],
    "improvements": [],
    "summary": "Feedback unavailable — please try again.",
    "_parse_error": True,
}


class EvaluationAgent:
    """Usage:
    agent = EvaluationAgent()
    feedback = await agent.evaluate_interview(history, company="Amazon", role="SDE II")
    """

    def __init__(self):
        self.llm = get_llm(temperature=0, max_tokens=1500)
        self.prompt = ChatPromptTemplate.from_template(EVALUATION_PROMPT)
        self.chain = self.prompt | self.llm

    async def evaluate_interview(
        self,
        conversation_history: list[dict],
        *,
        company: str = "the company",
        role: str = "the role",
    ) -> dict[str, Any]:
        try:
            response = await self.chain.ainvoke(
                {
                    "company": company,
                    "role": role,
                    "history": self._format_history(conversation_history),
                }
            )
            raw = getattr(response, "content", response)
            data = parse_llm_json(str(raw), fallback={})
        except Exception as e:
            logger.error(f"EvaluationAgent LLM call failed: {e}")
            return copy.deepcopy(_FEEDBACK_FALLBACK)

        if not isinstance(data, dict) or not data:
            return copy.deepcopy(_FEEDBACK_FALLBACK)

        return self._normalize(data)

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _format_history(history: list[dict]) -> str:
        lines = []
        for msg in history or []:
            speaker = "Interviewer" if msg.get("role") == "ai" else "Candidate"
            lines.append(f"{speaker}: {msg.get('content', '')}")
        return "\n".join(lines) if lines else "(empty transcript)"

    @staticmethod
    def _to_score(value: Any) -> float | None:
        """Coerce to a float clamped to [0, 10]; None if not numeric."""
        try:
            return round(max(0.0, min(10.0, float(value))), 1)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items = [str(v).strip() for v in value if v is not None and str(v).strip()]
        return items[:MAX_LIST_ITEMS]

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        # Accept both flat keys and a nested "scores" object from the LLM.
        source = data.get("scores") if isinstance(data.get("scores"), dict) else data
        found = {d: self._to_score(source.get(d)) for d in DIMENSIONS}
        present = [v for v in found.values() if v is not None]

        if not present:
            return copy.deepcopy(_FEEDBACK_FALLBACK)

        # A missing dimension takes the mean of the others rather than an unfair 0.
        fill = round(sum(present) / len(present), 1)
        scores = {d: (v if v is not None else fill) for d, v in found.items()}
        overall = round(sum(scores.values()) / len(scores), 1)

        summary = str(data.get("summary") or "").strip()
        return {
            "scores": scores,
            "overall_score": overall,
            "strengths": self._to_str_list(data.get("strengths")),
            "improvements": self._to_str_list(data.get("improvements")),
            "summary": summary or None,
        }
