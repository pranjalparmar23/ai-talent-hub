"""InterviewAgent — plays the interviewer in a mock interview via Groq.

Flow:
  1. start_session(): optionally pull reference interview material from ChromaDB,
     then ask the opening question.
  2. followup(): given the transcript so far, ask the next question.

Design notes:
  - Never raises. If the LLM fails we return a sensible generic question so the
    session keeps moving instead of 500-ing mid-interview.
  - RAG is *enrichment*, not a requirement. RAGRetriever.retrieve() is synchronous
    (embedding + Chroma HTTP), so it runs in a worker thread, and any failure or
    empty collection simply means we prompt without reference material.
"""

import asyncio
import logging
import re
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from app.llm.models import get_llm
from app.llm.prompts.interview_prompts import FOLLOWUP_PROMPT, INTERVIEW_PROMPT
from app.rag.retriever import RAGRetriever

logger = logging.getLogger(__name__)


# Collections that can hold company/role interview material. Both are optional.
REFERENCE_COLLECTIONS = ("company_interviews", "interview_experiences")
TOP_K_PER_COLLECTION = 3
SIMILARITY_THRESHOLD = 0.30  # below this = weak match, dropped as noise
MAX_CONTEXT_CHARS = 1800  # keep the prompt small; the LLM only needs a flavour
MAX_HISTORY_MESSAGES = 12  # last 6 exchanges is plenty of context

NO_REFERENCE = (
    "No reference material available. Rely on your general knowledge of how "
    "this company interviews."
)


class InterviewAgent:
    """Generates interviewer questions.

    Usage:
        agent = InterviewAgent()
        start = await agent.start_session("Amazon", "SDE II", max_questions=6)
        nxt = await agent.followup(history, answer, company="Amazon", role="SDE II",
                                   question_number=2, max_questions=6)
    """

    def __init__(self):
        self.llm = get_llm(temperature=0.5, max_tokens=512)
        self.start_prompt = ChatPromptTemplate.from_template(INTERVIEW_PROMPT)
        self.followup_prompt = ChatPromptTemplate.from_template(FOLLOWUP_PROMPT)
        # Tests replace these two wholesale with FakeChain (RunnableSequence is a
        # Pydantic model and cannot be patched in place).
        self.start_chain = self.start_prompt | self.llm
        self.followup_chain = self.followup_prompt | self.llm
        self.retriever = RAGRetriever()

    # ── Public API ─────────────────────────────────────────────────────────

    async def start_session(
        self, company: str, role: str, max_questions: int = 6
    ) -> dict[str, Any]:
        """Ask the opening question.

        Returns:
            {"question": str, "session_started": True, "used_rag": bool}
        """
        company = (company or "").strip() or "the company"
        role = (role or "").strip() or "the role"

        reference = await asyncio.to_thread(self._retrieve_context, company, role)

        try:
            response = await self.start_chain.ainvoke(
                {
                    "company": company,
                    "role": role,
                    "experiences": reference or NO_REFERENCE,
                    "max_questions": max_questions,
                }
            )
            question = self._clean(getattr(response, "content", response))
        except Exception as e:
            logger.error(f"InterviewAgent.start_session LLM call failed: {e}")
            question = ""

        if not question:
            question = (
                f"Thanks for joining. To start, could you walk me through your "
                f"background and what draws you to the {role} role at {company}?"
            )

        return {
            "question": question,
            "session_started": True,
            "used_rag": bool(reference),
        }

    async def followup(
        self,
        conversation_history: list[dict],
        candidate_response: str,
        *,
        company: str = "the company",
        role: str = "the role",
        question_number: int = 2,
        max_questions: int = 6,
    ) -> str:
        """Ask the next question given the transcript (which already includes the
        candidate's latest answer) and where we are in the interview."""
        if question_number >= max_questions:
            pacing = "This is the FINAL question, so make it count."
        else:
            pacing = f"{max_questions - question_number} question(s) will remain after this one."

        try:
            response = await self.followup_chain.ainvoke(
                {
                    "company": company,
                    "role": role,
                    "history": self.format_history(conversation_history),
                    "candidate_response": candidate_response,
                    "question_number": question_number,
                    "max_questions": max_questions,
                    "pacing_note": pacing,
                }
            )
            question = self._clean(getattr(response, "content", response))
        except Exception as e:
            logger.error(f"InterviewAgent.followup LLM call failed: {e}")
            question = ""

        return question or (
            "Thanks for that. Could you walk me through a specific example "
            "from your own experience that illustrates it?"
        )

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def format_history(history: list[dict]) -> str:
        """Render the transcript as plain dialogue (cheaper and clearer for the LLM
        than a JSON dump). Roles are stored as 'ai' / 'human'."""
        lines = []
        for msg in (history or [])[-MAX_HISTORY_MESSAGES:]:
            speaker = "Interviewer" if msg.get("role") == "ai" else "Candidate"
            lines.append(f"{speaker}: {msg.get('content', '')}")
        return "\n".join(lines) if lines else "(no conversation yet)"

    def _retrieve_context(self, company: str, role: str) -> str:
        """Sync: query reference collections, return a compact text block ('' if none).

        Any failure (collection empty/missing, Chroma down) is swallowed — RAG here
        is a nice-to-have.
        """
        query = f"{company} {role} interview questions"
        snippets: list[str] = []
        seen: set[str] = set()

        for collection in REFERENCE_COLLECTIONS:
            try:
                docs = self.retriever.retrieve(
                    collection, query, top_k=TOP_K_PER_COLLECTION
                )
            except Exception as e:
                logger.info(f"InterviewAgent: no RAG context from {collection}: {e}")
                continue

            for doc in docs:
                text = (doc.document or "").strip()
                if doc.similarity < SIMILARITY_THRESHOLD or not text or text in seen:
                    continue
                seen.add(text)
                snippets.append(f"- {text}")

        return "\n".join(snippets)[:MAX_CONTEXT_CHARS]

    @staticmethod
    def _clean(content: Any) -> str:
        """Strip labels/quotes/whitespace the LLM sometimes adds around a question."""
        text = str(content or "").strip()
        text = re.sub(r"^(interviewer|question)\s*\d*\s*:\s*", "", text, flags=re.I)
        return text.strip().strip('"').strip()
