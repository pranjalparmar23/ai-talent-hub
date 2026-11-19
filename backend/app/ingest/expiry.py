"""Removing knowledge that has passed its expiry date (Phase 7, task 8).

Every chunk carries `expires_at_ts` (Unix seconds). A chunk is expired when that
time is now or earlier. The ingest command refreshes the dates of every page that
still exists in its source, so what is left expired afterwards is content that
disappeared upstream or was not refreshed in time.

Nothing here fetches or embeds anything. It only finds chunks by metadata and
deletes them by id. `dry_run=True` finds them and deletes nothing.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.ingest.schema import INTERVIEW_COLLECTION, LEARNING_COLLECTION

KNOWLEDGE_COLLECTION_NAMES = (INTERVIEW_COLLECTION, LEARNING_COLLECTION)

# Chroma handles large deletes, but small batches keep each call quick.
DELETE_BATCH_SIZE = 500


@dataclass
class ExpiredDoc:
    """One expired document: all its expired chunks, in one collection."""

    collection: str
    doc_id: str
    source_id: str
    url: str
    chunk_ids: list[str] = field(default_factory=list)


@dataclass
class ExpiryReport:
    docs: list[ExpiredDoc] = field(default_factory=list)
    chunks_deleted: int = 0
    dry_run: bool = False

    @property
    def documents(self) -> int:
        return len(self.docs)

    @property
    def chunks_found(self) -> int:
        return sum(len(d.chunk_ids) for d in self.docs)


def _expired_filter(now_ts: int, source_id: str | None) -> dict[str, Any]:
    expired = {"expires_at_ts": {"$lte": now_ts}}
    if source_id is None:
        return expired
    # Chroma wants "$and" when there is more than one condition.
    return {"$and": [{"source_id": source_id}, expired]}


def _doc_id_of(chunk_id: str, metadata: dict[str, Any]) -> str:
    # Chunk ids look like "<doc_id>:<index>", so the id is a safe fallback.
    return str(metadata.get("doc_id") or chunk_id.rsplit(":", 1)[0])


def find_expired(
    *,
    now: datetime,
    source_ids: Iterable[str] | None = None,
    store: Any = None,
) -> list[ExpiredDoc]:
    """Expired chunks in the knowledge collections, grouped by document.

    `source_ids=None` looks at every source. `now` must have a time zone.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if store is None:
        from app.rag.vector_store import VectorStore

        store = VectorStore

    now_ts = int(now.timestamp())
    sources: list[str | None] = [None] if source_ids is None else list(source_ids)

    found: dict[tuple[str, str], ExpiredDoc] = {}
    for collection in KNOWLEDGE_COLLECTION_NAMES:
        for source in sources:
            rows = store.get_by_where(collection, _expired_filter(now_ts, source))
            for row in rows:
                chunk_id = row["id"]
                meta = row.get("metadata") or {}
                doc_id = _doc_id_of(chunk_id, meta)
                doc = found.setdefault(
                    (collection, doc_id),
                    ExpiredDoc(
                        collection=collection,
                        doc_id=doc_id,
                        source_id=str(meta.get("source_id", "")),
                        url=str(meta.get("url", "")),
                    ),
                )
                if chunk_id not in doc.chunk_ids:
                    doc.chunk_ids.append(chunk_id)

    return sorted(found.values(), key=lambda d: (d.collection, d.url, d.doc_id))


def remove_expired(
    *,
    now: datetime,
    source_ids: Iterable[str] | None = None,
    dry_run: bool = False,
    store: Any = None,
) -> ExpiryReport:
    """Find expired chunks and delete them (or only report them when dry_run)."""
    if store is None:
        from app.rag.vector_store import VectorStore

        store = VectorStore

    docs = find_expired(now=now, source_ids=source_ids, store=store)
    report = ExpiryReport(docs=docs, dry_run=dry_run)
    if dry_run:
        return report

    for doc in docs:
        for start in range(0, len(doc.chunk_ids), DELETE_BATCH_SIZE):
            batch = doc.chunk_ids[start : start + DELETE_BATCH_SIZE]
            report.chunks_deleted += store.delete_ids(doc.collection, batch)
    return report
