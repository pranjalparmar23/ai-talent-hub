"""Helpers shared by the integration tests."""

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.ingest.schema import KnowledgeDoc


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_doc(**overrides: Any) -> KnowledgeDoc:
    """A valid, current, full-text document. Override any field."""
    now = utcnow()
    data: dict[str, Any] = dict(
        source_id="it14-source",
        url="https://example.com/it14/default",
        title="An integration test page",
        domain="software-engineering",
        content_type="experience",
        licence="MIT",
        attribution="Integration test fixture, MIT licence",
        fetched_at=now,
        expires_at=now + timedelta(days=90),
        text="A short page used by the integration tests.",
    )
    data.update(overrides)
    return KnowledgeDoc(**data)


def stored_urls(store: Any, collection: str) -> set[str]:
    """Every page URL in a real collection. Empty when the collection does not exist."""
    try:
        rows = (
            store.get_client()
            .get_collection(name=collection)
            .get(include=["metadatas"])
        )
    except Exception:
        return set()
    return {
        str(meta["url"])
        for meta in rows.get("metadatas") or []
        if meta and meta.get("url")
    }


def plan_links(plan: Mapping[str, Any]) -> list[str]:
    """Every resource URL in a learning plan, in order (repeats kept)."""
    return [
        str(r["url"])
        for week in plan.get("weeks") or []
        for r in week.get("resources") or []
        if r.get("url")
    ]


def grounding_problems(
    result: Mapping[str, Any],
    known_urls: Iterable[str],
    *,
    links: Iterable[str] = (),
    grounded: bool | None = None,
    has_content: bool = True,
) -> list[str]:
    """What is wrong with the `sources` and `notice` of an agent's result.

    `links` are the URLs the result uses (a plan's resources). `has_content` is False
    for a result with nothing to ground (a plan with no weeks). `grounded`, when given,
    is the agent's own flag (the interview agent has one).

    An empty list means: no invented link, every source is used, sources and notice
    never appear together, and an answer without sources carries the notice.
    """
    known = set(known_urls)
    used = set(links)
    sources = {str(s.get("url", "")) for s in result.get("sources") or []}
    notice = result.get("notice")
    problems: list[str] = []

    if used - known:
        problems.append(
            f"links that are not in the knowledge base: {sorted(used - known)}"
        )
    if sources - known:
        problems.append(
            f"sources that are not in the knowledge base: {sorted(sources - known)}"
        )
    if links and sources - used:
        problems.append(
            f"sources that the answer does not use: {sorted(sources - used)}"
        )
    if used and not sources:
        problems.append("the answer has links but lists no sources")
    if sources and notice:
        problems.append("the answer has both sources and a notice")
    if has_content and not sources and not notice and grounded is not True:
        problems.append("an answer with no sources must carry the notice")
    if grounded is True and not sources:
        problems.append("the answer says it is grounded but lists no sources")
    if grounded is True and notice:
        problems.append("a grounded answer must not carry the notice")
    return problems
