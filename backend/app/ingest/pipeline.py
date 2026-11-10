"""Ingestion pipeline: turns one KnowledgeDoc into stored chunks (Phase 7, task 5).

For each document:

1. Licence gate. A refused document is reported, never stored.
2. Fingerprint the text and how it is embedded (content_hash).
3. Look at what is already stored for this document.
   - Same fingerprint: nothing is re-embedded, only the dates and credit line
     are refreshed, so good content does not expire just because it was checked.
   - New or changed: split into chunks, embed, and write with fixed ids.
4. Delete chunks that are no longer part of the document, including a copy left
   in the other collection if the content_type changed.

Because chunk ids are fixed (`<doc_id>:<index>`), running the same content twice
never creates duplicates. New chunks are written BEFORE old ones are deleted, so
a failure half way leaves the old content in place instead of nothing.

The ChromaDB collections must exist first (`python scripts/init_chroma.py`).
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.ingest.licence import check_licence
from app.ingest.schema import (
    INTERVIEW_COLLECTION,
    LEARNING_COLLECTION,
    KnowledgeDoc,
    chunk_metadata,
    compute_content_hash,
    make_chunk_id,
)

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# What happened to one document.
ADDED = "added"
UPDATED = "updated"
UNCHANGED = "unchanged"
REFUSED = "refused"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass(frozen=True)
class IngestResult:
    doc_id: str
    url: str
    status: str
    chunks_written: int = 0
    chunks_deleted: int = 0
    reason: str = ""


@dataclass
class IngestSummary:
    results: list[IngestResult] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(r.status for r in self.results))

    @property
    def ok(self) -> bool:
        """True when no document failed. Refused documents are not failures."""
        return not any(r.status == FAILED for r in self.results)


class IngestPipeline:
    """Writes documents into the knowledge collections.

    `store`, `embedder` and `chunker` can be replaced in tests. When left out,
    the real VectorStore, EmbeddingService and TextChunker are used (they are
    imported here, not at the top, so importing this module stays light).
    """

    def __init__(
        self,
        *,
        store: Any = None,
        embedder: Any = None,
        chunker: Any = None,
        embedding_model: str | None = None,
    ) -> None:
        if store is None:
            from app.rag.vector_store import VectorStore

            store = VectorStore
        if embedder is None or embedding_model is None:
            from app.rag.embeddings import EMBEDDING_MODEL_NAME, EmbeddingService

            embedder = embedder or EmbeddingService
            embedding_model = embedding_model or EMBEDDING_MODEL_NAME
        if chunker is None:
            from app.rag.chunking import TextChunker

            chunker = TextChunker(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        self.store = store
        self.embedder = embedder
        self.chunker = chunker
        self.embedding_model = embedding_model

    def ingest(self, doc: KnowledgeDoc) -> IngestResult:
        """Store one document. A licence refusal is returned, other errors raise."""
        decision = check_licence(doc)
        if not decision.allowed:
            logger.warning("Refused %s: %s", doc.url, decision.reason)
            return IngestResult(doc.doc_id, doc.url, REFUSED, reason=decision.reason)
        if decision.licence != doc.licence:
            doc = doc.model_copy(update={"licence": decision.licence})

        chunks = self.chunker.chunk_text(doc.text)
        if not chunks:
            return IngestResult(doc.doc_id, doc.url, SKIPPED, reason="no text to store")

        content_hash = compute_content_hash(
            doc.text,
            embedding_model=self.embedding_model,
            chunk_size=self.chunker.chunk_size,
            chunk_overlap=self.chunker.chunk_overlap,
        )
        doc = doc.model_copy(update={"content_hash": content_hash})

        collection = doc.collection
        ids = [make_chunk_id(doc.doc_id, i) for i in range(len(chunks))]
        metadatas = [
            chunk_metadata(doc, i, embedding_model=self.embedding_model)
            for i in range(len(chunks))
        ]

        existing = self.store.get_by_where(collection, {"doc_id": doc.doc_id})
        existing_ids = {row["id"] for row in existing}

        if (
            existing
            and existing_ids == set(ids)
            and all(r["metadata"].get("content_hash") == content_hash for r in existing)
        ):
            self.store.update_metadatas(collection, ids, metadatas)
            moved = self._remove_from_other_collection(collection, doc.doc_id)
            return IngestResult(
                doc.doc_id,
                doc.url,
                UNCHANGED,
                chunks_deleted=moved,
                reason="refreshed dates",
            )

        embeddings = self.embedder.embed_batch(chunks)
        self.store.upsert_documents(
            collection_name=collection,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
            ids=ids,
        )
        stale = sorted(existing_ids - set(ids))
        deleted = self.store.delete_ids(collection, stale)
        deleted += self._remove_from_other_collection(collection, doc.doc_id)

        status = UPDATED if existing else ADDED
        logger.info("%s %s: %d chunks", status, doc.url, len(chunks))
        return IngestResult(
            doc.doc_id,
            doc.url,
            status,
            chunks_written=len(chunks),
            chunks_deleted=deleted,
        )

    def ingest_many(self, docs: Iterable[KnowledgeDoc]) -> IngestSummary:
        """Store many documents. One bad document never stops the rest."""
        summary = IngestSummary()
        for doc in docs:
            try:
                summary.results.append(self.ingest(doc))
            except Exception as err:
                logger.exception("Failed to ingest %s", doc.url)
                summary.results.append(
                    IngestResult(
                        doc.doc_id,
                        doc.url,
                        FAILED,
                        reason=f"{type(err).__name__}: {err}",
                    )
                )
        return summary

    def _remove_from_other_collection(self, collection: str, doc_id: str) -> int:
        """If content_type changed, the old copy sits in the other collection."""
        other = (
            LEARNING_COLLECTION
            if collection == INTERVIEW_COLLECTION
            else INTERVIEW_COLLECTION
        )
        rows = self.store.get_by_where(other, {"doc_id": doc_id})
        return self.store.delete_ids(other, [r["id"] for r in rows])
