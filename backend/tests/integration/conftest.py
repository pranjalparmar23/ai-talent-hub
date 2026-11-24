"""Fixtures for the integration tests: the real ChromaDB and the real embedding model.

Integration tests are skipped unless you ask for them with `-m integration`, so a plain
`pytest` never starts them by accident.

The knowledge-layer tests never write to `interview_knowledge` or `learning_knowledge`.
The `store` fixture sends every call to throw-away collections named
`it14_<random>_<name>`, and drops them when the test ends. A collection name that is not
one of the two knowledge collections is refused outright. Collections left behind by a
crashed run (names starting with `it14_`) are removed at the start and end of a session.

Tests that look at the real collections (`TestRealKnowledgeBase`) only read.
"""

import logging
import uuid

import pytest

from app.ingest.schema import INTERVIEW_COLLECTION, LEARNING_COLLECTION
from app.rag import retriever as retriever_module
from app.rag.collections import CollectionSpec
from app.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

PREFIX = "it14_"
KNOWLEDGE_NAMES = (INTERVIEW_COLLECTION, LEARNING_COLLECTION)


# ── opt-in ───────────────────────────────────────────────────


def pytest_collection_modifyitems(config, items):
    """Skip every integration test unless `-m` asks for the integration marker."""
    if "integration" in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason="integration test: run with `pytest -m integration`")
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip)


# ── a store that cannot touch the real collections ───────────


class IsolatedStore:
    """The VectorStore calls the pipeline, the expiry sweep and the retriever make.

    Every collection name is given a private prefix first, so nothing reaches the real
    collections. Anything but the two knowledge collection names is refused.
    """

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def name_of(self, collection_name: str) -> str:
        if collection_name not in KNOWLEDGE_NAMES:
            raise AssertionError(f"integration tests only use {KNOWLEDGE_NAMES}")
        return f"{self.prefix}{collection_name}"

    def get_by_where(self, collection_name, where):
        return VectorStore.get_by_where(self.name_of(collection_name), where)

    def upsert_documents(self, collection_name, documents, embeddings, metadatas, ids):
        return VectorStore.upsert_documents(
            self.name_of(collection_name), documents, embeddings, metadatas, ids
        )

    def update_metadatas(self, collection_name, ids, metadatas):
        return VectorStore.update_metadatas(
            self.name_of(collection_name), ids, metadatas
        )

    def delete_ids(self, collection_name, ids):
        return VectorStore.delete_ids(self.name_of(collection_name), ids)

    def query(
        self,
        collection_name,
        query_embedding,
        top_k=5,
        where=None,
        include_embeddings=False,
    ):
        return VectorStore.query(
            self.name_of(collection_name),
            query_embedding,
            top_k=top_k,
            where=where,
            include_embeddings=include_embeddings,
        )

    def count(self, collection_name):
        return VectorStore.count(self.name_of(collection_name))


# ── fixtures ─────────────────────────────────────────────────


def _purge_leftovers() -> None:
    for name in VectorStore.list_collections():
        if name.startswith(PREFIX):
            try:
                VectorStore.delete_collection(name)
            except Exception:  # best effort: never fail a run over clean-up
                logger.warning("could not remove leftover collection %s", name)


@pytest.fixture(scope="session")
def chroma():
    """The real VectorStore, or a skip when ChromaDB cannot be reached."""
    try:
        VectorStore.heartbeat()
        _purge_leftovers()
    except Exception as exc:
        pytest.skip(
            f"ChromaDB is not reachable ({type(exc).__name__}). Start it and check "
            "CHROMA_HOST and CHROMA_PORT."
        )
    yield VectorStore
    _purge_leftovers()


@pytest.fixture(scope="session")
def embedder(chroma):
    """The real embedding service, or a skip when the model cannot be loaded."""
    from app.rag.embeddings import EmbeddingService

    try:
        EmbeddingService.embed_text("warm up")
    except Exception as exc:
        pytest.skip(
            f"the embedding model cannot be loaded ({type(exc).__name__}: {exc})"
        )
    return EmbeddingService


@pytest.fixture
def store(chroma, embedder, monkeypatch):
    """Throw-away copies of the two knowledge collections, used by retriever and pipeline."""
    prefix = f"{PREFIX}{uuid.uuid4().hex[:8]}_"
    isolated = IsolatedStore(prefix)
    for name in KNOWLEDGE_NAMES:
        chroma.get_or_create_collection(
            CollectionSpec(
                name=isolated.name_of(name),
                description="integration test, safe to delete",
            )
        )
    monkeypatch.setattr(retriever_module, "VectorStore", isolated)
    yield isolated
    for name in KNOWLEDGE_NAMES:
        try:
            chroma.delete_collection(isolated.name_of(name))
        except Exception:  # a test may already have dropped it
            logger.warning("collection %s was already gone", isolated.name_of(name))


@pytest.fixture
def pipeline(store):
    """The real ingest pipeline (real embeddings), writing to the throw-away collections."""
    from app.ingest.pipeline import IngestPipeline

    return IngestPipeline(store=store)


def _real_urls(chroma, collection: str) -> set[str]:
    from tests.integration._helpers import stored_urls

    urls = stored_urls(chroma, collection)
    if not urls:
        pytest.skip(f"{collection} is empty. Run `python -m app.ingest.run` first.")
    return urls


@pytest.fixture(scope="session")
def real_interview_urls(chroma) -> set[str]:
    """Every page URL stored in the real interview_knowledge (skips when empty)."""
    return _real_urls(chroma, INTERVIEW_COLLECTION)


@pytest.fixture(scope="session")
def real_learning_urls(chroma) -> set[str]:
    """Every page URL stored in the real learning_knowledge (skips when empty)."""
    return _real_urls(chroma, LEARNING_COLLECTION)
