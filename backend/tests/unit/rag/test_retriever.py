"""Tests for the retriever (app/rag/retriever.py).

The embedding model and ChromaDB are replaced by fakes, so these run with no model
download and no database. The filters sent to ChromaDB are asserted exactly,
because a typo there would quietly return nothing.
"""

import logging
from datetime import datetime, timezone

import pytest

from app.ingest.schema import INTERVIEW_COLLECTION, LEARNING_COLLECTION, SCHEMA_VERSION
from app.rag import retriever as retriever_module
from app.rag.retriever import (
    DEFAULT_MAX_PER_DOC,
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_MMR_LAMBDA,
    DEFAULT_TOP_K,
    FETCH_FACTOR,
    MIN_FETCH,
    KnowledgeChunk,
    RAGRetriever,
    SearchResult,
    build_where,
)

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc)
NOW_TS = int(NOW.timestamp())


# ── fakes ────────────────────────────────────────────────────


class FakeVectorStore:
    """Returns scripted rows and records every query."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.queries: list[dict] = []

    def query(
        self,
        collection_name,
        query_embedding,
        top_k=5,
        where=None,
        include_embeddings=False,
    ):
        self.queries.append(
            dict(
                collection_name=collection_name,
                query_embedding=query_embedding,
                top_k=top_k,
                where=where,
                include_embeddings=include_embeddings,
            )
        )
        rows = [dict(r) for r in self.rows]
        if not include_embeddings:
            for r in rows:
                r.pop("embedding", None)
        return rows[:top_k]


class FakeEmbeddingService:
    texts: list[str] = []

    @classmethod
    def embed_text(cls, text):
        cls.texts.append(text)
        return [0.5, 0.5]


@pytest.fixture
def store(monkeypatch):
    fake = FakeVectorStore()
    FakeEmbeddingService.texts = []
    monkeypatch.setattr(retriever_module, "VectorStore", fake)
    monkeypatch.setattr(retriever_module, "EmbeddingService", FakeEmbeddingService)
    return fake


def row(chunk_id, similarity, *, doc="d1", embedding=None, text=None, **meta):
    metadata = {
        "doc_id": doc,
        "url": f"https://example.com/{doc}",
        "title": f"Page {doc}",
        "licence": "MIT",
        "attribution": f"Credit for {doc}",
        **meta,
    }
    out = {
        "id": chunk_id,
        "document": text or f"text of {chunk_id}",
        "metadata": metadata,
        "distance": 1.0 - similarity,
    }
    if embedding is not None:
        out["embedding"] = embedding
    return out


def search(store_rows, **kwargs):
    store = retriever_module.VectorStore
    store.rows = store_rows
    kwargs.setdefault("now", NOW)
    return RAGRetriever().search("how do I answer", INTERVIEW_COLLECTION, **kwargs)


def ids(result):
    return [c.id for c in result.chunks]


# ── the original retrieve() is unchanged ─────────────────────


def test_retrieve_turns_distance_into_similarity(store):
    store.rows = [row("a:0000", 0.8)]
    (doc,) = RAGRetriever().retrieve("learning_resources", "docker basics")
    assert doc.id == "a:0000"
    assert doc.similarity == pytest.approx(0.8)
    assert store.queries[0]["where"] is None
    assert store.queries[0]["top_k"] == 5
    assert store.queries[0]["include_embeddings"] is False


def test_retrieve_with_an_empty_query_does_nothing(store):
    assert RAGRetriever().retrieve("learning_resources", "   ") == []
    assert store.queries == []
    assert FakeEmbeddingService.texts == []


def test_retrieve_passes_a_raw_where_filter_through(store):
    RAGRetriever().retrieve("learning_resources", "q", where={"topic": "dsa"})
    assert store.queries[0]["where"] == {"topic": "dsa"}


def test_retrieve_across_collections_keeps_each_collection_apart(store):
    store.rows = [row("a:0000", 0.9)]
    found = RAGRetriever().retrieve_across_collections(["one", "two"], "q")
    assert set(found) == {"one", "two"}
    assert [q["collection_name"] for q in store.queries] == ["one", "two"]


# ── build_where ──────────────────────────────────────────────


def test_the_base_filter_is_current_schema_and_not_expired():
    assert build_where(now_ts=NOW_TS) == {
        "$and": [
            {"schema_version": SCHEMA_VERSION},
            {"expires_at_ts": {"$gt": NOW_TS}},
        ]
    }


def test_a_domain_also_matches_general_content():
    where = build_where(now_ts=NOW_TS, domain="product")
    assert {"domain": {"$in": ["product", "general"]}} in where["$and"]


def test_asking_for_the_general_domain_is_a_plain_match():
    where = build_where(now_ts=NOW_TS, domain="general")
    assert {"domain": "general"} in where["$and"]


def test_a_role_family_also_matches_all():
    where = build_where(now_ts=NOW_TS, role_family="technical")
    assert {"role_family": {"$in": ["technical", "all"]}} in where["$and"]


def test_a_level_also_matches_all():
    where = build_where(now_ts=NOW_TS, level="senior")
    assert {"level": {"$in": ["senior", "all"]}} in where["$and"]


def test_asking_for_all_is_a_plain_match():
    where = build_where(now_ts=NOW_TS, level="all", role_family="all")
    assert {"level": "all"} in where["$and"]
    assert {"role_family": "all"} in where["$and"]


def test_one_content_type_is_a_plain_match():
    where = build_where(now_ts=NOW_TS, content_type="experience")
    assert {"content_type": "experience"} in where["$and"]


def test_several_content_types_match_any_of_them():
    where = build_where(now_ts=NOW_TS, content_type=["experience", "concept_note"])
    assert {"content_type": {"$in": ["experience", "concept_note"]}} in where["$and"]


def test_repeated_content_types_are_counted_once():
    where = build_where(now_ts=NOW_TS, content_type=["experience", "experience"])
    assert {"content_type": "experience"} in where["$and"]


def test_every_filter_together():
    where = build_where(
        now_ts=NOW_TS,
        domain="data",
        role_family="technical",
        level="mid",
        content_type="experience",
    )
    assert len(where["$and"]) == 6


def test_the_filter_always_has_at_least_two_conditions():
    # ChromaDB rejects "$and" with fewer than two.
    assert len(build_where(now_ts=NOW_TS)["$and"]) >= 2


# ── search: bad filters fail loudly ──────────────────────────


def test_search_refuses_a_legacy_collection(store):
    with pytest.raises(ValueError, match="retrieve"):
        RAGRetriever().search("q", "dsa_notes", now=NOW)
    assert store.queries == []


def test_search_refuses_an_unknown_role_family(store):
    with pytest.raises(ValueError, match="role_family"):
        RAGRetriever().search("q", INTERVIEW_COLLECTION, role_family="wizard", now=NOW)


def test_search_refuses_an_unknown_level(store):
    with pytest.raises(ValueError, match="level"):
        RAGRetriever().search("q", INTERVIEW_COLLECTION, level="expert", now=NOW)


def test_search_refuses_an_unknown_content_type(store):
    with pytest.raises(ValueError, match="unknown content_type"):
        RAGRetriever().search(
            "q", INTERVIEW_COLLECTION, content_type="podcast", now=NOW
        )


def test_search_refuses_a_content_type_from_the_other_collection(store):
    with pytest.raises(ValueError, match="stored in learning_knowledge"):
        RAGRetriever().search("q", INTERVIEW_COLLECTION, content_type="course", now=NOW)


def test_search_accepts_a_content_type_that_fits_the_collection(store):
    RAGRetriever().search("q", LEARNING_COLLECTION, content_type="course", now=NOW)
    assert len(store.queries) == 1


def test_now_must_have_a_time_zone(store):
    with pytest.raises(ValueError, match="timezone-aware"):
        RAGRetriever().search("q", INTERVIEW_COLLECTION, now=datetime(2026, 1, 1))


# ── search: what is asked of ChromaDB ────────────────────────


def test_search_sends_the_filter_the_collection_and_the_query_vector(store):
    search([], domain="product", level="mid")
    (sent,) = store.queries
    assert sent["collection_name"] == INTERVIEW_COLLECTION
    assert sent["query_embedding"] == [0.5, 0.5]
    assert sent["where"] == build_where(now_ts=NOW_TS, domain="product", level="mid")
    assert FakeEmbeddingService.texts == ["how do I answer"]


def test_the_clock_decides_what_counts_as_expired(store):
    search([], now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    expiry = store.queries[0]["where"]["$and"][1]
    assert expiry == {"expires_at_ts": {"$gt": 1798761600}}


def test_the_domain_is_tidied_before_it_is_used(store):
    search([], domain="  Product ")
    assert {"domain": {"$in": ["product", "general"]}} in store.queries[0]["where"][
        "$and"
    ]


def test_several_times_top_k_candidates_are_fetched(store):
    search([], top_k=10)
    assert store.queries[0]["top_k"] == 10 * FETCH_FACTOR


def test_a_small_top_k_still_fetches_the_minimum(store):
    search([], top_k=2)
    assert store.queries[0]["top_k"] == MIN_FETCH


def test_embeddings_are_requested_only_for_mmr(store):
    search([], mmr=True)
    search([], mmr=False)
    assert [q["include_embeddings"] for q in store.queries] == [True, False]


def test_an_empty_query_makes_no_calls(store):
    result = RAGRetriever().search("  ", INTERVIEW_COLLECTION, now=NOW)
    assert result.empty
    assert store.queries == []
    assert FakeEmbeddingService.texts == []


def test_a_top_k_of_zero_makes_no_calls(store):
    assert search([row("a:0000", 0.9)], top_k=0).empty
    assert store.queries == []


def test_the_defaults_are_the_documented_ones():
    assert (DEFAULT_TOP_K, DEFAULT_MIN_SIMILARITY) == (5, 0.40)
    assert (DEFAULT_MMR_LAMBDA, DEFAULT_MAX_PER_DOC) == (0.7, 2)


# ── search: cutoff and results ───────────────────────────────


def test_weak_matches_are_dropped_and_counted(store):
    rows = [row("a:0000", 0.9, doc="a"), row("b:0000", 0.2, doc="b")]
    result = search(rows)
    assert ids(result) == ["a:0000"]
    assert result.candidates == 2
    assert result.below_cutoff == 1


def test_a_match_exactly_at_the_cutoff_is_kept(store):
    # 0.5 is exact in floating point, so the match really equals the cutoff.
    result = search([row("a:0000", 0.5)], min_similarity=0.5)
    assert ids(result) == ["a:0000"]


def test_the_cutoff_can_be_changed(store):
    rows = [row("a:0000", 0.5, doc="a"), row("b:0000", 0.2, doc="b")]
    assert len(search(rows, min_similarity=0.1).chunks) == 2
    assert len(search(rows, min_similarity=0.6).chunks) == 0


def test_nothing_found_gives_an_empty_result_not_an_error(store):
    result = search([])
    assert result.empty
    assert (result.candidates, result.below_cutoff) == (0, 0)
    assert result.collection == INTERVIEW_COLLECTION


def test_everything_too_weak_is_empty_but_says_why(store):
    result = search([row("a:0000", 0.1, doc="a"), row("b:0000", 0.2, doc="b")])
    assert result.empty
    assert result.candidates == 2
    assert result.below_cutoff == 2


def test_results_carry_text_similarity_and_metadata(store):
    result = search([row("a:0000", 0.8, text="the page text", level="mid")])
    (chunk,) = result.chunks
    assert isinstance(chunk, KnowledgeChunk)
    assert chunk.text == "the page text"
    assert chunk.similarity == pytest.approx(0.8)
    assert chunk.metadata["level"] == "mid"


def test_results_come_best_match_first_without_mmr(store):
    rows = [
        row("a:0000", 0.5, doc="a"),
        row("b:0000", 0.9, doc="b"),
        row("c:0000", 0.7, doc="c"),
    ]
    assert ids(search(rows, mmr=False)) == ["b:0000", "c:0000", "a:0000"]


def test_top_k_limits_the_results(store):
    rows = [row(f"d{i}:0000", 0.9 - i * 0.01, doc=f"d{i}") for i in range(8)]
    assert len(search(rows, top_k=3).chunks) == 3


# ── search: variety and the per-page limit ───────────────────


def test_a_near_copy_is_passed_over_for_a_different_chunk(store):
    rows = [
        row("a:0000", 0.90, doc="a", embedding=[1.0, 0.0]),
        row("b:0000", 0.89, doc="b", embedding=[1.0, 0.01]),
        row("c:0000", 0.60, doc="c", embedding=[0.0, 1.0]),
    ]
    assert ids(search(rows, top_k=2)) == ["a:0000", "c:0000"]


def test_without_mmr_the_near_copy_is_kept(store):
    rows = [
        row("a:0000", 0.90, doc="a", embedding=[1.0, 0.0]),
        row("b:0000", 0.89, doc="b", embedding=[1.0, 0.01]),
        row("c:0000", 0.60, doc="c", embedding=[0.0, 1.0]),
    ]
    assert ids(search(rows, top_k=2, mmr=False)) == ["a:0000", "b:0000"]


def test_the_variety_setting_can_be_changed(store):
    rows = [
        row("a:0000", 0.90, doc="a", embedding=[1.0, 0.0]),
        row("b:0000", 0.89, doc="b", embedding=[1.0, 0.01]),
        row("c:0000", 0.60, doc="c", embedding=[0.0, 1.0]),
    ]
    assert ids(search(rows, top_k=2, mmr_lambda=1.0)) == ["a:0000", "b:0000"]


def test_missing_embeddings_fall_back_to_best_first(store):
    rows = [
        row("a:0000", 0.7, doc="a", embedding=[1.0, 0.0]),
        row("b:0000", 0.9, doc="b"),  # no embedding
    ]
    assert ids(search(rows, top_k=2)) == ["b:0000", "a:0000"]


def test_one_page_supplies_at_most_two_chunks_by_default(store):
    rows = [row(f"p:{i:04d}", 0.9 - i * 0.01, doc="p") for i in range(4)]
    rows.append(row("q:0000", 0.5, doc="q"))
    assert ids(search(rows, top_k=5)) == ["p:0000", "p:0001", "q:0000"]


def test_the_per_page_limit_can_be_changed(store):
    rows = [row(f"p:{i:04d}", 0.9 - i * 0.01, doc="p") for i in range(4)]
    assert len(search(rows, top_k=5, max_per_doc=3).chunks) == 3


@pytest.mark.parametrize("off", [None, 0])
def test_the_per_page_limit_can_be_switched_off(store, off):
    rows = [row(f"p:{i:04d}", 0.9 - i * 0.01, doc="p") for i in range(4)]
    assert len(search(rows, top_k=5, max_per_doc=off).chunks) == 4


def test_the_page_comes_from_the_chunk_id_when_metadata_has_no_doc_id(store):
    rows = []
    for i in range(3):
        r = row(f"abcd:{i:04d}", 0.9 - i * 0.01)
        r["metadata"].pop("doc_id")
        rows.append(r)
    assert len(search(rows, top_k=5).chunks) == 2


# ── citations ────────────────────────────────────────────────


def test_a_chunk_knows_its_source_licence_and_credit_line(store):
    (chunk,) = search([row("a:0000", 0.8, doc="a")]).chunks
    assert chunk.citation == {
        "title": "Page a",
        "url": "https://example.com/a",
        "licence": "MIT",
        "attribution": "Credit for a",
    }
    assert chunk.url == "https://example.com/a"


def test_the_result_lists_each_page_once_in_order(store):
    rows = [
        row("a:0000", 0.9, doc="a"),
        row("b:0000", 0.8, doc="b"),
        row("a:0001", 0.7, doc="a"),
    ]
    result = search(rows, mmr=False)
    assert [c["url"] for c in result.citations] == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_a_chunk_without_a_url_still_gets_a_citation(store):
    r = row("a:0000", 0.8)
    r["metadata"].pop("url")
    result = search([r])
    assert result.citations == [
        {
            "title": "Page d1",
            "url": "",
            "licence": "MIT",
            "attribution": "Credit for d1",
        }
    ]


def test_an_empty_result_has_no_citations():
    assert SearchResult(collection=INTERVIEW_COLLECTION).citations == []


# ── monitoring ───────────────────────────────────────────────


def test_each_search_logs_its_counts_and_time(store, caplog):
    rows = [row("a:0000", 0.9, doc="a"), row("b:0000", 0.1, doc="b")]
    with caplog.at_level(logging.INFO, logger="app.rag.retriever"):
        search(rows)
    message = next(
        r.getMessage() for r in caplog.records if "search collection" in r.getMessage()
    )
    assert "collection=interview_knowledge" in message
    assert "candidates=2" in message
    assert "below_cutoff=1" in message
    assert "returned=1" in message
    assert "ms=" in message
