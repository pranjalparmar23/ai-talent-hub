"""InterviewAgent — plays the interviewer in a mock interview via Groq.

Flow:
  1. start_session(): pull reference material from the interview_knowledge collection
     (through retrieve_with_fallback), then ask the opening question.
  2. followup(): given the transcript so far, ask the next question.

Design notes:
  - Never raises. If the LLM fails we return a sensible generic question so the
    session keeps moving instead of 500-ing mid-interview.
  - RAG is *enrichment*, not a requirement. If nothing reviewed matches (or Chroma is
    down) the prompt says to use general knowledge, and the result carries a `notice`
    so the app can tell the candidate the question is not based on reviewed sources.
  - `sources` lists the reviewed pages that went into the prompt. The model uses the
    material for flavour and is told not to quote it, so these are the pages the
    question could draw on, not a claim about any single sentence.
  - search() is synchronous (embedding + Chroma HTTP), so it runs in a worker thread.
  - Only the opening question uses reference material. Follow-ups work from the
    transcript.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from app.ingest.schema import INTERVIEW_COLLECTION
from app.llm.models import get_llm
from app.llm.prompts.interview_prompts import FOLLOWUP_PROMPT, INTERVIEW_PROMPT
from app.mcp import security
from app.rag.fallback import (
    GENERAL_KNOWLEDGE_PROMPT_NOTE,
    GENERAL_KNOWLEDGE_USER_NOTE,
    retrieve_with_fallback,
)
from app.rag.retriever import RAGRetriever

logger = logging.getLogger(__name__)


COLLECTION = INTERVIEW_COLLECTION
TOP_K = 5
# The prompt only needs a flavour of the material, so it is capped. Chunks are
# whole passages, so each one gets a share of the budget.
MAX_CONTEXT_CHARS = 3000
MAX_CHUNK_CHARS = 700
MIN_CHUNK_CHARS = 200  # a chunk with less room than this is left out, not squeezed in
MAX_HISTORY_MESSAGES = 12  # last 6 exchanges is plenty of context


@dataclass(frozen=True)
class _Reference:
    """What retrieval gave the opening question."""

    text: str = ""  # the block that goes into the prompt ("" if nothing matched)
    sources: list[dict] = field(default_factory=list)  # one citation per page

    @property
    def grounded(self) -> bool:
        return bool(self.text)

    def prompt_text(self) -> str:
        """The `experiences` input of the prompt. Outside text, so it is marked as data."""
        if not self.text:
            return GENERAL_KNOWLEDGE_PROMPT_NOTE
        return security.wrap_untrusted(COLLECTION, self.text)


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
            {
                "question": str,
                "session_started": True,
                "grounded": bool,        # reviewed material went into the prompt
                "sources": list[dict],   # title/url/licence/attribution, one per page
                "notice": str | None,    # set when the question is not based on reviewed sources
            }
        """
        company = (company or "").strip() or "the company"
        role = (role or "").strip() or "the role"

        reference = await asyncio.to_thread(self._retrieve_context, company, role)
        logger.info(
            "InterviewAgent retrieved %d sources (grounded=%s)",
            len(reference.sources),
            reference.grounded,
        )

        try:
            response = await self.start_chain.ainvoke(
                {
                    "company": company,
                    "role": role,
                    "experiences": reference.prompt_text(),
                    "max_questions": max_questions,
                }
            )
            question = self._clean(getattr(response, "content", response))
        except Exception as e:
            logger.error(f"InterviewAgent.start_session LLM call failed: {e}")
            question = ""

        if not question:
            # The canned opener is not based on anything we retrieved, so it claims
            # no sources and needs no notice.
            return {
                "question": (
                    f"Thanks for joining. To start, could you walk me through your "
                    f"background and what draws you to the {role} role at {company}?"
                ),
                "session_started": True,
                "grounded": False,
                "sources": [],
                "notice": None,
            }

        return {
            "question": question,
            "session_started": True,
            "grounded": reference.grounded,
            "sources": reference.sources,
            "notice": None if reference.grounded else GENERAL_KNOWLEDGE_USER_NOTE,
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

    def _retrieve_context(self, company: str, role: str) -> _Reference:
        """Sync: search the interview knowledge, return a compact block and its sources.

        Any failure (collection empty/missing, Chroma down) is swallowed — RAG here
        is a nice-to-have. Weak matches are already dropped by search() itself.
        """
        query = f"{company} {role} interview questions"
        try:
            retrieval = retrieve_with_fallback(
                query, COLLECTION, retriever=self.retriever, top_k=TOP_K
            )
        except Exception as e:
            logger.warning(f"InterviewAgent: retrieval failed: {e}")
            return _Reference()

        blocks: list[str] = []
        sources: list[dict] = []
        listed: set[str] = set()
        used = 0

        for chunk in retrieval.chunks:
            text = (chunk.text or "").strip()
            if not text:
                continue
            citation = chunk.citation
            prefix = f"- {citation['title']}: "
            room = MAX_CONTEXT_CHARS - used - len(prefix)
            if room < MIN_CHUNK_CHARS:
                break  # the budget is spent

            block = prefix + text[: min(MAX_CHUNK_CHARS, room)]
            blocks.append(block)
            used += len(block) + 1  # +1 for the newline

            key = citation["url"] or chunk.id
            if key not in listed:
                listed.add(key)
                sources.append(citation)

        return _Reference(text="\n".join(blocks), sources=sources)

    @staticmethod
    def _clean(content: Any) -> str:
        """Strip labels/quotes/whitespace the LLM sometimes adds around a question."""
        text = str(content or "").strip()
        text = re.sub(r"^(interviewer|question)\s*\d*\s*:\s*", "", text, flags=re.I)
        return text.strip().strip('"').strip()
