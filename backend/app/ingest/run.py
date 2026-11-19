"""The ingest command: fill the knowledge collections (Phase 7, task 8).

    python -m app.ingest.run --source tech-interview-handbook
    python -m app.ingest.run --source roadmap-sh
    python -m app.ingest.run --source official-docs
    python -m app.ingest.run --source freecodecamp
    python -m app.ingest.run --source all
    python -m app.ingest.run --source all --dry-run

One run, for each chosen source:

1. Ask the source adapter for its documents.
2. Link sources only (roadmap-sh, official-docs, freecodecamp): ask each site whether
   the page is still there (app/ingest/linkcheck.py). A link the site says is gone
   (404 or 410) is left out, and any copy of it stored earlier is removed. A link that
   cannot be judged (timeout, 403, 429, no network) is kept. `--no-link-check` skips
   this step.
3. Store the documents with the pipeline. Unchanged pages are not embedded again; only
   their dates are refreshed, so a re-run is cheap and good content does not expire.
4. Remove that source's chunks that are past their expiry date.
5. Print a report. The exit code is 0 when nothing failed, 1 when a document or a
   whole source failed, and 2 for a bad argument. Broken links are reported, but they
   do not change the exit code: a dead page upstream is not a failure of this run.

`--dry-run` fetches, checks links and licences, and lists what is expired and which
stored copies of broken links would go. It writes and deletes nothing. It still sends
the link-check requests, which only read.

Link sources in one run share one set of addresses, so a page that two lists both name
is stored once, for the list that comes first in SOURCES.

`--enable-server NAME` switches an MCP server on for this run only, in memory. The
bundled mcp_servers.yaml is not changed, so servers stay off by default.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from app.ingest.expiry import KNOWLEDGE_COLLECTION_NAMES, ExpiryReport, remove_expired
from app.ingest.licence import check_licence
from app.ingest.linkcheck import LinkResult, check_links, count_by_status
from app.ingest.pipeline import (
    ADDED,
    FAILED,
    REFUSED,
    SKIPPED,
    UNCHANGED,
    UPDATED,
    IngestSummary,
)
from app.ingest.schema import KnowledgeDoc
from app.ingest.sources.base import Skipped, SourceError
from app.mcp.config import ServerConfig, load_registry
from app.mcp.gateway import MCPGateway

logger = logging.getLogger(__name__)

# How many detail lines one report prints per group before it says "and N more".
MAX_DETAIL_LINES = 15

STATUS_ORDER = (ADDED, UPDATED, UNCHANGED, REFUSED, SKIPPED, FAILED)

# Takes a list of addresses and returns {address: LinkResult} (see linkcheck.check_links).
LinkChecker = Callable[[Sequence[str]], Awaitable[dict[str, LinkResult]]]


# ── the sources this command knows ───────────────────────────


@dataclass(frozen=True)
class SourceSpec:
    name: str
    build: Callable[[MCPGateway], Any]  # returns a SourceAdapter
    servers: tuple[str, ...] = ()  # MCP servers it needs (must be enabled)
    link_only: bool = False  # its documents are links: run the link check on them


def _build_handbook(gateway: MCPGateway):
    from app.ingest.sources.readers import GatewayGitHubReader
    from app.ingest.sources.tech_interview_handbook import (
        OWNER,
        REPO,
        TechInterviewHandbookAdapter,
    )

    return TechInterviewHandbookAdapter(
        GatewayGitHubReader(OWNER, REPO, gateway=gateway)
    )


def _link_list(filename: str) -> Callable[[MCPGateway], Any]:
    """A builder for a link list kept in app/ingest/sources/data/. It needs no server."""

    def build(gateway: MCPGateway):
        from app.ingest.sources.link_only import DATA_DIR, LinkOnlyAdapter

        return LinkOnlyAdapter(DATA_DIR / filename)

    return build


# The order matters for link sources: when two lists name the same page, the one that
# comes first keeps it.
SOURCES: dict[str, SourceSpec] = {
    "tech-interview-handbook": SourceSpec(
        "tech-interview-handbook", _build_handbook, servers=("github",)
    ),
    "roadmap-sh": SourceSpec(
        "roadmap-sh", _link_list("roadmap_sh.yaml"), link_only=True
    ),
    "official-docs": SourceSpec(
        "official-docs", _link_list("official_docs.yaml"), link_only=True
    ),
    "freecodecamp": SourceSpec(
        "freecodecamp", _link_list("freecodecamp.yaml"), link_only=True
    ),
}


# ── the result of one source ─────────────────────────────────


@dataclass
class SourceRun:
    source: str
    dry_run: bool = False
    docs_fetched: int = 0
    adapter_skipped: list[Skipped] = field(default_factory=list)
    summary: IngestSummary | None = None  # not set on a dry run
    would_store: int = 0  # dry run only
    would_refuse: list[tuple[str, str]] = field(default_factory=list)  # dry run only
    expiry: ExpiryReport | None = None
    expiry_note: str = ""
    error: str = ""
    # Link check (link sources only). `link_counts` is None when no check ran.
    link_counts: dict[str, int] | None = None
    links_broken: list[LinkResult] = field(default_factory=list)  # left out
    links_unknown: list[LinkResult] = field(default_factory=list)  # kept
    link_note: str = ""  # why the check did not run
    stale_chunks: int = 0  # stored chunks of broken links (removed, or would be)

    @property
    def ok(self) -> bool:
        return not self.error and (self.summary is None or self.summary.ok)


# ── building the gateway ─────────────────────────────────────


def build_gateway(
    enable_servers: Sequence[str] = (),
    registry: dict[str, ServerConfig] | None = None,
) -> MCPGateway:
    """A gateway where the named servers are switched on, for this process only."""
    servers = dict(registry if registry is not None else load_registry())
    for name in enable_servers:
        if name not in servers:
            known = ", ".join(sorted(servers)) or "none"
            raise ValueError(f"unknown MCP server '{name}'. Known servers: {known}")
        servers[name] = replace(servers[name], enabled=True)
    return MCPGateway(servers)


def _disabled_servers_message(spec: SourceSpec, registry: dict[str, ServerConfig]):
    bad = [n for n in spec.servers if n not in registry or not registry[n].enabled]
    if not bad:
        return ""
    flags = " ".join(f"--enable-server {n}" for n in bad)
    return (
        f"needs the MCP server(s) {', '.join(bad)}, which are off or missing in "
        f"mcp_servers.yaml. Run again with: {flags}"
    )


# ── running ──────────────────────────────────────────────────


def _knowledge_collections_exist(store: Any) -> bool:
    return set(KNOWLEDGE_COLLECTION_NAMES) <= set(store.list_collections())


async def _check_links(
    run: SourceRun, docs: list[KnowledgeDoc], checker: LinkChecker
) -> tuple[list[KnowledgeDoc], list[KnowledgeDoc]]:
    """Ask the sites about the documents' links. Returns (kept, broken documents).

    A check that fails as a whole is not allowed to stop the run: nothing is dropped.
    """
    try:
        results = await checker([d.url for d in docs])
    except Exception as err:
        logger.exception("Link check failed for %s", run.source)
        run.link_note = f"not checked: {type(err).__name__}: {err}"
        return docs, []

    run.link_counts = count_by_status(results.values())
    run.links_broken = [r for r in results.values() if not r.keep]
    run.links_unknown = [r for r in results.values() if r.status.value == "unknown"]
    gone = {r.url for r in run.links_broken}
    kept = [d for d in docs if d.url not in gone]
    return kept, [d for d in docs if d.url in gone]


def _stored_chunk_ids(doc: KnowledgeDoc, store: Any) -> list[str]:
    rows = store.get_by_where(doc.collection, {"doc_id": doc.doc_id})
    return [r["id"] for r in rows]


def _remove_stale_copies(
    run: SourceRun, broken: list[KnowledgeDoc], store: Any, dry_run: bool
) -> None:
    """A page that was fine on an earlier run may be gone now. Take its chunks out,
    so a learning plan never points at it."""
    if dry_run and not _knowledge_collections_exist(store):
        return
    for doc in broken:
        ids = _stored_chunk_ids(doc, store)
        if not ids:
            continue
        run.stale_chunks += len(ids)
        if not dry_run:
            store.delete_ids(doc.collection, ids)


async def run_source(
    spec: SourceSpec,
    *,
    gateway: MCPGateway,
    pipeline: Any,
    store: Any,
    dry_run: bool = False,
    expire: bool = True,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    link_check: bool = True,
    link_checker: LinkChecker = check_links,
    seen_urls: set[str] | None = None,
) -> SourceRun:
    """Fetch, store and expire one source. Problems go into the result, not raised.

    `link_checker` and `seen_urls` only matter for link sources (`spec.link_only`).
    """
    run = SourceRun(source=spec.name, dry_run=dry_run)

    problem = _disabled_servers_message(spec, gateway.registry)
    if problem:
        run.error = problem
        return run

    try:
        adapter = spec.build(gateway)
        if spec.link_only and seen_urls is not None:
            adapter.seen_urls = seen_urls  # share "already listed" with the other lists
        batch = await adapter.fetch()
    except Exception as err:  # a bad source must not stop "--source all"
        logger.exception("Source %s failed", spec.name)
        # A SourceError already carries a plain-words message.
        run.error = (
            str(err) if isinstance(err, SourceError) else f"{type(err).__name__}: {err}"
        )
        return run

    docs = list(batch.docs)
    run.docs_fetched = len(docs)
    run.adapter_skipped = list(batch.skipped)

    broken: list[KnowledgeDoc] = []
    if spec.link_only and link_check and docs:
        docs, broken = await _check_links(run, docs, link_checker)
    elif spec.link_only and not link_check:
        run.link_note = "not checked (--no-link-check)"

    if broken:
        try:
            _remove_stale_copies(run, broken, store, dry_run)
        except Exception as err:
            logger.exception("Removing stale links failed for %s", spec.name)
            run.error = f"could not remove stored copies of broken links: {err}"

    if dry_run:
        for doc in docs:
            decision = check_licence(doc)
            if decision.allowed:
                run.would_store += 1
            else:
                run.would_refuse.append((doc.url, decision.reason))
    else:
        run.summary = pipeline.ingest_many(docs)

    if expire:
        try:
            if dry_run and not _knowledge_collections_exist(store):
                run.expiry_note = "the collections do not exist yet, so nothing expired"
            else:
                run.expiry = remove_expired(
                    now=now_fn(),
                    source_ids=[adapter.source_id],
                    dry_run=dry_run,
                    store=store,
                )
        except Exception as err:
            logger.exception("Expiry sweep failed for %s", spec.name)
            run.error = f"expiry sweep failed: {type(err).__name__}: {err}"
    return run


async def run_all(
    specs: Sequence[SourceSpec],
    *,
    gateway: MCPGateway,
    pipeline: Any,
    store: Any,
    dry_run: bool = False,
    expire: bool = True,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    link_check: bool = True,
    link_checker: LinkChecker = check_links,
) -> list[SourceRun]:
    runs = []
    seen_urls: set[str] = set()  # one set for the whole run: a page is stored once
    for spec in specs:
        runs.append(
            await run_source(
                spec,
                gateway=gateway,
                pipeline=pipeline,
                store=store,
                dry_run=dry_run,
                expire=expire,
                now_fn=now_fn,
                link_check=link_check,
                link_checker=link_checker,
                seen_urls=seen_urls,
            )
        )
    return runs


# ── the report ───────────────────────────────────────────────


def _capped(lines: list[str]) -> list[str]:
    if len(lines) <= MAX_DETAIL_LINES:
        return lines
    more = len(lines) - MAX_DETAIL_LINES
    return lines[:MAX_DETAIL_LINES] + [f"    ... and {more} more"]


def _problem_link(kind: str, r: LinkResult) -> str:
    """One report line for a link that is broken or could not be judged. When the site
    sent the visitor somewhere else, the line says where, so the list can be updated."""
    moved = f" (redirected to {r.final_url})" if r.final_url else ""
    return f"    {kind:8s} {r.url}: {r.reason}{moved}"


def _link_lines(run: SourceRun) -> list[str]:
    """The link-check part of the report. Empty for sources that are not links."""
    if run.link_counts is None:
        return [f"  links:    {run.link_note}"] if run.link_note else []

    c = run.link_counts
    lines = [
        f"  links:    checked {sum(c.values())}: ok {c['ok']}, "
        f"broken {c['broken']} (left out), unknown {c['unknown']} (kept)"
    ]
    lines.extend(_capped([_problem_link("broken", r) for r in run.links_broken]))
    lines.extend(_capped([_problem_link("unknown", r) for r in run.links_unknown]))
    if run.stale_chunks:
        verb = "would be removed" if run.dry_run else "removed"
        lines.append(
            f"  stale:    {run.stale_chunks} stored chunks of broken links {verb}"
        )
    return lines


def format_report(run: SourceRun) -> str:
    lines = [
        run.source + ("  (dry run: nothing written or deleted)" if run.dry_run else "")
    ]

    if run.error:
        lines.append(f"  PROBLEM: {run.error}")

    if run.docs_fetched or not run.error:
        left_out = f" ({len(run.adapter_skipped)} left out by the adapter)"
        lines.append(f"  fetched:  {run.docs_fetched} documents" + left_out)

    if run.summary is not None:
        counts = run.summary.counts
        parts = [f"{s} {counts.get(s, 0)}" for s in STATUS_ORDER]
        lines.append("  stored:   " + ", ".join(parts))
        details = [
            f"    {r.status:8s} {r.url}: {r.reason}"
            for r in run.summary.results
            if r.status in (REFUSED, SKIPPED, FAILED)
        ]
        lines.extend(_capped(details))
    elif run.docs_fetched:
        lines.append(
            f"  would store {run.would_store}, would refuse {len(run.would_refuse)}"
        )
        lines.extend(_capped([f"    refused  {u}: {r}" for u, r in run.would_refuse]))

    if run.adapter_skipped:
        left = [f"    left out {s.path}: {s.reason}" for s in run.adapter_skipped]
        lines.extend(_capped(left))

    lines.extend(_link_lines(run))

    if run.expiry is not None:
        e = run.expiry
        if run.dry_run:
            lines.append(
                f"  expired:  {e.documents} documents ({e.chunks_found} chunks) "
                "would be removed"
            )
            expired = [f"    expired  {d.url or d.doc_id}" for d in e.docs]
            lines.extend(_capped(expired))
        else:
            lines.append(
                f"  expired:  {e.documents} documents removed "
                f"({e.chunks_deleted} chunks)"
            )
    elif run.expiry_note:
        lines.append(f"  expired:  {run.expiry_note}")

    return "\n".join(lines)


# ── the command line ─────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingest.run",
        description="Fill the knowledge collections from one source or all of them.",
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=[*SOURCES, "all"],
        help="which source to ingest",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and check licences; write and delete nothing",
    )
    parser.add_argument(
        "--no-expire",
        dest="expire",
        action="store_false",
        help="do not remove chunks that are past their expiry date",
    )
    parser.add_argument(
        "--no-link-check",
        dest="link_check",
        action="store_false",
        help="do not ask the sites whether link-only pages still exist",
    )
    parser.add_argument(
        "--enable-server",
        action="append",
        default=[],
        metavar="NAME",
        help="switch an MCP server on for this run only (for example: github)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command. Uses its own event loop, so it is safe to call from tests."""
    args = build_parser().parse_args(argv)
    specs = list(SOURCES.values()) if args.source == "all" else [SOURCES[args.source]]

    try:
        gateway = build_gateway(args.enable_server)
    except ValueError as err:
        print(f"Error: {err}")
        return 2

    from app.rag.vector_store import VectorStore

    try:
        VectorStore.heartbeat()
    except Exception as err:
        print(f"Cannot reach ChromaDB: {err}")
        print("Check CHROMA_HOST and CHROMA_PORT in backend/.env, and that the")
        print("container is running:  docker compose ps chromadb")
        return 1

    pipeline = None
    if not args.dry_run:
        VectorStore.init_all_collections()
        from app.ingest.pipeline import IngestPipeline

        pipeline = IngestPipeline()

    # A private loop: asyncio.run() would reset the "current" loop to None.
    loop = asyncio.new_event_loop()
    try:
        runs = loop.run_until_complete(
            run_all(
                specs,
                gateway=gateway,
                pipeline=pipeline,
                store=VectorStore,
                dry_run=args.dry_run,
                expire=args.expire,
                link_check=args.link_check,
            )
        )
    finally:
        loop.close()

    print("\n\n".join(format_report(r) for r in runs))
    all_ok = all(r.ok for r in runs)
    print(
        "\nDone."
        if all_ok
        else "\nFinished with problems (see PROBLEM and failed above)."
    )
    return 0 if all_ok else 1


def _silence_chroma_telemetry() -> None:
    try:
        import posthog

        posthog.capture = lambda *args, **kwargs: None
        posthog.Posthog.capture = lambda *args, **kwargs: None
    except ImportError:
        pass


if __name__ == "__main__":
    # Must come before chromadb is imported (it is imported inside main()).
    _silence_chroma_telemetry()
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    sys.exit(main())
