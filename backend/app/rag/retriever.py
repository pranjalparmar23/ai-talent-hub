"""RAGRetriever — the public API for semantic search over ChromaDB collections.

Two ways to search:

- `retrieve()` is the original search. It works on any collection and returns text
  and a similarity score. The interview and roadmap agents still use it until
  task 11, and it is removed in task 15.
- `search()` is retriever v2 (Phase 7, task 9). It searches the two knowledge
  collections, filters by domain, role family, level and content type, ignores
  expired chunks, drops weak matches, avoids near-duplicate results, and returns
  each chunk with its source, licence and credit line.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.ingest.schema import (
    COLLECTION_BY_CONTENT_TYPE,
    INTERVIEW_COLLECTION,
    LEARNING_COLLECTION,
    LEVELS,
    ROLE_FAMILIES,
    SCHEMA_VERSION,
    citation_from_metadata,
)
from app.rag.chunking import TextChunker
from app.rag.embeddings import EmbeddingService
from app.rag.ranking import mmr_select
from app.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

KNOWLEDGE_COLLECTIONS = (INTERVIEW_COLLECTION, LEARNING_COLLECTION)

# Defaults for search(). They can be changed per call. The cutoff was raised from 0.30
# to 0.40 in task 13: on the golden set precision went from 0.62 to 0.77 and the
# no-match cases all came back empty, with hit rate and MRR unchanged.
DEFAULT_TOP_K = 5
DEFAULT_MIN_SIMILARITY = 0.40  # below this a match is noise (what the agents use)
DEFAULT_MMR_LAMBDA = 0.7  # 1.0 = only relevance, 0.0 = only variety
DEFAULT_MAX_PER_DOC = 2  # most chunks one page may supply
FETCH_FACTOR = 4  # candidates fetched = top_k x this ...
MIN_FETCH = 20  # ... but never fewer than this


@dataclass
class RetrievedDocument:
    id: str
    document: str
    metadata: dict[str, Any] = field(default_factory=dict)
    similarity: float = 0.0

    def __repr__(self) -> str:
        preview = self.document[:50].replace("\n", " ")
        return f"<RetrievedDocument similarity={self.similarity:.3f} '{preview}...'>"


@dataclass
class KnowledgeChunk:
    """One search hit: the text, how well it matches, and where it came from."""

    id: str
    text: str
    similarity: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def citation(self) -> dict[str, str]:
        """title, url, licence and attribution (the credit line to show)."""
        return citation_from_metadata(self.metadata)

    @property
    def url(self) -> str:
        return str(self.metadata.get("url", ""))


@dataclass
class SearchResult:
    """What search() returns.

    `chunks` is in the order MMR picked them. The first is the best match. Each next
    one trades a little relevance for variety, so a later chunk can score lower than
    one that was left out. With `mmr=False` the order is plain best first.

    `candidates` and `below_cutoff` say why a result is empty: nothing matched the
    filters at all, or everything matched too weakly. The fallback (task 10) and the
    empty-retrieval metric read these.
    """

    collection: str
    chunks: list[KnowledgeChunk] = field(default_factory=list)
    candidates: int = 0  # chunks that passed the filters
    below_cutoff: int = 0  # of those, dropped as weak matches

    @property
    def empty(self) -> bool:
        return not self.chunks

    @property
    def citations(self) -> list[dict[str, str]]:
        """One citation per page, in the order the pages first appear."""
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        for chunk in self.chunks:
            citation = chunk.citation
            key = citation["url"] or chunk.id
            if key in seen:
                continue
            seen.add(key)
            out.append(citation)
        return out


def _match_any(field_name: str, values: Sequence[str]) -> dict[str, Any]:
    """One condition: the field equals one of the values."""
    unique = list(dict.fromkeys(values))
    if len(unique) == 1:
        return {field_name: unique[0]}
    return {field_name: {"$in": unique}}


def build_where(
    *,
    now_ts: int,
    domain: str | None = None,
    role_family: str | None = None,
    level: str | None = None,
    content_type: str | Sequence[str] | None = None,
) -> dict[str, Any]:
    """The ChromaDB filter for search().

    Always: the current schema version, and not expired. Optional filters add to it.
    A chunk marked `all` (role family or level) or `general` (domain) fits any request.
    """
    conditions: list[dict[str, Any]] = [
        {"schema_version": SCHEMA_VERSION},
        {"expires_at_ts": {"$gt": now_ts}},
    ]
    if domain:
        conditions.append(_match_any("domain", [domain, "general"]))
    if role_family:
        conditions.append(_match_any("role_family", [role_family, "all"]))
    if level:
        conditions.append(_match_any("level", [level, "all"]))
    if content_type:
        types = [content_type] if isinstance(content_type, str) else list(content_type)
        conditions.append(_match_any("content_type", types))
    return {"$and": conditions}


def _check_filters(
    collection: str,
    role_family: str | None,
    level: str | None,
    content_type: str | Sequence[str] | None,
) -> None:
    """Fail loudly on a typo, instead of quietly returning nothing."""
    if collection not in KNOWLEDGE_COLLECTIONS:
        raise ValueError(
            f"search() works on {', '.join(KNOWLEDGE_COLLECTIONS)}, not '{collection}'. "
            "Use retrieve() for the old collections."
        )
    if role_family and role_family not in ROLE_FAMILIES:
        raise ValueError(
            f"role_family must be one of {ROLE_FAMILIES}, got '{role_family}'"
        )
    if level and level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}, got '{level}'")
    if content_type:
        types = [content_type] if isinstance(content_type, str) else list(content_type)
        for item in types:
            home = COLLECTION_BY_CONTENT_TYPE.get(item)
            if home is None:
                known = ", ".join(COLLECTION_BY_CONTENT_TYPE)
                raise ValueError(f"unknown content_type '{item}'. Known: {known}")
            if home != collection:
                raise ValueError(
                    f"content_type '{item}' is stored in {home}, not in {collection}"
                )


def _doc_key(row: dict[str, Any]) -> str:
    """The page a chunk belongs to. Chunk ids look like '<doc_id>:<index>'."""
    meta = row.get("metadata") or {}
    return str(meta.get("doc_id") or str(row.get("id", "")).rsplit(":", 1)[0])


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

    def search(
        self,
        query: str,
        collection: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        domain: str | None = None,
        role_family: str | None = None,
        level: str | None = None,
        content_type: str | Sequence[str] | None = None,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        mmr: bool = True,
        mmr_lambda: float = DEFAULT_MMR_LAMBDA,
        max_per_doc: int | None = DEFAULT_MAX_PER_DOC,
        now: datetime | None = None,
    ) -> SearchResult:
        """Retriever v2: filtered, fresh, varied results with their sources.

        Steps: filter in ChromaDB (current schema, not expired, plus the optional
        filters), fetch several times `top_k` candidates, drop those below
        `min_similarity`, then pick `top_k` with MMR (the first pick is the best match,
        the rest add variety) and at most `max_per_doc` chunks from one page. Pass
        `mmr=False` for plain best-first order.
        A search that finds nothing returns an empty result; it does not raise.
        """
        _check_filters(collection, role_family, level, content_type)
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        result = SearchResult(collection=collection)
        if not query or not query.strip() or top_k <= 0:
            return result

        started = time.perf_counter()
        where = build_where(
            now_ts=int(now.timestamp()),
            domain=domain.strip().lower() if domain else None,
            role_family=role_family,
            level=level,
            content_type=content_type,
        )
        fetch_k = max(top_k * FETCH_FACTOR, MIN_FETCH)
        rows = VectorStore.query(
            collection_name=collection,
            query_embedding=EmbeddingService.embed_text(query),
            top_k=fetch_k,
            where=where,
            include_embeddings=mmr,
        )
        result.candidates = len(rows)

        strong = []
        for row in rows:
            row["similarity"] = 1.0 - row.get("distance", 1.0)
            if row["similarity"] >= min_similarity:
                strong.append(row)
        result.below_cutoff = len(rows) - len(strong)

        vectors = None
        if mmr and strong and all(r.get("embedding") for r in strong):
            vectors = [r["embedding"] for r in strong]
        order = mmr_select(
            [r["similarity"] for r in strong],
            vectors,
            k=top_k,
            lambda_mult=mmr_lambda,
            groups=[_doc_key(r) for r in strong],
            max_per_group=max_per_doc or None,
        )
        result.chunks = [
            KnowledgeChunk(
                id=strong[i].get("id", ""),
                text=strong[i].get("document", ""),
                similarity=strong[i]["similarity"],
                metadata=strong[i].get("metadata") or {},
            )
            for i in order
        ]

        logger.info(
            "search collection=%s candidates=%d below_cutoff=%d returned=%d ms=%.0f",
            collection,
            result.candidates,
            result.below_cutoff,
            len(result.chunks),
            (time.perf_counter() - started) * 1000,
        )
        return result
