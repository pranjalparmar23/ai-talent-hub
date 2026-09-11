"""ChromaDB vector store wrapper.

Connects to the ChromaDB container and exposes a thin async-friendly API
for adding documents, querying, and managing collections.
"""
import os
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
    def list_collections(cls) -> list[str]:
        """Return all collection names currently in ChromaDB."""
        client = cls.get_client()
        return [c.name for c in client.list_collections()]

    @classmethod
    def delete_collection(cls, name: str) -> None:
        """Delete a collection by name. Used by tests for cleanup."""
        client = cls.get_client()
        client.delete_collection(name=name)
        logger.info(f"Deleted collection: {name}")

    @classmethod
    def init_all_collections(cls) -> list[str]:
        """Create all collections defined in COLLECTIONS registry. Idempotent."""
        created = []
        for spec in COLLECTIONS:
            cls.get_or_create_collection(spec)
            created.append(spec.name)
            logger.info(f"Ensured collection exists: {spec.name}")
        return created

    @classmethod
    def heartbeat(cls) -> int:
        """Return ChromaDB heartbeat — used by /health endpoint."""
        return cls.get_client().heartbeat()

# ── Compatibility shim for legacy imports ────────────────────
def get_vector_store() -> VectorStore:
    """Legacy alias — returns the VectorStore class for backward compatibility."""
    return VectorStore