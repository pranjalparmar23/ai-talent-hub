"""ChromaDB vector store wrapper.

Connects to the ChromaDB container and exposes a thin sync API for adding
documents, querying, and managing collections.
"""
import os
import uuid
import logging
from typing import Optional
import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.api.models.Collection import Collection
from app.rag.collections import COLLECTIONS, CollectionSpec

logger = logging.getLogger(__name__)


class VectorStore:
    """Singleton-style wrapper around the ChromaDB HTTP client."""

    _client: Optional[chromadb.HttpClient] = None

    # ── Connection ───────────────────────────────────────────

    @classmethod
    def get_client(cls) -> chromadb.HttpClient:
        """Lazy-init the ChromaDB client (one per process)."""
        if cls._client is None:
            host = os.getenv("CHROMA_HOST", "localhost")
            port = int(os.getenv("CHROMA_PORT", "8001"))
            cls._client = chromadb.HttpClient(
                host=host,
                port=port,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            logger.info(f"ChromaDB client connected to {host}:{port}")
        return cls._client

    @classmethod
    def heartbeat(cls) -> int:
        """Return ChromaDB heartbeat — used by /health endpoint."""
        return cls.get_client().heartbeat()

    # ── Collection management ────────────────────────────────

    @classmethod
    def get_or_create_collection(cls, spec: CollectionSpec) -> Collection:
        """Get a collection by spec, creating it if it doesn't exist."""
        client = cls.get_client()
        return client.get_or_create_collection(
            name=spec.name,
            metadata={
                "description": spec.description,
                "hnsw:space": spec.distance_metric,
            },
        )

    @classmethod
    def get_collection(cls, name: str) -> Collection:
        """Get an existing collection by name. Raises if it doesn't exist."""
        return cls.get_client().get_collection(name=name)

    @classmethod
    def list_collections(cls) -> list[str]:
        """Return all collection names currently in ChromaDB."""
        return [c.name for c in cls.get_client().list_collections()]

    @classmethod
    def delete_collection(cls, name: str) -> None:
        """Delete a collection by name. Used by tests for cleanup."""
        cls.get_client().delete_collection(name=name)
        logger.info(f"Deleted collection: {name}")

    @classmethod
    def init_all_collections(cls) -> list[str]:
        """Create all collections defined in COLLECTIONS registry. Idempotent."""
        created = []
        for spec in COLLECTIONS:
            cls.get_or_create_collection(spec)
            created.append(spec.name)
        return created

    # ── Document operations ──────────────────────────────────

    @classmethod
    def add_documents(
        cls,
        collection_name: str,
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: Optional[list[dict]] = None,
        ids: Optional[list[str]] = None,
    ) -> list[str]:
        """Add documents with pre-computed embeddings to a collection.

        Args:
            collection_name: Target collection.
            documents: The raw text chunks (one per row).
            embeddings: Pre-computed vectors (one per document, same length).
            metadatas: Optional metadata dicts (one per document).
            ids: Optional unique IDs. Auto-generated UUIDs if not provided.

        Returns:
            The list of IDs used (useful when auto-generated).
        """
        if not documents:
            return []
        if len(embeddings) != len(documents):
            raise ValueError(
                f"Length mismatch: {len(documents)} documents but {len(embeddings)} embeddings"
            )
        if metadatas is not None and len(metadatas) != len(documents):
            raise ValueError(
                f"Length mismatch: {len(documents)} documents but {len(metadatas)} metadatas"
            )

        if ids is None:
            ids = [str(uuid.uuid4()) for _ in documents]

        collection = cls.get_collection(collection_name)
        collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        logger.info(f"Added {len(documents)} documents to '{collection_name}'")
        return ids

    @classmethod
    def query(
        cls,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
        where: Optional[dict] = None,
    ) -> list[dict]:
        """Find the top-k most similar documents in a collection.

        Args:
            collection_name: Collection to search.
            query_embedding: Pre-computed query vector.
            top_k: Number of results to return.
            where: Optional metadata filter, e.g. {"company": "Amazon"}.

        Returns:
            List of result dicts with keys: id, document, metadata, distance.
            Sorted by similarity (best match first).
        """
        collection = cls.get_collection(collection_name)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
        )

        # Chroma returns lists-of-lists (one entry per query). We only send 1 query,
        # so unwrap the outer list.
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        return [
            {
                "id": ids[i],
                "document": documents[i],
                "metadata": metadatas[i] if metadatas else {},
                "distance": distances[i] if distances else None,
            }
            for i in range(len(ids))
        ]

    @classmethod
    def count(cls, collection_name: str) -> int:
        """Return number of documents in a collection. Useful for monitoring."""
        return cls.get_collection(collection_name).count()


# ── Compatibility shim for legacy imports ────────────────────
def get_vector_store() -> type[VectorStore]:
    """Legacy alias — returns the VectorStore class for backward compatibility."""
    return VectorStore