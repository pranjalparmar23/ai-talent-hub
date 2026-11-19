"""Tests for the retrieval fallback (app/rag/fallback.py).

Two layers. The first uses a stand-in retriever that returns scripted results, to pin
down the routing rules. The second runs the real `RAGRetriever` on top of a fake
vector store, so the whole path from filters to route is checked with no model
download and no database.
"""

import logging
from dataclasses import FrozenInstanceError

import pytest

from app.ingest.schema import INTERVIEW_COLLECTION, LEARNING_COLLECTION
from app.rag import fallback as fallback_module
from app.rag import retriever as retriever_module
from app.rag.fallback import (
    GENERAL_KNOWLEDGE_PROMPT_NOTE,
    GENERAL_KNOWLEDGE_USER_NOTE,
    REASON_EMPTY_QUERY,
    REASON_NO_MATCH,
    REASON_RETRIEVAL_ERROR,
    REASON_WEAK_MATCH,
    ROUTE_GENERAL,
    ROUTE_KNOWLEDGE,
    Retrieval,
    retrieve_with_fallback,
)
from app.rag.retriever import KnowledgeChunk, SearchResult

# ── layer 1: a stand-in retriever ────────────────────────────


class StandInRetriever:
    """Returns one scripted result (or raises one scripted error) and records calls."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[tuple] = []

    def search(self, query, collection, **options):
        self.calls.append((query, collection, options))
        if self.error:
            raise self.error
        return self.result


def chunk(chunk_id="d1:0", similarity=0.8, url="https://example.com/d1"):
    return KnowledgeChunk(
        id=chunk_id,
        text="some text",
        similarity=similarity,
        metadata={
            "title": "Page d1",
            "url": url,
            "licence": "MIT",
            "attribution": "Credit for d1",
        },
    )


def found():
    return SearchResult(
        INTERVIEW_COLLECTION,
        [chunk(), chunk("d2:0", 0.6, "https://example.com/d2")],
        5,
        1,
    )


def nothing(candidates=0, below_cutoff=0):
    return SearchResult(INTERVIEW_COLLECTION, [], candidates, below_cutoff)


def ask(retriever, query="conflict questions", collection=INTERVIEW_COLLECTION, **kw):
    return retrieve_with_fallback(query, collection, retriever=retriever, **kw)


# routes


def test_chunks_found_takes_the_knowledge_route():
    got = ask(StandInRetriever(found()))
    assert got.route == ROUTE_KNOWLEDGE
    assert got.reason == ""
    assert got.grounded is True


def test_the_knowledge_route_returns_the_chunks_in_order():
    got = ask(StandInRetriever(found()))
    assert [c.id for c in got.chunks] == ["d1:0", "d2:0"]


def test_the_knowledge_route_returns_the_citations_and_no_notes():
    got = ask(StandInRetriever(found()))
    assert [c["url"] for c in got.citations] == [
        "https://example.com/d1",
        "https://example.com/d2",
    ]
    assert got.prompt_note == ""
    assert got.user_note == ""


def test_nothing_passing_the_filters_is_no_match():
    got = ask(StandInRetriever(nothing(candidates=0)))
    assert got.route == ROUTE_GENERAL
    assert got.reason == REASON_NO_MATCH
    assert got.grounded is False


def test_chunks_that_were_all_too_weak_is_weak_match():
    got = ask(StandInRetriever(nothing(candidates=7, below_cutoff=7)))
    assert (got.route, got.reason) == (ROUTE_GENERAL, REASON_WEAK_MATCH)


@pytest.mark.parametrize("query", ["", "   ", "\n\t", None])
def test_a_blank_query_is_empty_query(query):
    got = ask(StandInRetriever(nothing()), query=query)
    assert (got.route, got.reason) == (ROUTE_GENERAL, REASON_EMPTY_QUERY)


def test_the_general_route_has_no_chunks_and_no_citations():
    got = ask(StandInRetriever(nothing(candidates=3, below_cutoff=3)))
    assert got.chunks == []
    assert got.citations == []


def test_the_general_route_carries_both_notes():
    got = ask(StandInRetriever(nothing()))
    assert got.prompt_note == GENERAL_KNOWLEDGE_PROMPT_NOTE
    assert got.user_note == GENERAL_KNOWLEDGE_USER_NOTE


def test_the_notes_say_what_they_must():
    prompt = GENERAL_KNOWLEDGE_PROMPT_NOTE.lower()
    assert "general knowledge" in prompt
    assert "do not invent sources" in prompt
    user = GENERAL_KNOWLEDGE_USER_NOTE.lower()
    assert "not based on our reviewed sources" in user


def test_a_general_route_never_exposes_chunks_even_if_a_result_is_attached():
    # Guard: whatever is attached, the general route must not hand out citations.
    sneaky = Retrieval(ROUTE_GENERAL, REASON_NO_MATCH, found())
    assert sneaky.chunks == []
    assert sneaky.citations == []


def test_the_result_is_kept_on_the_retrieval_for_the_counts():
    result = nothing(candidates=4, below_cutoff=4)
    assert ask(StandInRetriever(result)).result is result


# failures


def test_a_retrieval_failure_takes_the_general_route_without_raising():
    got = ask(StandInRetriever(error=RuntimeError("chroma is down")))
    assert (got.route, got.reason) == (ROUTE_GENERAL, REASON_RETRIEVAL_ERROR)
    assert got.result is None
    assert got.chunks == [] and got.citations == []
    assert got.prompt_note and got.user_note


@pytest.mark.parametrize("error", [ConnectionError("x"), OSError("x"), KeyError("x")])
def test_any_ordinary_error_is_absorbed(error):
    assert ask(StandInRetriever(error=error)).reason == REASON_RETRIEVAL_ERROR


def test_a_retrieval_failure_is_logged_with_its_traceback(caplog):
    with caplog.at_level(logging.ERROR, logger="app.rag.fallback"):
        ask(StandInRetriever(error=RuntimeError("chroma is down")))
    record = next(r for r in caplog.records if r.levelno == logging.ERROR)
    assert record.exc_info is not None
    assert INTERVIEW_COLLECTION in record.getMessage()


def test_a_value_error_is_a_bug_in_the_caller_and_is_raised():
    with pytest.raises(ValueError, match="role_family"):
        ask(StandInRetriever(error=ValueError("role_family must be one of ...")))


# arguments


def test_the_query_collection_and_options_are_passed_to_search():
    stand_in = StandInRetriever(found())
    ask(
        stand_in,
        query="system design",
        collection=LEARNING_COLLECTION,
        top_k=3,
        domain="software-engineering",
        role_family="technical",
        level="senior",
    )
    assert stand_in.calls == [
        (
            "system design",
            LEARNING_COLLECTION,
            {
                "top_k": 3,
                "domain": "software-engineering",
                "role_family": "technical",
                "level": "senior",
            },
        )
    ]


def test_with_no_options_none_are_passed():
    stand_in = StandInRetriever(found())
    ask(stand_in)
    assert stand_in.calls[0][2] == {}


def test_a_retriever_is_created_when_none_is_given(monkeypatch):
    made = []

    class Made(StandInRetriever):
        def __init__(self):
            super().__init__(found())
            made.append(self)

    monkeypatch.setattr(fallback_module, "RAGRetriever", Made)
    got = retrieve_with_fallback("conflict questions", INTERVIEW_COLLECTION)
    assert got.grounded and len(made) == 1
    assert made[0].calls[0][:2] == ("conflict questions", INTERVIEW_COLLECTION)


# the retrieval object


def test_a_retrieval_cannot_be_changed_after_it_is_made():
    got = ask(StandInRetriever(found()))
    with pytest.raises(FrozenInstanceError):
        got.route = ROUTE_GENERAL


# logging


def log_line(caplog):
    lines = [
        r.getMessage() for r in caplog.records if "fallback route=" in r.getMessage()
    ]
    assert len(lines) == 1
    return lines[0]


def test_the_log_line_for_a_hit_has_the_counts(caplog):
    with caplog.at_level(logging.INFO, logger="app.rag.fallback"):
        ask(StandInRetriever(found()))
    assert log_line(caplog) == (
        "fallback route=knowledge reason=- "
        f"collection={INTERVIEW_COLLECTION} candidates=5 below_cutoff=1"
    )


def test_the_log_line_for_a_miss_has_the_reason(caplog):
    with caplog.at_level(logging.INFO, logger="app.rag.fallback"):
        ask(StandInRetriever(nothing(candidates=4, below_cutoff=4)))
    assert log_line(caplog) == (
        "fallback route=general_knowledge reason=weak_match "
        f"collection={INTERVIEW_COLLECTION} candidates=4 below_cutoff=4"
    )


def test_the_log_line_after_a_failure_shows_zero_counts(caplog):
    with caplog.at_level(logging.INFO, logger="app.rag.fallback"):
        ask(StandInRetriever(error=RuntimeError("down")))
    assert "reason=retrieval_error" in log_line(caplog)
    assert "candidates=0 below_cutoff=0" in log_line(caplog)


def test_the_query_text_is_never_logged(caplog):
    secret = "my private career change plans"
    with caplog.at_level(logging.DEBUG, logger="app.rag.fallback"):
        ask(StandInRetriever(found()), query=secret)
        ask(StandInRetriever(nothing()), query=secret)
        ask(StandInRetriever(error=RuntimeError("down")), query=secret)
    assert "private" not in caplog.text and "career" not in caplog.text


# ── layer 2: the real retriever on a fake vector store ───────


class FakeVectorStore:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.queries: list[dict] = []

    def query(
        self,
        collection_name,
        query_embedding,
        top_k=5,
        where=None,
        include_embeddings=False,
    ):
        self.queries.append({"collection_name": collection_name, "where": where})
        if self.error:
            raise self.error
        return [dict(r) for r in self.rows][:top_k]


class FakeEmbeddingService:
    @staticmethod
    def embed_text(text):
        return [0.5, 0.5]


def store_row(chunk_id, similarity, doc="d1"):
    return {
        "id": chunk_id,
        "document": f"text of {chunk_id}",
        "distance": 1.0 - similarity,
        "metadata": {
            "doc_id": doc,
            "url": f"https://example.com/{doc}",
            "title": f"Page {doc}",
            "licence": "MIT",
            "attribution": f"Credit for {doc}",
        },
    }


@pytest.fixture
def make_store(monkeypatch):
    def _make(rows=None, error=None):
        fake = FakeVectorStore(rows, error)
        monkeypatch.setattr(retriever_module, "VectorStore", fake)
        monkeypatch.setattr(retriever_module, "EmbeddingService", FakeEmbeddingService)
        return fake

    return _make


def test_a_strong_match_takes_the_knowledge_route_with_its_source(make_store):
    make_store([store_row("d1:0", 0.82)])
    got = retrieve_with_fallback("conflict questions", INTERVIEW_COLLECTION)
    assert got.route == ROUTE_KNOWLEDGE
    assert [c.id for c in got.chunks] == ["d1:0"]
    assert got.citations == [
        {
            "title": "Page d1",
            "url": "https://example.com/d1",
            "licence": "MIT",
            "attribution": "Credit for d1",
        }
    ]


def test_only_weak_matches_take_the_general_route_and_are_counted(make_store):
    make_store([store_row("d1:0", 0.10), store_row("d2:0", 0.12, doc="d2")])
    got = retrieve_with_fallback("conflict questions", INTERVIEW_COLLECTION)
    assert (got.route, got.reason) == (ROUTE_GENERAL, REASON_WEAK_MATCH)
    assert (got.result.candidates, got.result.below_cutoff) == (2, 2)


def test_an_empty_store_takes_the_general_route_as_no_match(make_store):
    make_store([])
    got = retrieve_with_fallback("conflict questions", INTERVIEW_COLLECTION)
    assert (got.route, got.reason) == (ROUTE_GENERAL, REASON_NO_MATCH)


def test_a_blank_query_never_reaches_the_store(make_store):
    fake = make_store([store_row("d1:0", 0.9)])
    got = retrieve_with_fallback("   ", INTERVIEW_COLLECTION)
    assert (got.route, got.reason) == (ROUTE_GENERAL, REASON_EMPTY_QUERY)
    assert fake.queries == []


def test_the_minimum_similarity_can_be_changed_per_call(make_store):
    make_store([store_row("d1:0", 0.40)])
    assert retrieve_with_fallback("q", INTERVIEW_COLLECTION).grounded
    strict = retrieve_with_fallback("q", INTERVIEW_COLLECTION, min_similarity=0.5)
    assert strict.reason == REASON_WEAK_MATCH


def test_filters_reach_the_vector_store(make_store):
    fake = make_store([])
    retrieve_with_fallback("q", INTERVIEW_COLLECTION, role_family="technical")
    conditions = fake.queries[0]["where"]["$and"]
    assert {"role_family": {"$in": ["technical", "all"]}} in conditions


def test_a_failing_vector_store_takes_the_general_route(make_store):
    make_store(error=ConnectionError("chroma is not reachable"))
    got = retrieve_with_fallback("conflict questions", INTERVIEW_COLLECTION)
    assert (got.route, got.reason) == (ROUTE_GENERAL, REASON_RETRIEVAL_ERROR)


def test_a_typo_in_a_filter_still_raises_and_the_store_is_not_asked(make_store):
    fake = make_store([store_row("d1:0", 0.9)])
    with pytest.raises(ValueError, match="role_family"):
        retrieve_with_fallback("q", INTERVIEW_COLLECTION, role_family="tecnical")
    assert fake.queries == []


def test_an_unknown_collection_still_raises(make_store):
    make_store([])
    with pytest.raises(ValueError, match="search\\(\\) works on"):
        retrieve_with_fallback("q", "resumes")


def test_an_empty_query_with_a_typo_still_raises(make_store):
    make_store([])
    with pytest.raises(ValueError, match="level"):
        retrieve_with_fallback("", INTERVIEW_COLLECTION, level="seniro")
