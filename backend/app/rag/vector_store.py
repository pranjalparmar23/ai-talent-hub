"""ChromaDB vector store wrapper.

Connects to the ChromaDB container and exposes a thin API for writing
chunks, querying, and managing collections.
"""

import logging
import os
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
    def upsert_documents(
        cls,
        collection_name: str,
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
        ids: list[str],
    ) -> list[str]:
        """Insert chunks, or overwrite them when the id already exists.

        The ids are required: the ingest pipeline gives every chunk a fixed id, so
        loading the same content twice changes nothing.
        """
        if not (len(documents) == len(embeddings) == len(metadatas) == len(ids)):
            raise ValueError(
                "documents, embeddings, metadatas and ids must be the same length"
            )
        if not ids:
            return []

        collection = cls._get_collection_by_name(collection_name)
        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        return ids

    @classmethod
    def get_by_where(cls, collection_name: str, where: dict) -> list[dict]:
        """Every chunk matching a filter, as id and metadata (no text, no vectors)."""
        collection = cls._get_collection_by_name(collection_name)
        result = collection.get(where=where, include=["metadatas"])
        ids = result.get("ids") or []
        metas = result.get("metadatas") or [None] * len(ids)
        return [{"id": i, "metadata": m or {}} for i, m in zip(ids, metas)]

    @classmethod
    def update_metadatas(
        cls, collection_name: str, ids: list[str], metadatas: list[dict]
    ) -> int:
        """Change the metadata of existing chunks. Text and vectors stay as they are."""
        if len(ids) != len(metadatas):
            raise ValueError("ids and metadatas must be the same length")
        if not ids:
            return 0
        collection = cls._get_collection_by_name(collection_name)
        collection.update(ids=ids, metadatas=metadatas)
        return len(ids)

    @classmethod
    def delete_ids(cls, collection_name: str, ids: list[str]) -> int:
        """Delete chunks by id. Returns how many ids were asked for."""
        if not ids:
            return 0
        cls._get_collection_by_name(collection_name).delete(ids=ids)
        return len(ids)

    @classmethod
    def query(
        cls,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
        where: dict | None = None,
        include_embeddings: bool = False,
    ) -> list[dict]:
        """Nearest chunks to a vector. Each row has id, document, metadata, distance.

        With `include_embeddings=True` each row also has the stored `embedding`
        (a list of floats). The retriever needs it to pick varied results (MMR).
        """
        collection = cls._get_collection_by_name(collection_name)

        include = ["documents", "metadatas", "distances"]
        if include_embeddings:
            include.append("embeddings")

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
            include=include,
        )

        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        vectors = None
        if include_embeddings:
            embeddings = results.get("embeddings")
            if embeddings is not None and len(embeddings) > 0:
                vectors = embeddings[0]

        rows = []
        for n, (i, d, m, dist) in enumerate(zip(ids, docs, metas, distances)):
            row = {"id": i, "document": d, "metadata": m or {}, "distance": dist}
            if vectors is not None:
                row["embedding"] = [float(x) for x in vectors[n]]
            rows.append(row)
        return rows

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
