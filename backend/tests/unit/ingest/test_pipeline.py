"""Unit tests for the ingestion pipeline. A fake store and embedder: no ChromaDB."""

from datetime import datetime, timedelta, timezone

import pytest

from app.ingest import pipeline as pl
from app.ingest.schema import INTERVIEW_COLLECTION, LEARNING_COLLECTION, KnowledgeDoc
from app.rag.chunking import TextChunker

NOW = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
LONG_TEXT = "Tell a short story about a real project. " * 40  # several chunks


class FakeStore:
    """Keeps chunks in two dicts and remembers the order of write calls."""

    def __init__(self):
        self.collections = {INTERVIEW_COLLECTION: {}, LEARNING_COLLECTION: {}}
        self.calls = []

    def get_by_where(self, collection_name, where):
        rows = self.collections[collection_name]
        return [
            {"id": i, "metadata": m}
            for i, (_, m) in rows.items()
            if all(m.get(k) == v for k, v in where.items())
        ]

    def upsert_documents(self, collection_name, documents, embeddings, metadatas, ids):
        self.calls.append("upsert")
        for i, doc, meta in zip(ids, documents, metadatas):
            self.collections[collection_name][i] = (doc, meta)
        return ids

    def update_metadatas(self, collection_name, ids, metadatas):
        self.calls.append("update")
        for i, meta in zip(ids, metadatas):
            text = self.collections[collection_name][i][0]
            self.collections[collection_name][i] = (text, meta)
        return len(ids)

    def delete_ids(self, collection_name, ids):
        if ids:
            self.calls.append("delete")
        for i in ids:
            del self.collections[collection_name][i]
        return len(ids)


class FakeEmbedder:
    def __init__(self):
        self.texts_embedded = 0

    def embed_batch(self, texts):
        self.texts_embedded += len(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]


class BrokenEmbedder:
    def embed_batch(self, texts):
        raise RuntimeError("model is down")


def make_doc(**overrides):
    data = dict(
        source_id="tech-interview-handbook",
        url="https://www.techinterviewhandbook.org/behavioral-interview/",
        title="Behavioral interview guide",
        domain="software-engineering",
        content_type="experience",
        licence="MIT",
        attribution="Tech Interview Handbook, MIT licence",
        fetched_at=NOW,
        expires_at=NOW + timedelta(days=90),
        text=LONG_TEXT,
    )
    data.update(overrides)
    return KnowledgeDoc(**data)


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def pipe(store, embedder):
    return pl.IngestPipeline(
        store=store,
        embedder=embedder,
        chunker=TextChunker(chunk_size=500, chunk_overlap=50),
        embedding_model="test-model",
    )


def stored(store, collection=INTERVIEW_COLLECTION):
    return store.collections[collection]


# ── adding ───────────────────────────────────────────────────


def test_new_document_is_added_with_fixed_chunk_ids(pipe, store):
    doc = make_doc()
    result = pipe.ingest(doc)
    assert result.status == pl.ADDED
    assert result.chunks_written > 1
    assert sorted(stored(store)) == [
        f"{doc.doc_id}:{i:04d}" for i in range(result.chunks_written)
    ]


def test_stored_metadata_has_the_credit_and_the_hash(pipe, store):
    pipe.ingest(make_doc())
    _, meta = next(iter(stored(store).values()))
    assert meta["licence"] == "MIT"
    assert meta["attribution"] == "Tech Interview Handbook, MIT licence"
    assert meta["embedding_model"] == "test-model"
    assert len(meta["content_hash"]) == 64
    assert meta["schema_version"] == 2


def test_licence_is_stored_the_standard_way(pipe, store):
    pipe.ingest(make_doc(licence="mit"))
    assert {m["licence"] for _, m in stored(store).values()} == {"MIT"}


def test_learning_content_goes_to_the_learning_collection(pipe, store):
    pipe.ingest(make_doc(content_type="course"))
    assert stored(store, LEARNING_COLLECTION)
    assert not stored(store, INTERVIEW_COLLECTION)


def test_link_only_document_is_one_short_chunk(pipe, store):
    doc = make_doc(
        ingest_mode="link_only", licence="proprietary", text="Roadmap for backend"
    )
    result = pipe.ingest(doc)
    assert result.status == pl.ADDED
    assert result.chunks_written == 1
    ((text, meta),) = stored(store).values()
    assert text == "Roadmap for backend"
    assert meta["ingest_mode"] == "link_only"


def test_two_documents_do_not_touch_each_other(pipe, store):
    a = make_doc()
    b = make_doc(url="https://www.techinterviewhandbook.org/other/")
    pipe.ingest(a)
    count_a = len(stored(store))
    pipe.ingest(b)
    assert len(stored(store)) == count_a * 2
    assert (
        len(store.get_by_where(INTERVIEW_COLLECTION, {"doc_id": a.doc_id})) == count_a
    )


# ── running the same thing again ─────────────────────────────


def test_same_content_twice_changes_nothing_and_does_not_re_embed(
    pipe, store, embedder
):
    first = pipe.ingest(make_doc())
    embedded_after_first = embedder.texts_embedded
    ids_after_first = sorted(stored(store))

    second = pipe.ingest(make_doc())

    assert second.status == pl.UNCHANGED
    assert second.chunks_written == 0
    assert embedder.texts_embedded == embedded_after_first
    assert sorted(stored(store)) == ids_after_first
    assert len(ids_after_first) == first.chunks_written


def test_unchanged_content_still_gets_fresh_dates(pipe, store):
    pipe.ingest(make_doc())
    later = NOW + timedelta(days=30)
    pipe.ingest(make_doc(fetched_at=later, expires_at=later + timedelta(days=90)))
    expiry = {m["expires_at_ts"] for _, m in stored(store).values()}
    assert expiry == {int((later + timedelta(days=90)).timestamp())}


def test_a_new_embedding_model_re_embeds_everything(store, embedder):
    kwargs = dict(store=store, embedder=embedder, chunker=TextChunker(500, 50))
    pl.IngestPipeline(embedding_model="model-a", **kwargs).ingest(make_doc())
    before = embedder.texts_embedded
    result = pl.IngestPipeline(embedding_model="model-b", **kwargs).ingest(make_doc())
    assert result.status == pl.UPDATED
    assert embedder.texts_embedded == before * 2


# ── changed content ──────────────────────────────────────────


def test_shorter_text_is_updated_and_old_chunks_are_deleted(pipe, store):
    first = pipe.ingest(make_doc())
    result = pipe.ingest(make_doc(text="Just one short paragraph now."))
    assert result.status == pl.UPDATED
    assert result.chunks_written == 1
    assert result.chunks_deleted == first.chunks_written - 1
    assert len(stored(store)) == 1


def test_new_chunks_are_written_before_old_ones_are_deleted(pipe, store):
    pipe.ingest(make_doc())
    store.calls.clear()
    pipe.ingest(make_doc(text="Just one short paragraph now."))
    assert store.calls == ["upsert", "delete"]


def test_changed_text_gets_a_new_hash(pipe, store):
    pipe.ingest(make_doc())
    old = {m["content_hash"] for _, m in stored(store).values()}
    pipe.ingest(make_doc(text="Different words entirely."))
    new = {m["content_hash"] for _, m in stored(store).values()}
    assert len(old) == len(new) == 1 and old != new


# ── content type changes ─────────────────────────────────────


def test_changing_content_type_moves_the_document(pipe, store):
    first = pipe.ingest(make_doc(content_type="experience"))
    result = pipe.ingest(make_doc(content_type="course"))
    assert result.status == pl.ADDED
    assert result.chunks_deleted == first.chunks_written
    assert not stored(store, INTERVIEW_COLLECTION)
    assert len(stored(store, LEARNING_COLLECTION)) == first.chunks_written


def test_a_failed_move_keeps_the_old_copy(store):
    ok = pl.IngestPipeline(
        store=store,
        embedder=FakeEmbedder(),
        chunker=TextChunker(500, 50),
        embedding_model="test-model",
    )
    first = ok.ingest(make_doc(content_type="experience"))
    broken = pl.IngestPipeline(
        store=store,
        embedder=BrokenEmbedder(),
        chunker=TextChunker(500, 50),
        embedding_model="test-model",
    )
    with pytest.raises(RuntimeError):
        broken.ingest(make_doc(content_type="course"))
    assert len(stored(store, INTERVIEW_COLLECTION)) == first.chunks_written
    assert not stored(store, LEARNING_COLLECTION)


# ── refusals and empty text ──────────────────────────────────


def test_refused_licence_stores_nothing_and_does_not_embed(pipe, store, embedder):
    result = pipe.ingest(make_doc(licence="GPL-3.0-only"))
    assert result.status == pl.REFUSED
    assert "GPL-3.0-only" in result.reason
    assert not stored(store)
    assert embedder.texts_embedded == 0
    assert store.calls == []


def test_text_that_makes_no_chunks_is_skipped(store, embedder):
    class NoChunks:
        chunk_size = 500
        chunk_overlap = 50

        def chunk_text(self, text):
            return []

    pipe = pl.IngestPipeline(
        store=store, embedder=embedder, chunker=NoChunks(), embedding_model="m"
    )
    result = pipe.ingest(make_doc())
    assert result.status == pl.SKIPPED
    assert not stored(store)


# ── many documents ───────────────────────────────────────────


def test_ingest_many_counts_each_outcome(pipe):
    docs = [
        make_doc(url="https://x.test/1"),
        make_doc(url="https://x.test/2"),
        make_doc(url="https://x.test/3", licence="CC-BY-NC-4.0"),
    ]
    summary = pipe.ingest_many(docs)
    assert summary.counts == {pl.ADDED: 2, pl.REFUSED: 1}
    assert summary.ok is True  # a refusal is not a failure


def test_one_failure_does_not_stop_the_rest(store):
    class FailsOnce(FakeEmbedder):
        def embed_batch(self, texts):
            if not self.texts_embedded and not getattr(self, "tried", False):
                self.tried = True
                raise RuntimeError("first call fails")
            return super().embed_batch(texts)

    pipe = pl.IngestPipeline(
        store=store,
        embedder=FailsOnce(),
        chunker=TextChunker(500, 50),
        embedding_model="m",
    )
    summary = pipe.ingest_many(
        [make_doc(url="https://x.test/1"), make_doc(url="https://x.test/2")]
    )
    assert summary.counts == {pl.FAILED: 1, pl.ADDED: 1}
    assert summary.ok is False
    failed = summary.results[0]
    assert failed.status == pl.FAILED
    assert "RuntimeError: first call fails" in failed.reason


def test_empty_batch_is_fine(pipe):
    summary = pipe.ingest_many([])
    assert summary.counts == {}
    assert summary.ok is True
