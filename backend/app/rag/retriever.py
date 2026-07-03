"""RAG Retriever — the clean orchestration layer.

Brings together EmbeddingService + TextChunker + VectorStore into one
class with two main operations: ingest documents, retrieve relevant ones.

This is the only RAG module that agent code should import — embeddings,
chunking, and vector storage are implementation details.
"""
import logging
from dataclasses import dataclass
from typing import Optional
from app.rag.embeddings import EmbeddingService
from app.rag.chunking import TextChunker
from app.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievedDocument:
    """One result from a retrieval query."""
    document: str
    metadata: dict
    similarity: float  # 1.0 = identical, 0.0 = orthogonal, <0 = opposite direction
    id: str

    def __repr__(self) -> str:
        preview = self.document[:60].replace("\n", " ")
        return f"<RetrievedDocument similarity={self.similarity:.3f} '{preview}...'>"


class RAGRetriever:
    """Public RAG API: add documents to collections, retrieve by query."""

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ):
        self._chunker = TextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    # ── Ingestion ────────────────────────────────────────────

    def add_documents(
        self,
        collection_name: str,
        documents: list[str],
        metadatas: Optional[list[dict]] = None,
    ) -> int:
        """Chunk, embed, and store documents in a collection.

        Args:
            collection_name: Target ChromaDB collection.
            documents: Raw text documents.
            metadatas: Optional metadata per document (chunks inherit it).

        Returns:
            Total chunks inserted (may be > len(documents) if docs were long).
        """
        if not documents:
            logger.warning("add_documents called with empty list")
            return 0

        # 1. Chunk long documents into bite-sized pieces
        chunks, chunk_metas = self._chunker.chunk_documents(documents, metadatas)
        if not chunks:
            logger.warning("Chunking produced no chunks")
            return 0

        # 2. Embed all chunks in one batched call (much faster than per-chunk)
        embeddings = EmbeddingService.embed_batch(chunks)

        # 3. Persist to ChromaDB
        VectorStore.add_documents(
            collection_name=collection_name,
            documents=chunks,
            embeddings=embeddings,
            metadatas=chunk_metas,
        )

        logger.info(
            f"Ingested {len(documents)} docs → {len(chunks)} chunks "
            f"into '{collection_name}'"
        )
        return len(chunks)

    # ── Retrieval ────────────────────────────────────────────

    def retrieve(
        self,
        collection_name: str,
        query: str,
        top_k: int = 5,
        where: Optional[dict] = None,
    ) -> list[RetrievedDocument]:
        """Find the top-k most similar documents to a natural-language query.

        Args:
            collection_name: Collection to search.
            query: User's question or search text.
            top_k: How many results to return.
            where: Optional metadata filter, e.g. {"company": "Amazon"}.

        Returns:
            Results sorted by similarity (best match first).
        """
        if not query or not query.strip():
            return []

        query_vec = EmbeddingService.embed_text(query)
        raw_results = VectorStore.query(
            collection_name=collection_name,
            query_embedding=query_vec,
            top_k=top_k,
            where=where,
        )

        # Convert ChromaDB's cosine distance to similarity (1 - distance)
        return [
            RetrievedDocument(
                id=r["id"],
                document=r["document"],
                metadata=r["metadata"] or {},
                similarity=1.0 - (r["distance"] if r["distance"] is not None else 1.0),
            )
            for r in raw_results
        ]

    def retrieve_across_collections(
        self,
        collection_names: list[str],
        query: str,
        top_k_per_collection: int = 3,
    ) -> dict[str, list[RetrievedDocument]]:
        """Search multiple collections at once. Returns results grouped by collection.

        Useful when an agent needs context from several namespaces — e.g. the
        InterviewAgent needs both behavioral_questions AND company_interviews.
        """
        return {
            name: self.retrieve(name, query, top_k=top_k_per_collection)
            for name in collection_names
        }

    # ── Utilities ────────────────────────────────────────────

    @staticmethod
    def collection_size(collection_name: str) -> int:
        """How many chunks are stored in this collection."""
        return VectorStore.count(collection_name)