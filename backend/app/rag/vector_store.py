"""ChromaDB vector store wrapper.

Connects to the ChromaDB container and exposes a thin API for adding
documents, querying, and managing collections.
"""
import logging
import os
import uuid
from typing import Optional

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings as ChromaSettings

from app.rag.collections import COLLECTIONS, CollectionSpec

logger = logging.getLogger(__name__)


class VectorStore:
    """Singleton-style wrapper around the ChromaDB HTTP client."""

    _client: Optional["chromadb.HttpClient"] = None

    @classmethod
    def get_client(cls) -> "chromadb.HttpClient":
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
        client = cls.get_client()
        return client.get_or_create_collection(
            name=spec.name,
            metadata={
                "description": spec.description,
                "hnsw:space": spec.distance_metric,
            },
        )

    @classmethod
    def _get_collection_by_name(cls, name: str) -> Collection:
        return cls.get_client().get_collection(name=name)

    @classmethod
    def add_documents(
        cls,
        collection_name: str,
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict] | None = None,
        ids: list[str] | None = None,
    ) -> list[str]:
        if len(documents) != len(embeddings):
            raise ValueError(
                f"Length mismatch: {len(documents)} documents vs {len(embeddings)} embeddings"
            )
        if metadatas is not None and len(metadatas) != len(documents):
            raise ValueError(
                f"Length mismatch: {len(documents)} documents vs {len(metadatas)} metadatas"
            )

        collection = cls._get_collection_by_name(collection_name)

        if ids is None:
            ids = [str(uuid.uuid4()) for _ in documents]

        collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        return ids

    @classmethod
    def query(
        cls,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
        where: dict | None = None,
    ) -> list[dict]:
        collection = cls._get_collection_by_name(collection_name)

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
        )

        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        return [
            {"id": i, "document": d, "metadata": m or {}, "distance": dist}
            for i, d, m, dist in zip(ids, docs, metas, distances)
        ]

    @classmethod
    def count(cls, collection_name: str) -> int:
        return cls._get_collection_by_name(collection_name).count()

    @classmethod
    def list_collections(cls) -> list[str]:
        client = cls.get_client()
        return [c.name for c in client.list_collections()]

    @classmethod
    def delete_collection(cls, name: str) -> None:
        client = cls.get_client()
        client.delete_collection(name=name)
        logger.info(f"Deleted collection: {name}")

    @classmethod
    def init_all_collections(cls) -> list[str]:
        created = []
        for spec in COLLECTIONS:
            cls.get_or_create_collection(spec)
            created.append(spec.name)
            logger.info(f"Ensured collection exists: {spec.name}")
        return created

    @classmethod
    def heartbeat(cls) -> int:
        return cls.get_client().heartbeat()


# ── Compatibility shim for legacy imports ────────────────────
def get_vector_store() -> type[VectorStore]:
    """Legacy alias — returns the VectorStore class for backward compatibility."""
    return VectorStore