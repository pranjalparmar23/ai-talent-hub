"""The ingest command: fill the knowledge collections (Phase 7, task 8).

    python -m app.ingest.run --source tech-interview-handbook
    python -m app.ingest.run --source roadmap-sh
    python -m app.ingest.run --source all
    python -m app.ingest.run --source all --dry-run

One run, for each chosen source:

1. Ask the source adapter for its documents.
2. Store them with the pipeline. Unchanged pages are not embedded again; only their
   dates are refreshed, so a re-run is cheap and good content does not expire.
3. Remove that source's chunks that are past their expiry date.
4. Print a report. The exit code is 0 when nothing failed, 1 when a document or a
   whole source failed, and 2 for a bad argument.

`--dry-run` fetches and checks licences, and lists what is expired. It writes and
deletes nothing.

`--enable-server NAME` switches an MCP server on for this run only, in memory. The
bundled mcp_servers.yaml is not changed, so servers stay off by default.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from app.ingest.expiry import KNOWLEDGE_COLLECTION_NAMES, ExpiryReport, remove_expired
from app.ingest.licence import check_licence
from app.ingest.pipeline import (
    ADDED,
    FAILED,
    REFUSED,
    SKIPPED,
    UNCHANGED,
    UPDATED,
    IngestSummary,
)
from app.ingest.sources.base import Skipped, SourceError
from app.mcp.config import ServerConfig, load_registry
from app.mcp.gateway import MCPGateway

logger = logging.getLogger(__name__)

# How many detail lines one report prints per group before it says "and N more".
MAX_DETAIL_LINES = 15

STATUS_ORDER = (ADDED, UPDATED, UNCHANGED, REFUSED, SKIPPED, FAILED)


# ── the sources this command knows ───────────────────────────


@dataclass(frozen=True)
class SourceSpec:
    name: str
    build: Callable[[MCPGateway], Any]  # returns a SourceAdapter
    servers: tuple[str, ...] = ()  # MCP servers it needs (must be enabled)


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


def _build_roadmap(gateway: MCPGateway):
    from app.ingest.sources.link_only import LinkOnlyAdapter

    return LinkOnlyAdapter()


SOURCES: dict[str, SourceSpec] = {
    "tech-interview-handbook": SourceSpec(
        "tech-interview-handbook", _build_handbook, servers=("github",)
    ),
    "roadmap-sh": SourceSpec("roadmap-sh", _build_roadmap),
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


async def run_source(
    spec: SourceSpec,
    *,
    gateway: MCPGateway,
    pipeline: Any,
    store: Any,
    dry_run: bool = False,
    expire: bool = True,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> SourceRun:
    """Fetch, store and expire one source. Problems go into the result, not raised."""
    run = SourceRun(source=spec.name, dry_run=dry_run)

    problem = _disabled_servers_message(spec, gateway.registry)
    if problem:
        run.error = problem
        return run

    try:
        adapter = spec.build(gateway)
        batch = await adapter.fetch()
    except Exception as err:  # a bad source must not stop "--source all"
        logger.exception("Source %s failed", spec.name)
        # A SourceError already carries a plain-words message.
        run.error = (
            str(err) if isinstance(err, SourceError) else f"{type(err).__name__}: {err}"
        )
        return run

    run.docs_fetched = len(batch.docs)
    run.adapter_skipped = list(batch.skipped)

    if dry_run:
        for doc in batch.docs:
            decision = check_licence(doc)
            if decision.allowed:
                run.would_store += 1
            else:
                run.would_refuse.append((doc.url, decision.reason))
    else:
        run.summary = pipeline.ingest_many(batch.docs)

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
) -> list[SourceRun]:
    runs = []
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
            )
        )
    return runs


# ── the report ───────────────────────────────────────────────


def _capped(lines: list[str]) -> list[str]:
    if len(lines) <= MAX_DETAIL_LINES:
        return lines
    more = len(lines) - MAX_DETAIL_LINES
    return lines[:MAX_DETAIL_LINES] + [f"    ... and {more} more"]


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
