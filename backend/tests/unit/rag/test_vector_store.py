"""Tests for VectorStore.query (app/rag/vector_store.py).

ChromaDB is replaced by a fake collection that records what it was asked and
returns the shape ChromaDB returns, so no database is needed. The real filters and
the real `embeddings` option are proven against a running ChromaDB by the
integration tests (Phase 7, task 14).
"""

import numpy as np
import pytest

from app.rag.vector_store import VectorStore


class FakeCollection:
    """Records query() calls and returns a scripted ChromaDB-shaped answer."""

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[dict] = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.answer


def answer(embeddings="absent", metadatas=None):
    """A ChromaDB-style answer for one query with two hits."""
    out = {
        "ids": [["a:0000", "b:0000"]],
        "documents": [["text a", "text b"]],
        "metadatas": [metadatas or [{"url": "u1"}, {"url": "u2"}]],
        "distances": [[0.1, 0.4]],
    }
    if embeddings != "absent":
        out["embeddings"] = embeddings
    return out


@pytest.fixture
def collection(monkeypatch):
    fake = FakeCollection(answer())
    monkeypatch.setattr(
        VectorStore, "_get_collection_by_name", classmethod(lambda cls, name: fake)
    )
    return fake


def query(**kwargs):
    return VectorStore.query("interview_knowledge", [0.1, 0.2], **kwargs)


# ── the default call is unchanged ────────────────────────────


def test_a_normal_query_asks_for_text_metadata_and_distance_only(collection):
    query()
    (call,) = collection.calls
    assert call["include"] == ["documents", "metadatas", "distances"]


def test_a_normal_query_returns_rows_without_embeddings(collection):
    rows = query()
    assert rows == [
        {
            "id": "a:0000",
            "document": "text a",
            "metadata": {"url": "u1"},
            "distance": 0.1,
        },
        {
            "id": "b:0000",
            "document": "text b",
            "metadata": {"url": "u2"},
            "distance": 0.4,
        },
    ]


def test_the_vector_top_k_and_filter_are_passed_to_chroma(collection):
    where = {"level": "mid"}
    query(top_k=7, where=where)
    (call,) = collection.calls
    assert call["query_embeddings"] == [[0.1, 0.2]]
    assert call["n_results"] == 7
    assert call["where"] == where


def test_top_k_defaults_to_five_and_where_to_none(collection):
    query()
    assert collection.calls[0]["n_results"] == 5
    assert collection.calls[0]["where"] is None


# ── include_embeddings ───────────────────────────────────────


def test_embeddings_are_requested_only_when_asked_for(collection):
    collection.answer = answer(embeddings=[[[1.0, 0.0], [0.0, 1.0]]])
    query(include_embeddings=True)
    assert collection.calls[0]["include"] == [
        "documents",
        "metadatas",
        "distances",
        "embeddings",
    ]


def test_each_row_gets_its_own_embedding(collection):
    collection.answer = answer(embeddings=[[[1.0, 0.0], [0.0, 1.0]]])
    rows = query(include_embeddings=True)
    assert [r["embedding"] for r in rows] == [[1.0, 0.0], [0.0, 1.0]]


def test_numpy_embeddings_become_plain_float_lists(collection):
    # ChromaDB hands back numpy arrays; the rest of the code wants plain lists.
    vectors = np.array([[0.25, 0.5], [0.75, 1.0]], dtype=np.float32)
    collection.answer = answer(embeddings=[vectors])
    rows = query(include_embeddings=True)
    for r, expected in zip(rows, [[0.25, 0.5], [0.75, 1.0]]):
        assert r["embedding"] == expected
        assert type(r["embedding"]) is list
        assert all(type(x) is float for x in r["embedding"])


def test_the_other_row_fields_are_unchanged_when_embeddings_are_added(collection):
    collection.answer = answer(embeddings=[[[1.0, 0.0], [0.0, 1.0]]])
    rows = query(include_embeddings=True)
    assert rows[0]["id"] == "a:0000"
    assert rows[0]["document"] == "text a"
    assert rows[0]["metadata"] == {"url": "u1"}
    assert rows[0]["distance"] == 0.1


def test_rows_have_no_embedding_when_chroma_returns_none(collection):
    collection.answer = answer(embeddings=None)
    rows = query(include_embeddings=True)
    assert all("embedding" not in r for r in rows)


def test_rows_have_no_embedding_when_the_key_is_missing(collection):
    rows = query(include_embeddings=True)
    assert len(rows) == 2
    assert all("embedding" not in r for r in rows)


def test_embeddings_are_not_added_when_not_requested_even_if_chroma_sends_them(
    collection,
):
    collection.answer = answer(embeddings=[[[1.0, 0.0], [0.0, 1.0]]])
    assert all("embedding" not in r for r in query())


# ── odd answers ──────────────────────────────────────────────


def test_a_missing_metadata_becomes_an_empty_dict(collection):
    collection.answer = answer(metadatas=[None, {"url": "u2"}])
    rows = query()
    assert rows[0]["metadata"] == {}
    assert rows[1]["metadata"] == {"url": "u2"}


def test_no_hits_gives_an_empty_list(collection):
    collection.answer = {
        "ids": [[]],
        "documents": [[]],
        "metadatas": [[]],
        "distances": [[]],
        "embeddings": [],
    }
    assert query(include_embeddings=True) == []
