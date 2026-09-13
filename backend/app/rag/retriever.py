"""RAGRetriever — the public API for semantic search over ChromaDB collections."""

import logging
from dataclasses import dataclass, field
from typing import Any

from app.rag.chunking import TextChunker
from app.rag.embeddings import EmbeddingService
from app.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievedDocument:
    id: str
    document: str
    metadata: dict[str, Any] = field(default_factory=dict)
    similarity: float = 0.0

    def __repr__(self) -> str:
        preview = self.document[:50].replace("\n", " ")
        return f"<RetrievedDocument similarity={self.similarity:.3f} '{preview}...'>"


class RAGRetriever:
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.chunker = TextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    def add_documents(
        self,
        collection_name: str,
        documents: list[str],
        metadatas: list[dict] | None = None,
    ) -> int:
        if not documents:
            return 0

        chunks, chunk_metas = self.chunker.chunk_documents(documents, metadatas)
        if not chunks:
            return 0

        embeddings = EmbeddingService.embed_batch(chunks)
        VectorStore.add_documents(
            collection_name=collection_name,
            documents=chunks,
            embeddings=embeddings,
            metadatas=chunk_metas,
        )
        return len(chunks)

    def retrieve(
        self,
        collection_name: str,
        query: str,
        top_k: int = 5,
        where: dict | None = None,
    ) -> list[RetrievedDocument]:
        if not query or not query.strip():
            return []

        query_vector = EmbeddingService.embed_text(query)
        raw_results = VectorStore.query(
            collection_name=collection_name,
            query_embedding=query_vector,
            top_k=top_k,
            where=where,
        )

        return [
            RetrievedDocument(
                id=r.get("id", ""),
                document=r.get("document", ""),
                metadata=r.get("metadata", {}) or {},
                similarity=1.0 - r.get("distance", 1.0),
            )
            for r in raw_results
        ]

    def retrieve_across_collections(
        self,
        collection_names: list[str],
        query: str,
        top_k_per_collection: int = 5,
    ) -> dict[str, list[RetrievedDocument]]:
        return {
            name: self.retrieve(name, query, top_k=top_k_per_collection)
            for name in collection_names
        }
