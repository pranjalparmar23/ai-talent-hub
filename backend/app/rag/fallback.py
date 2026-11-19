"""What to do when our own knowledge has nothing to offer (Phase 7, task 10).

`retrieve_with_fallback()` runs `RAGRetriever.search()` and says which route the answer
takes:

- `knowledge`: at least one reviewed chunk matched. Answer from it and show its sources.
- `general_knowledge`: nothing usable matched, or retrieval itself failed. The agent
  carries on with the model's own knowledge, shows no sources, and tells the user that
  the answer is not based on our reviewed material.

This is our own code only. There is no web search and no outside service, so it costs
nothing, sends nothing anywhere and needs no setting. Nothing is stored either.

Retrieval problems never break an interview or a roadmap: if the vector store or the
embedding model fails, the route is `general_knowledge` and the error is logged. A bad
filter value (a typo in `role_family`, say) is a bug in the calling code, so that still
raises, exactly as `search()` does.

The query text is never logged, only counts, so gaps in the knowledge base can be
counted without writing anyone's words to the logs.
"""

import logging
from dataclasses import dataclass
from typing import Any

from app.rag.retriever import KnowledgeChunk, RAGRetriever, SearchResult

logger = logging.getLogger(__name__)

# ── routes and reasons ───────────────────────────────────────

ROUTE_KNOWLEDGE = "knowledge"
ROUTE_GENERAL = "general_knowledge"

REASON_NO_MATCH = "no_match"  # nothing passed the filters
REASON_WEAK_MATCH = "weak_match"  # chunks passed the filters, all too weak
REASON_EMPTY_QUERY = "empty_query"  # there was nothing to search for
REASON_RETRIEVAL_ERROR = "retrieval_error"  # the vector store or model failed

# ── the two notes ────────────────────────────────────────────

# For the model's prompt.
GENERAL_KNOWLEDGE_PROMPT_NOTE = (
    "No reviewed source matched this request. Answer from general knowledge only. "
    "Do not invent sources, links or quotes, and do not present the answer as based "
    "on reviewed material."
)

# For the person using the app.
GENERAL_KNOWLEDGE_USER_NOTE = (
    "Not based on our reviewed sources. This comes from general knowledge, "
    "so check it before relying on it."
)


@dataclass(frozen=True)
class Retrieval:
    """The route taken, why, and (when there is one) the search result."""

    route: str
    reason: str = ""
    result: SearchResult | None = None  # None when retrieval failed

    @property
    def grounded(self) -> bool:
        """True when the answer can rest on reviewed chunks."""
        return self.route == ROUTE_KNOWLEDGE

    @property
    def chunks(self) -> list[KnowledgeChunk]:
        return self.result.chunks if self.grounded and self.result else []

    @property
    def citations(self) -> list[dict[str, str]]:
        """One per page. Always empty on the general-knowledge route."""
        return self.result.citations if self.grounded and self.result else []

    @property
    def prompt_note(self) -> str:
        """Text to add to the model's prompt. Empty when the answer is grounded."""
        return "" if self.grounded else GENERAL_KNOWLEDGE_PROMPT_NOTE

    @property
    def user_note(self) -> str:
        """Text to show the person. Empty when the answer is grounded."""
        return "" if self.grounded else GENERAL_KNOWLEDGE_USER_NOTE


def retrieve_with_fallback(
    query: str,
    collection: str,
    *,
    retriever: RAGRetriever | None = None,
    **search_options: Any,
) -> Retrieval:
    """Search our knowledge, and say so when there is nothing to build on.

    `search_options` go straight to `RAGRetriever.search()` (top_k, domain,
    role_family, level, content_type, min_similarity and so on).
    """
    retriever = retriever or RAGRetriever()
    try:
        result = retriever.search(query, collection, **search_options)
    except ValueError:
        raise  # a bad filter value: a bug in the caller, not something to hide
    except Exception:
        logger.exception("retrieval failed, using general knowledge: %s", collection)
        return _logged(Retrieval(ROUTE_GENERAL, REASON_RETRIEVAL_ERROR), collection)

    if not result.empty:
        return _logged(Retrieval(ROUTE_KNOWLEDGE, "", result), collection)
    if not (query or "").strip():
        reason = REASON_EMPTY_QUERY
    elif result.candidates > 0:
        reason = REASON_WEAK_MATCH
    else:
        reason = REASON_NO_MATCH
    return _logged(Retrieval(ROUTE_GENERAL, reason, result), collection)


def _logged(retrieval: Retrieval, collection: str) -> Retrieval:
    result = retrieval.result
    logger.info(
        "fallback route=%s reason=%s collection=%s candidates=%d below_cutoff=%d",
        retrieval.route,
        retrieval.reason or "-",
        collection,
        result.candidates if result else 0,
        result.below_cutoff if result else 0,
    )
    return retrieval
