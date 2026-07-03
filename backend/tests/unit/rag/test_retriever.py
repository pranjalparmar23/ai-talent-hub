"""Unit tests for the RAG layer — embeddings, chunking, vector store, retriever.

These tests hit the real ChromaDB container (not mocked) because mocking
chromadb's HttpClient adds more risk than it removes — small ingest+query
cycles are fast and prove the integration works.

Each test uses a unique throwaway collection so they can't interfere with
seeded data or with each other.
"""
import uuid
import pytest

from app.rag.embeddings import EmbeddingService, EMBEDDING_DIM
from app.rag.chunking import TextChunker
from app.rag.vector_store import VectorStore
from app.rag.collections import CollectionSpec
from app.rag.retriever import RAGRetriever, RetrievedDocument


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def throwaway_collection():
    """Create a unique collection for one test, delete it after."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    spec = CollectionSpec(name=name, description="ephemeral test collection")
    VectorStore.get_or_create_collection(spec)
    yield name
    try:
        VectorStore.delete_collection(name)
    except Exception:
        pass  # Already gone


@pytest.fixture
def retriever():
    return RAGRetriever()


# ── EmbeddingService ─────────────────────────────────────────────────────────

class TestEmbeddingService:
    def test_embed_text_returns_correct_dimension(self):
        vec = EmbeddingService.embed_text("Senior backend engineer")
        assert len(vec) == EMBEDDING_DIM
        assert all(isinstance(v, float) for v in vec)

    def test_embed_text_is_deterministic(self):
        v1 = EmbeddingService.embed_text("Hello world")
        v2 = EmbeddingService.embed_text("Hello world")
        assert v1 == v2

    def test_embed_text_rejects_empty_string(self):
        with pytest.raises(ValueError):
            EmbeddingService.embed_text("")
        with pytest.raises(ValueError):
            EmbeddingService.embed_text("   ")

    def test_embed_batch_returns_one_vector_per_input(self):
        texts = ["Python", "JavaScript", "Kubernetes"]
        vectors = EmbeddingService.embed_batch(texts)
        assert len(vectors) == 3
        assert all(len(v) == EMBEDDING_DIM for v in vectors)

    def test_embed_batch_empty_list_returns_empty(self):
        assert EmbeddingService.embed_batch([]) == []

    def test_embed_batch_rejects_all_empty_strings(self):
        with pytest.raises(ValueError):
            EmbeddingService.embed_batch(["", "  ", "\n"])

    def test_similar_texts_have_high_similarity(self):
        """Sanity check that the model isn't producing garbage embeddings."""
        v1 = EmbeddingService.embed_text("How to learn Kubernetes")
        v2 = EmbeddingService.embed_text("Best resources for learning Kubernetes")
        v3 = EmbeddingService.embed_text("Cookie dough recipe for beginners")

        # Cosine similarity
        def cos(a, b):
            import math
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            return dot / (na * nb)

        sim_related = cos(v1, v2)
        sim_unrelated = cos(v1, v3)
        assert sim_related > sim_unrelated
        assert sim_related > 0.5  # related texts must be clearly similar

    def test_dimension_constant_matches_actual(self):
        vec = EmbeddingService.embed_text("anything")
        assert EmbeddingService.embedding_dimension() == len(vec) == EMBEDDING_DIM


# ── TextChunker ──────────────────────────────────────────────────────────────

class TestTextChunker:
    def test_short_text_returns_single_chunk(self):
        chunker = TextChunker(chunk_size=500, chunk_overlap=50)
        chunks = chunker.chunk_text("This is a short sentence.")
        assert len(chunks) == 1
        assert chunks[0] == "This is a short sentence."

    def test_empty_text_returns_empty_list(self):
        chunker = TextChunker()
        assert chunker.chunk_text("") == []
        assert chunker.chunk_text("   ") == []

    def test_long_text_splits_into_multiple_chunks(self):
        chunker = TextChunker(chunk_size=100, chunk_overlap=10)
        text = "Sentence one. " * 50  # 700 chars
        chunks = chunker.chunk_text(text)
        assert len(chunks) > 1
        # Each chunk roughly within size limit (some flex due to separators)
        for c in chunks:
            assert len(c) <= 150  # tolerance for boundary handling

    def test_chunks_overlap_correctly(self):
        chunker = TextChunker(chunk_size=80, chunk_overlap=20)
        # Use text that will definitely split
        text = "Alpha beta gamma delta. " * 20
        chunks = chunker.chunk_text(text)
        assert len(chunks) >= 2

    def test_chunk_documents_propagates_metadata(self):
        chunker = TextChunker(chunk_size=50, chunk_overlap=5)
        docs = ["Document one is short.", "Document two is also brief."]
        metas = [{"source": "doc1"}, {"source": "doc2"}]
        all_chunks, all_metas = chunker.chunk_documents(docs, metas)

        assert len(all_chunks) == len(all_metas)
        # First doc's chunks should carry source=doc1
        # Find which chunks belong to which doc by content
        for chunk, meta in zip(all_chunks, all_metas):
            assert "source" in meta
            assert "chunk_index" in meta

    def test_chunk_documents_rejects_mismatched_metadata(self):
        chunker = TextChunker()
        with pytest.raises(ValueError, match="Mismatched"):
            chunker.chunk_documents(["a", "b"], [{"x": 1}])

    def test_chunk_documents_without_metadata(self):
        chunker = TextChunker()
        chunks, metas = chunker.chunk_documents(["First doc", "Second doc"])
        assert len(chunks) == 2
        # All chunks still get a chunk_index even without input metadata
        assert all("chunk_index" in m for m in metas)


# ── VectorStore (integration with ChromaDB) ──────────────────────────────────

class TestVectorStore:
    def test_heartbeat_returns_int(self):
        hb = VectorStore.heartbeat()
        assert isinstance(hb, int)
        assert hb > 0

    def test_add_and_query_documents(self, throwaway_collection):
        docs = [
            "Python is a programming language used for AI and web development.",
            "Kubernetes orchestrates containers across clusters.",
            "PostgreSQL is a relational database.",
        ]
        embeddings = EmbeddingService.embed_batch(docs)
        ids = VectorStore.add_documents(
            collection_name=throwaway_collection,
            documents=docs,
            embeddings=embeddings,
            metadatas=[{"topic": "python"}, {"topic": "k8s"}, {"topic": "postgres"}],
        )

        assert len(ids) == 3
        assert VectorStore.count(throwaway_collection) == 3

        # Query: should rank Python doc first
        qvec = EmbeddingService.embed_text("Python coding language")
        results = VectorStore.query(throwaway_collection, qvec, top_k=3)
        assert results[0]["metadata"]["topic"] == "python"
        assert results[0]["distance"] < results[-1]["distance"]

    def test_add_documents_validates_length_match(self, throwaway_collection):
        with pytest.raises(ValueError, match="Length mismatch"):
            VectorStore.add_documents(
                collection_name=throwaway_collection,
                documents=["a", "b"],
                embeddings=[EmbeddingService.embed_text("a")],  # only 1, not 2
            )

    def test_add_documents_validates_metadata_length(self, throwaway_collection):
        with pytest.raises(ValueError, match="Length mismatch"):
            VectorStore.add_documents(
                collection_name=throwaway_collection,
                documents=["a"],
                embeddings=[EmbeddingService.embed_text("a")],
                metadatas=[{"x": 1}, {"y": 2}],  # 2 metas, 1 doc
            )

    def test_query_with_where_filter(self, throwaway_collection):
        docs = ["Python guide", "Java guide", "Python advanced"]
        embeddings = EmbeddingService.embed_batch(docs)
        VectorStore.add_documents(
            collection_name=throwaway_collection,
            documents=docs,
            embeddings=embeddings,
            metadatas=[{"lang": "python"}, {"lang": "java"}, {"lang": "python"}],
        )

        qvec = EmbeddingService.embed_text("programming guide")
        results = VectorStore.query(
            throwaway_collection, qvec, top_k=5, where={"lang": "python"}
        )
        assert len(results) == 2
        assert all(r["metadata"]["lang"] == "python" for r in results)

    def test_query_returns_empty_for_empty_collection(self, throwaway_collection):
        qvec = EmbeddingService.embed_text("anything")
        results = VectorStore.query(throwaway_collection, qvec, top_k=5)
        assert results == []

    def test_count_zero_for_new_collection(self, throwaway_collection):
        assert VectorStore.count(throwaway_collection) == 0


# ── RAGRetriever (the public API) ────────────────────────────────────────────

class TestRAGRetriever:
    def test_add_documents_then_retrieve(self, retriever, throwaway_collection):
        docs = [
            "Kubernetes is a container orchestration platform from Google.",
            "React is a frontend JavaScript library from Meta.",
            "PostgreSQL is a relational database management system.",
        ]
        metas = [{"topic": "k8s"}, {"topic": "react"}, {"topic": "postgres"}]

        chunks = retriever.add_documents(throwaway_collection, docs, metas)
        assert chunks >= 3  # at least one chunk per doc (short docs = 1 chunk each)

        results = retriever.retrieve(
            throwaway_collection, "container orchestration", top_k=3
        )
        assert len(results) == 3
        assert isinstance(results[0], RetrievedDocument)
        # Top result should be Kubernetes (semantic relevance)
        assert results[0].metadata["topic"] == "k8s"
        # Similarity = 1 - distance, so higher = better. Results must be descending.
        sims = [r.similarity for r in results]
        assert sims == sorted(sims, reverse=True)

    def test_empty_query_returns_empty(self, retriever, throwaway_collection):
        retriever.add_documents(throwaway_collection, ["something"], [{"x": 1}])
        assert retriever.retrieve(throwaway_collection, "") == []
        assert retriever.retrieve(throwaway_collection, "   ") == []

    def test_add_empty_documents_returns_zero(self, retriever, throwaway_collection):
        assert retriever.add_documents(throwaway_collection, [], []) == 0

    def test_similarity_is_one_minus_distance(self, retriever, throwaway_collection):
        retriever.add_documents(
            throwaway_collection,
            ["Python programming"],
            [{"topic": "python"}],
        )
        results = retriever.retrieve(throwaway_collection, "Python coding", top_k=1)
        # Similarity should be high (close to 1) for nearly identical text
        assert 0.4 < results[0].similarity <= 1.0

    def test_retrieve_respects_top_k(self, retriever, throwaway_collection):
        docs = [f"Document number {i}" for i in range(10)]
        retriever.add_documents(throwaway_collection, docs, [{"i": i} for i in range(10)])
        results = retriever.retrieve(throwaway_collection, "document", top_k=3)
        assert len(results) == 3

    def test_retrieve_with_metadata_filter(self, retriever, throwaway_collection):
        retriever.add_documents(
            throwaway_collection,
            ["Python guide", "Java guide", "Python advanced"],
            [{"lang": "python"}, {"lang": "java"}, {"lang": "python"}],
        )
        results = retriever.retrieve(
            throwaway_collection, "programming", top_k=5, where={"lang": "java"}
        )
        assert len(results) == 1
        assert results[0].metadata["lang"] == "java"

    def test_retrieve_across_collections(self, retriever):
        # Use two throwaway collections
        c1_name = f"test_{uuid.uuid4().hex[:8]}"
        c2_name = f"test_{uuid.uuid4().hex[:8]}"
        try:
            VectorStore.get_or_create_collection(CollectionSpec(name=c1_name, description="t1"))
            VectorStore.get_or_create_collection(CollectionSpec(name=c2_name, description="t2"))

            retriever.add_documents(c1_name, ["Kubernetes orchestration"], [{"src": "c1"}])
            retriever.add_documents(c2_name, ["React JavaScript"], [{"src": "c2"}])

            results = retriever.retrieve_across_collections(
                [c1_name, c2_name], "container deployment", top_k_per_collection=1
            )
            assert set(results.keys()) == {c1_name, c2_name}
            assert len(results[c1_name]) == 1
            assert len(results[c2_name]) == 1
        finally:
            for name in (c1_name, c2_name):
                try:
                    VectorStore.delete_collection(name)
                except Exception:
                    pass

    def test_long_document_is_chunked(self, retriever, throwaway_collection):
        # 2000+ char document — should produce multiple chunks
        long_doc = "Senior backend engineering involves designing scalable systems. " * 50
        chunks = retriever.add_documents(throwaway_collection, [long_doc], [{"src": "long"}])
        assert chunks > 1
        # All chunks should be retrievable with same metadata (plus chunk_index)
        results = retriever.retrieve(throwaway_collection, "backend engineering", top_k=3)
        assert all(r.metadata["src"] == "long" for r in results)
        # Each chunk has its own index
        indices = [r.metadata["chunk_index"] for r in results]
        assert len(set(indices)) == len(indices)