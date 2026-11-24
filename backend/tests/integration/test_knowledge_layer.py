"""Integration tests for the knowledge layer against the real ChromaDB.

What the unit tests only show with fakes, these show on the real thing: the real HTTP
client, the real filters (`$and`, `$in`, `$gt`, `$lte`), the real deletes and the real
embedding model. No Groq key is needed.

Two parts:

- Most tests use the `store` fixture, which sends everything to throw-away collections
  (see conftest.py). They ingest a small corpus and check idempotency, the licence gate,
  expiry, the retriever filters and the fallback routes. The real collections are never
  written.
- `TestRealKnowledgeBase` reads the real `interview_knowledge` and `learning_knowledge`
  and checks their contents. It skips when they are empty.

Run (ChromaDB must be up; the first run loads the embedding model):

    pytest tests/integration/test_knowledge_layer.py -m integration --no-cov

Settings that depend on the model (the 0.30 cutoff) are not asserted. Tests pass
`min_similarity` explicitly (0.0 for "let everything through", 0.999 for "nothing is
that close") so a result never depends on how this model scores two texts.
"""

from datetime import timedelta

import pytest

from app.ingest import pipeline as pl
from app.ingest.expiry import find_expired, remove_expired
from app.ingest.schema import (
    INTERVIEW_COLLECTION,
    INTERVIEW_CONTENT_TYPES,
    LEARNING_COLLECTION,
    LEARNING_CONTENT_TYPES,
    SCHEMA_VERSION,
    chunk_metadata,
    make_chunk_id,
)
from app.rag.fallback import (
    GENERAL_KNOWLEDGE_USER_NOTE,
    REASON_EMPTY_QUERY,
    REASON_NO_MATCH,
    REASON_RETRIEVAL_ERROR,
    REASON_WEAK_MATCH,
    ROUTE_GENERAL,
    ROUTE_KNOWLEDGE,
    retrieve_with_fallback,
)
from app.rag.retriever import RAGRetriever
from tests.integration._helpers import make_doc, utcnow

pytestmark = pytest.mark.integration

BASE = "https://example.com/it14"
STAR_QUERY = "how do I answer behavioral questions with the STAR method"


# ── a small corpus ───────────────────────────────────────────


def corpus():
    """Four interview pages that differ in domain, role family, level and type."""
    return [
        make_doc(
            url=f"{BASE}/behavioral",
            title="Behavioral interview questions",
            content_type="behavioral_question",
            text=(
                "Behavioral interview questions ask about past work. Use the STAR "
                "method: describe the situation, the task, the action you took and "
                "the result. Tell a short story about a project you led."
            ),
        ),
        make_doc(
            url=f"{BASE}/system-design",
            title="System design interview",
            content_type="concept_note",
            role_family="technical",
            level="senior",
            text=(
                "System design interview: design a scalable service with a load "
                "balancer, caching, database sharding and message queues. Discuss "
                "trade-offs and bottlenecks."
            ),
        ),
        make_doc(
            url=f"{BASE}/sales",
            title="Sales interview",
            domain="sales",
            content_type="experience",
            role_family="non_technical",
            level="entry",
            text=(
                "Sales interview: handling customer objections, closing a deal, "
                "negotiating price and building a pipeline of leads."
            ),
        ),
        make_doc(
            url=f"{BASE}/general",
            title="Interview preparation for any role",
            domain="general",
            content_type="company_pattern",
            text=(
                "Interview preparation for any role: research the company, practise "
                "out loud, prepare questions to ask and arrive early."
            ),
        ),
    ]


def learning_corpus():
    return [
        make_doc(
            url=f"{BASE}/docker",
            title="Docker documentation",
            domain="devops",
            content_type="docs",
            role_family="technical",
            text=(
                "Docker documentation: containers, images, a Dockerfile, volumes and "
                "networking. Learn to build and run containers."
            ),
        ),
        make_doc(
            url=f"{BASE}/cooking",
            title="Pasta cooking course",
            domain="cooking",
            content_type="course",
            text=(
                "A cooking course on pasta: boil water, salt it, cook the noodles "
                "and make a tomato sauce."
            ),
        ),
    ]


def load(pipeline, docs):
    summary = pipeline.ingest_many(docs)
    assert summary.ok, [r for r in summary.results if r.status == pl.FAILED]
    return summary


def search(query, collection=INTERVIEW_COLLECTION, **options):
    options.setdefault("top_k", 10)
    options.setdefault("min_similarity", 0.0)
    options.setdefault("mmr", False)
    return RAGRetriever().search(query, collection, **options)


def urls_of(result):
    return {chunk.url for chunk in result.chunks}


def at(name):
    return f"{BASE}/{name}"


# ── ingestion on the real store ──────────────────────────────


class TestIngest:
    def test_new_documents_are_added_with_their_credit_and_dates(self, store, pipeline):
        summary = load(pipeline, corpus())
        assert summary.counts == {pl.ADDED: 4}

        rows = store.get_by_where(
            INTERVIEW_COLLECTION, {"schema_version": SCHEMA_VERSION}
        )
        assert len(rows) == store.count(INTERVIEW_COLLECTION) >= 4
        for row in rows:
            meta = row["metadata"]
            assert meta["licence"] == "MIT"
            assert meta["attribution"] and meta["content_hash"]
            assert meta["expires_at_ts"] > meta["fetched_at_ts"]
            assert meta["source_id"] == "it14-source"
        assert store.count(LEARNING_COLLECTION) == 0

    def test_the_same_documents_again_change_nothing(self, store, pipeline):
        load(pipeline, corpus())
        before = store.count(INTERVIEW_COLLECTION)
        again = load(pipeline, corpus())
        assert again.counts == {pl.UNCHANGED: 4}
        assert all(r.chunks_written == 0 for r in again.results)
        assert store.count(INTERVIEW_COLLECTION) == before

    def test_changed_text_replaces_the_old_chunks(self, store, pipeline):
        url = at("changing")
        long_doc = make_doc(url=url, text="Practise one STAR story at a time. " * 60)
        load(pipeline, [long_doc])
        old = store.get_by_where(INTERVIEW_COLLECTION, {"doc_id": long_doc.doc_id})
        assert len(old) >= 3

        short_doc = make_doc(url=url, text="A much shorter page about the STAR method.")
        (result,) = load(pipeline, [short_doc]).results
        assert result.status == pl.UPDATED
        assert result.chunks_deleted == len(old) - 1

        now = store.get_by_where(INTERVIEW_COLLECTION, {"doc_id": long_doc.doc_id})
        assert [row["id"] for row in now] == [make_chunk_id(long_doc.doc_id, 0)]

    def test_a_document_moves_when_its_content_type_changes(self, store, pipeline):
        url = at("moving")
        first = make_doc(url=url, content_type="experience")
        load(pipeline, [first])
        assert store.get_by_where(INTERVIEW_COLLECTION, {"doc_id": first.doc_id})

        moved = make_doc(url=url, content_type="docs")
        load(pipeline, [moved])
        assert store.get_by_where(LEARNING_COLLECTION, {"doc_id": first.doc_id})
        assert store.get_by_where(INTERVIEW_COLLECTION, {"doc_id": first.doc_id}) == []

    @pytest.mark.parametrize("licence", ["CC-BY-SA-4.0", "unknown", "GPL-3.0"])
    def test_a_refused_licence_writes_nothing(self, store, pipeline, licence):
        (result,) = pipeline.ingest_many(
            [make_doc(url=at("refused"), licence=licence)]
        ).results
        assert result.status == pl.REFUSED
        assert result.reason
        assert store.count(INTERVIEW_COLLECTION) == 0
        assert store.count(LEARNING_COLLECTION) == 0

    def test_a_link_only_page_is_one_short_chunk(self, store, pipeline):
        doc = make_doc(
            url=at("link-only"),
            content_type="docs",
            ingest_mode="link_only",
            licence="proprietary",
            text="Our own one-line description of an official guide.",
        )
        (result,) = load(pipeline, [doc]).results
        assert result.status == pl.ADDED and result.chunks_written == 1
        rows = store.get_by_where(LEARNING_COLLECTION, {"doc_id": doc.doc_id})
        assert len(rows) == 1
        assert rows[0]["metadata"]["ingest_mode"] == "link_only"


# ── expiry on the real store ─────────────────────────────────


class TestExpiry:
    @staticmethod
    def expired(source, name, now):
        return make_doc(
            source_id=source,
            url=at(name),
            fetched_at=now - timedelta(days=100),
            expires_at=now - timedelta(days=10),
        )

    def test_the_sweep_removes_only_expired_chunks_of_the_named_source(
        self, store, pipeline
    ):
        now = utcnow()
        old = self.expired("it14-old", "old", now)
        other = self.expired("it14-other", "other", now)
        fresh = make_doc(source_id="it14-old", url=at("fresh"))
        load(pipeline, [old, other, fresh])
        total = store.count(INTERVIEW_COLLECTION)

        found = find_expired(now=now, source_ids=["it14-old"], store=store)
        assert [d.url for d in found] == [old.url]

        dry = remove_expired(
            now=now, source_ids=["it14-old"], dry_run=True, store=store
        )
        assert dry.dry_run and store.count(INTERVIEW_COLLECTION) == total

        report = remove_expired(now=now, source_ids=["it14-old"], store=store)
        assert report.chunks_deleted == len(found[0].chunk_ids) >= 1
        assert store.get_by_where(INTERVIEW_COLLECTION, {"doc_id": old.doc_id}) == []
        assert store.get_by_where(INTERVIEW_COLLECTION, {"doc_id": fresh.doc_id})
        assert store.get_by_where(INTERVIEW_COLLECTION, {"doc_id": other.doc_id})

        remove_expired(now=now, store=store)  # every source this time
        assert store.get_by_where(INTERVIEW_COLLECTION, {"doc_id": other.doc_id}) == []
        assert store.get_by_where(INTERVIEW_COLLECTION, {"doc_id": fresh.doc_id})

    def test_an_expired_chunk_is_never_returned_even_before_the_sweep(
        self, store, pipeline
    ):
        now = utcnow()
        old = self.expired("it14-old", "old-page", now)
        old = old.model_copy(
            update={"text": "The STAR method for behavioral questions."}
        )
        fresh = make_doc(
            url=at("fresh-page"), text="The STAR method for behavioral questions."
        )
        load(pipeline, [old, fresh])
        assert urls_of(search(STAR_QUERY, now=now)) == {fresh.url}

    def test_a_chunk_from_an_old_schema_is_never_returned(
        self, store, pipeline, embedder
    ):
        doc = make_doc(
            url=at("old-schema"), text="The STAR method for behavioral questions."
        )
        stale = chunk_metadata(
            doc.model_copy(update={"content_hash": "old"}), 0, embedding_model="test"
        )
        stale["schema_version"] = SCHEMA_VERSION - 1
        store.upsert_documents(
            INTERVIEW_COLLECTION,
            [doc.text],
            [embedder.embed_text(doc.text)],
            [stale],
            [make_chunk_id(doc.doc_id, 0)],
        )
        current = make_doc(
            url=at("current"), text="The STAR method for behavioral questions."
        )
        load(pipeline, [current])
        assert urls_of(search(STAR_QUERY)) == {current.url}


# ── retriever filters on the real store ──────────────────────

B, S, SALES, G = (at(n) for n in ("behavioral", "system-design", "sales", "general"))


class TestRetrieverFilters:
    @pytest.fixture(autouse=True)
    def _corpus(self, store, pipeline):
        load(pipeline, corpus())
        load(pipeline, learning_corpus())

    def test_without_filters_every_interview_page_comes_back(self):
        assert urls_of(search("interview")) == {B, S, SALES, G}

    def test_the_two_collections_are_kept_apart(self):
        assert urls_of(search("containers", LEARNING_COLLECTION)) == {
            at("docker"),
            at("cooking"),
        }
        assert urls_of(search("containers")) <= {B, S, SALES, G}

    @pytest.mark.parametrize(
        ("filters", "expected"),
        [
            ({"domain": "sales"}, {SALES, G}),  # "general" content fits any domain
            ({"domain": "software-engineering"}, {B, S, G}),
            ({"role_family": "technical"}, {B, S, G}),  # "all" fits any role family
            ({"role_family": "non_technical"}, {B, SALES, G}),
            ({"level": "senior"}, {B, S, G}),  # "all" fits any level
            ({"level": "entry"}, {B, SALES, G}),
            ({"content_type": "experience"}, {SALES}),
            ({"content_type": ["experience", "concept_note"]}, {SALES, S}),  # $in
            ({"content_type": "company_pattern", "domain": "sales"}, {G}),
            (
                {
                    "domain": "sales",
                    "role_family": "non_technical",
                    "level": "entry",
                    "content_type": "experience",
                },
                {SALES},
            ),
        ],
    )
    def test_each_filter_selects_the_right_pages(self, filters, expected):
        assert urls_of(search("interview", **filters)) == expected

    def test_filters_that_match_nothing_give_an_empty_result_not_an_error(self):
        result = search("interview", content_type="concept_note", domain="sales")
        assert result.empty and result.candidates == 0


# ── ranking, cutoff, variety and citations ───────────────────


class TestRanking:
    @pytest.fixture(autouse=True)
    def _corpus(self, store, pipeline):
        load(pipeline, corpus())

    def test_the_best_match_comes_first(self):
        assert search(STAR_QUERY, mmr=False).chunks[0].url == B
        assert search(STAR_QUERY, mmr=True, top_k=3).chunks[0].url == B

    def test_the_cutoff_separates_weak_matches_and_says_how_many(self):
        loose = search(STAR_QUERY, min_similarity=0.0)
        assert loose.chunks and loose.below_cutoff == 0
        strict = search(STAR_QUERY, min_similarity=0.999)
        assert strict.empty
        assert strict.candidates == loose.candidates > 0
        assert strict.below_cutoff == strict.candidates

    def test_citations_name_each_page_once_with_its_licence(self):
        result = search(STAR_QUERY)
        citations = result.citations
        assert [c["url"] for c in citations] == list(
            dict.fromkeys(c.url for c in result.chunks)
        )
        for citation in citations:
            assert citation["title"] and citation["url"].startswith("https://")
            assert citation["licence"] == "MIT"
            assert citation["attribution"]

    def test_one_page_gives_at_most_two_chunks_by_default(self, pipeline):
        long_doc = make_doc(
            url=at("long"), text="Practise the STAR method every day. " * 60
        )
        load(pipeline, [long_doc])
        query = "Practise the STAR method every day."
        capped = search(query, top_k=5)
        assert sum(c.url == long_doc.url for c in capped.chunks) == 2
        open_ = search(query, top_k=5, max_per_doc=None)
        assert sum(c.url == long_doc.url for c in open_.chunks) >= 3


# ── the fallback on the real store ───────────────────────────


class TestFallback:
    @pytest.fixture(autouse=True)
    def _corpus(self, store, pipeline):
        load(pipeline, corpus())

    def test_a_match_is_answered_from_the_knowledge_base(self):
        found = retrieve_with_fallback(
            STAR_QUERY, INTERVIEW_COLLECTION, min_similarity=0.0
        )
        assert found.route == ROUTE_KNOWLEDGE and found.grounded
        assert found.citations and found.user_note == "" and found.prompt_note == ""

    def test_filters_with_no_candidates_say_no_match(self):
        found = retrieve_with_fallback(
            STAR_QUERY,
            INTERVIEW_COLLECTION,
            content_type="concept_note",
            domain="sales",
        )
        self.assert_general(found, REASON_NO_MATCH)

    def test_matches_that_are_all_too_weak_say_weak_match(self):
        found = retrieve_with_fallback(
            STAR_QUERY, INTERVIEW_COLLECTION, min_similarity=0.999
        )
        self.assert_general(found, REASON_WEAK_MATCH)

    def test_an_empty_query_says_empty_query(self):
        self.assert_general(
            retrieve_with_fallback("   ", INTERVIEW_COLLECTION), REASON_EMPTY_QUERY
        )

    def test_a_failing_store_falls_back_instead_of_raising(self, chroma, store):
        chroma.delete_collection(store.name_of(INTERVIEW_COLLECTION))
        found = retrieve_with_fallback(STAR_QUERY, INTERVIEW_COLLECTION)
        self.assert_general(found, REASON_RETRIEVAL_ERROR)
        assert found.result is None

    def test_a_bad_filter_is_a_bug_in_the_caller_and_still_raises(self):
        with pytest.raises(ValueError):
            retrieve_with_fallback(STAR_QUERY, INTERVIEW_COLLECTION, level="wizard")

    @staticmethod
    def assert_general(found, reason):
        assert found.route == ROUTE_GENERAL and not found.grounded
        assert found.reason == reason
        assert found.chunks == [] and found.citations == []
        assert found.user_note == GENERAL_KNOWLEDGE_USER_NOTE
        assert found.prompt_note


# ── the real collections (read only) ─────────────────────────

REAL_TYPES = {
    INTERVIEW_COLLECTION: set(INTERVIEW_CONTENT_TYPES),
    LEARNING_COLLECTION: set(LEARNING_CONTENT_TYPES),
}
REQUIRED = (
    "doc_id",
    "source_id",
    "url",
    "title",
    "domain",
    "role_family",
    "level",
    "content_type",
    "ingest_mode",
    "licence",
    "attribution",
    "language",
    "content_hash",
)


class TestRealKnowledgeBase:
    """Looks at what is really stored. Never writes. Skips when a collection is empty."""

    @staticmethod
    def metadatas(chroma, collection):
        rows = (
            chroma.get_client()
            .get_collection(name=collection)
            .get(include=["metadatas"])
        )
        return list(zip(rows["ids"], rows["metadatas"]))

    @pytest.fixture(params=sorted(REAL_TYPES))
    def collection(self, request, chroma, real_interview_urls, real_learning_urls):
        return request.param

    def test_every_chunk_has_its_credit_and_dates(self, chroma, collection):
        bad = []
        for chunk_id, meta in self.metadatas(chroma, collection):
            missing = [k for k in REQUIRED if not str(meta.get(k, "")).strip()]
            fetched, expires = meta.get("fetched_at_ts"), meta.get("expires_at_ts")
            if (
                missing
                or meta.get("schema_version") != SCHEMA_VERSION
                or not str(meta["url"]).startswith(("http://", "https://"))
                or not isinstance(fetched, int)
                or not isinstance(expires, int)
                or expires <= fetched
            ):
                bad.append((chunk_id, missing))
        assert not bad, f"{len(bad)} chunk(s) in {collection} are incomplete: {bad[:5]}"

    def test_each_collection_holds_only_its_own_content_types(self, chroma, collection):
        wrong = [
            chunk_id
            for chunk_id, meta in self.metadatas(chroma, collection)
            if meta.get("content_type") not in REAL_TYPES[collection]
        ]
        assert not wrong, f"chunks of the wrong type in {collection}: {wrong[:5]}"

    @pytest.mark.parametrize(
        ("collection", "query"),
        [
            (INTERVIEW_COLLECTION, "how do I answer behavioral interview questions"),
            (LEARNING_COLLECTION, "learning resources for Kubernetes"),
        ],
    )
    def test_a_real_search_returns_cited_pages(
        self, collection, query, embedder, real_interview_urls, real_learning_urls
    ):
        known = (
            real_interview_urls
            if collection == INTERVIEW_COLLECTION
            else real_learning_urls
        )
        found = retrieve_with_fallback(query, collection)
        assert found.grounded, f"nothing found for {query!r}: {found.reason}"
        for chunk in found.chunks:
            assert chunk.similarity >= 0.30
        for citation in found.citations:
            assert citation["url"] in known
            assert citation["url"].startswith(("http://", "https://"))
            assert citation["licence"] and citation["attribution"]

    def test_the_filters_run_on_the_real_server_and_the_results_obey_them(
        self, embedder, real_interview_urls
    ):
        result = RAGRetriever().search(
            "interview preparation",
            INTERVIEW_COLLECTION,
            top_k=10,
            min_similarity=0.0,
            domain="software-engineering",
            role_family="technical",
            level="mid",
            content_type=["interview_question", "experience"],
        )
        for chunk in result.chunks:
            meta = chunk.metadata
            assert meta["domain"] in ("software-engineering", "general")
            assert meta["role_family"] in ("technical", "all")
            assert meta["level"] in ("mid", "all")
            assert meta["content_type"] in ("interview_question", "experience")

    def test_the_expiry_filter_runs_on_the_real_server_and_changes_nothing(
        self, chroma, real_interview_urls, real_learning_urls
    ):
        counts = {c: chroma.count(c) for c in REAL_TYPES}
        now = utcnow()
        everything = remove_expired(now=now, dry_run=True)
        one_source = remove_expired(
            now=now, source_ids=["tech-interview-handbook"], dry_run=True
        )
        assert everything.dry_run and one_source.dry_run
        assert {c: chroma.count(c) for c in REAL_TYPES} == counts
