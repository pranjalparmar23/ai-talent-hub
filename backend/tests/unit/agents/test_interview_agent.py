"""Unit tests for InterviewAgent.

Focus:
  - Question cleaning (labels / quotes stripped)
  - RAG is optional enrichment: filtered by similarity, never fatal
  - Graceful fallback when the LLM fails or returns nothing
  - Pacing info (question number / final question) reaches the prompt
"""

import pytest

from app.agents.candidate.interview_agent import (
    NO_REFERENCE,
    InterviewAgent,
)
from app.rag.retriever import RetrievedDocument
from tests.unit.agents._fakes import FakeChain, FakeMsg


class FakeRetriever:
    """Stands in for RAGRetriever. Maps collection name -> docs or an exception."""

    def __init__(self, by_collection=None):
        self.by_collection = by_collection or {}
        self.queries: list[tuple[str, str]] = []

    def retrieve(self, collection_name, query, top_k=5, where=None):
        self.queries.append((collection_name, query))
        result = self.by_collection.get(collection_name, [])
        if isinstance(result, Exception):
            raise result
        return result


def _doc(text, similarity=0.8, id="d1"):
    return RetrievedDocument(id=id, document=text, metadata={}, similarity=similarity)


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    a = InterviewAgent()
    a.retriever = FakeRetriever()
    return a


# ── start_session ─────────────────────────────────────────────────────


class TestStartSession:
    @pytest.mark.asyncio
    async def test_returns_cleaned_question(self, agent):
        agent.start_chain = FakeChain(
            response=FakeMsg('Interviewer: "Tell me about yourself."')
        )
        result = await agent.start_session("Amazon", "SDE II")

        assert result["question"] == "Tell me about yourself."
        assert result["session_started"] is True
        assert result["used_rag"] is False

    @pytest.mark.asyncio
    async def test_rag_context_injected_and_flagged(self, agent):
        agent.retriever = FakeRetriever(
            {"company_interviews": [_doc("Amazon asks about Leadership Principles")]}
        )
        chain = FakeChain(response=FakeMsg("Walk me through your background."))
        agent.start_chain = chain

        result = await agent.start_session("Amazon", "SDE II", max_questions=5)

        assert result["used_rag"] is True
        sent = chain.calls[0]
        assert "Leadership Principles" in sent["experiences"]
        assert sent["company"] == "Amazon"
        assert sent["max_questions"] == 5

    @pytest.mark.asyncio
    async def test_low_similarity_and_duplicate_docs_dropped(self, agent):
        agent.retriever = FakeRetriever(
            {
                "company_interviews": [
                    _doc("weak match", similarity=0.05),
                    _doc("same text", similarity=0.9),
                ],
                "interview_experiences": [_doc("same text", similarity=0.9)],
            }
        )
        chain = FakeChain(response=FakeMsg("Q?"))
        agent.start_chain = chain

        await agent.start_session("Google", "SWE")

        context = chain.calls[0]["experiences"]
        assert "weak match" not in context
        assert context.count("same text") == 1

    @pytest.mark.asyncio
    async def test_rag_failure_is_not_fatal(self, agent):
        agent.retriever = FakeRetriever(
            {
                "company_interviews": RuntimeError("chroma down"),
                "interview_experiences": RuntimeError("chroma down"),
            }
        )
        chain = FakeChain(response=FakeMsg("Why this role?"))
        agent.start_chain = chain

        result = await agent.start_session("Meta", "PM")

        assert result["question"] == "Why this role?"
        assert result["used_rag"] is False
        assert chain.calls[0]["experiences"] == NO_REFERENCE

    @pytest.mark.asyncio
    async def test_llm_failure_returns_fallback_question(self, agent):
        agent.start_chain = FakeChain(exc=Exception("Groq rate limit"))
        result = await agent.start_session("Stripe", "Backend Engineer")

        assert "Backend Engineer" in result["question"]
        assert "Stripe" in result["question"]
        assert result["session_started"] is True

    @pytest.mark.asyncio
    async def test_empty_llm_output_returns_fallback(self, agent):
        agent.start_chain = FakeChain(response=FakeMsg("   "))
        result = await agent.start_session("Stripe", "SRE")
        assert "SRE" in result["question"]

    @pytest.mark.asyncio
    async def test_blank_inputs_do_not_crash(self, agent):
        agent.start_chain = FakeChain(response=FakeMsg("Hello!"))
        result = await agent.start_session("  ", "")
        assert result["question"] == "Hello!"


# ── followup ──────────────────────────────────────────────────────────

HISTORY = [
    {"role": "ai", "content": "Tell me about yourself."},
    {"role": "human", "content": "I build APIs."},
]


class TestFollowup:
    @pytest.mark.asyncio
    async def test_returns_cleaned_question(self, agent):
        agent.followup_chain = FakeChain(
            response=FakeMsg("Question 2: How do you handle retries?")
        )
        question = await agent.followup(HISTORY, "I build APIs.")
        assert question == "How do you handle retries?"

    @pytest.mark.asyncio
    async def test_prompt_gets_dialogue_history_and_pacing(self, agent):
        chain = FakeChain(response=FakeMsg("Next?"))
        agent.followup_chain = chain

        await agent.followup(
            HISTORY,
            "I build APIs.",
            company="Amazon",
            role="SDE II",
            question_number=3,
            max_questions=6,
        )

        sent = chain.calls[0]
        assert "Interviewer: Tell me about yourself." in sent["history"]
        assert "Candidate: I build APIs." in sent["history"]
        assert sent["question_number"] == 3
        assert "3 question(s) will remain" in sent["pacing_note"]

    @pytest.mark.asyncio
    async def test_final_question_is_flagged(self, agent):
        chain = FakeChain(response=FakeMsg("Last one?"))
        agent.followup_chain = chain

        await agent.followup(HISTORY, "x", question_number=6, max_questions=6)

        assert "FINAL" in chain.calls[0]["pacing_note"]

    @pytest.mark.asyncio
    async def test_llm_failure_returns_generic_question(self, agent):
        agent.followup_chain = FakeChain(exc=Exception("boom"))
        question = await agent.followup(HISTORY, "I build APIs.")
        assert question.endswith("?")

    @pytest.mark.asyncio
    async def test_empty_llm_output_returns_generic_question(self, agent):
        agent.followup_chain = FakeChain(response=FakeMsg(""))
        question = await agent.followup(HISTORY, "I build APIs.")
        assert question.endswith("?")


class TestFormatHistory:
    def test_empty_history(self):
        assert InterviewAgent.format_history([]) == "(no conversation yet)"

    def test_only_last_messages_kept(self):
        history = [{"role": "human", "content": f"msg{i}"} for i in range(30)]
        rendered = InterviewAgent.format_history(history)
        assert "msg29" in rendered
        assert "msg0\n" not in rendered
        assert rendered.count("Candidate:") == 12
