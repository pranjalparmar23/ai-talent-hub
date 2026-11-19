"""Tests for the ingest command (app/ingest/run.py).

No ChromaDB, no MCP server, no network. Fake adapters return ready-made documents,
a fake pipeline records what it is given, and a fake store stands in for ChromaDB.
The tests for main() put a fake `app.rag.vector_store` module in place, so they run
even where chromadb is not installed.
"""

import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from app.ingest import pipeline as pipeline_module
from app.ingest import run as run_module
from app.ingest.pipeline import ADDED, FAILED, REFUSED, SKIPPED, UNCHANGED
from app.ingest.pipeline import IngestResult, IngestSummary
from app.ingest.run import (
    MAX_DETAIL_LINES,
    SourceRun,
    SourceSpec,
    build_gateway,
    format_report,
    main,
    run_all,
    run_source,
)
from app.ingest.expiry import ExpiredDoc, ExpiryReport
from app.ingest.schema import INTERVIEW_COLLECTION, LEARNING_COLLECTION, KnowledgeDoc
from app.ingest.sources.base import SourceBatch, SourceError, Skipped
from app.mcp.config import load_registry
from tests.unit.ingest.test_expiry import FUTURE, PAST, FakeStore, chunk, put

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc)


# ── fakes ────────────────────────────────────────────────────


def doc(url="https://example.com/a", **over) -> KnowledgeDoc:
    data = dict(
        source_id="src-a",
        url=url,
        title="A page",
        domain="software-engineering",
        content_type="experience",
        licence="MIT",
        attribution="Credit line",
        fetched_at=NOW,
        expires_at=NOW + timedelta(days=90),
        text="Some words about interviews. " * 20,
    )
    data.update(over)
    return KnowledgeDoc(**data)


class FakeAdapter:
    def __init__(self, docs=(), skipped=(), source_id="src-a", error=None):
        self.source_id = source_id
        self.docs = list(docs)
        self.skipped = list(skipped)
        self.error = error
        self.fetches = 0

    async def fetch(self):
        self.fetches += 1
        if self.error:
            raise self.error
        return SourceBatch(self.source_id, list(self.docs), list(self.skipped))


class FakePipeline:
    """Records every batch. `outcomes` maps a url to (status, reason)."""

    def __init__(self, outcomes=None):
        self.outcomes = outcomes or {}
        self.batches: list[list[KnowledgeDoc]] = []

    def ingest_many(self, docs):
        docs = list(docs)
        self.batches.append(docs)
        results = []
        for d in docs:
            status, reason = self.outcomes.get(d.url, (ADDED, ""))
            results.append(IngestResult(d.doc_id, d.url, status, reason=reason))
        return IngestSummary(results)


class AdminStore(FakeStore):
    """A FakeStore that also answers the questions main() asks of VectorStore."""

    def __init__(self, reachable=True, existing=None):
        super().__init__()
        self.reachable = reachable
        self.existing = (
            [INTERVIEW_COLLECTION, LEARNING_COLLECTION]
            if existing is None
            else existing
        )
        self.initialised = 0

    def heartbeat(self):
        if not self.reachable:
            raise ConnectionError("connection refused")
        return 1

    def init_all_collections(self):
        self.initialised += 1
        return list(self.existing)

    def list_collections(self):
        return list(self.existing)


def gateway(**servers):
    """A stand-in gateway: only the registry is read. servers = {name: enabled}."""
    registry = {n: types.SimpleNamespace(enabled=on) for n, on in servers.items()}
    return types.SimpleNamespace(registry=registry)


def spec(adapter, name="src-a", servers=()):
    built = []

    def build(gw):
        built.append(gw)
        if isinstance(adapter, Exception):
            raise adapter
        return adapter

    build.built = built  # type: ignore[attr-defined]  # which gateways it got
    return SourceSpec(name, build, servers=tuple(servers))


def call(s, *, pipeline=None, store=None, gw=None, **kwargs):
    kwargs.setdefault("now_fn", lambda: NOW)
    return run_source(
        s,
        gateway=gw or gateway(),
        pipeline=pipeline if pipeline is not None else FakePipeline(),
        store=store if store is not None else AdminStore(),
        **kwargs,
    )


# ── build_gateway ────────────────────────────────────────────


def test_the_bundled_github_server_is_off_by_default():
    assert load_registry()["github"].enabled is False
    assert build_gateway().registry["github"].enabled is False


def test_enable_server_turns_it_on_for_that_gateway_only():
    original = load_registry()
    gw = build_gateway(["github"], registry=original)
    assert gw.registry["github"].enabled is True
    assert original["github"].enabled is False  # the loaded registry is unchanged


def test_other_servers_stay_as_they_were():
    gw = build_gateway(["github"])
    others = [n for n in gw.registry if n != "github"]
    assert others
    assert all(gw.registry[n].enabled == load_registry()[n].enabled for n in others)


def test_an_unknown_server_name_is_an_error_that_lists_the_known_ones():
    with pytest.raises(ValueError) as caught:
        build_gateway(["nope"])
    assert "nope" in str(caught.value)
    assert "github" in str(caught.value)


def test_the_yaml_file_is_not_touched_by_enabling_a_server():
    build_gateway(["github"])
    assert load_registry()["github"].enabled is False


# ── servers a source needs ───────────────────────────────────


async def test_a_source_that_needs_a_disabled_server_stops_early():
    adapter = FakeAdapter([doc()])
    s = spec(adapter, servers=["github"])
    pipeline = FakePipeline()
    result = await call(s, gw=gateway(github=False), pipeline=pipeline)
    assert not result.ok
    assert "github" in result.error
    assert "--enable-server github" in result.error
    assert s.build.built == []  # the adapter was never built
    assert adapter.fetches == 0
    assert pipeline.batches == []


async def test_a_source_that_needs_a_server_missing_from_the_registry_stops_early():
    s = spec(FakeAdapter([doc()]), servers=["github"])
    result = await call(s, gw=gateway())
    assert "--enable-server github" in result.error


async def test_a_source_runs_when_its_server_is_enabled():
    s = spec(FakeAdapter([doc()]), servers=["github"])
    result = await call(s, gw=gateway(github=True))
    assert result.ok
    assert result.docs_fetched == 1


# ── a normal run ─────────────────────────────────────────────


async def test_documents_go_to_the_pipeline_and_the_summary_is_kept():
    docs = [doc("https://example.com/a"), doc("https://example.com/b")]
    pipeline = FakePipeline()
    result = await call(spec(FakeAdapter(docs)), pipeline=pipeline)
    assert [d.url for d in pipeline.batches[0]] == [d.url for d in docs]
    assert result.docs_fetched == 2
    assert result.summary.counts == {ADDED: 2}
    assert result.ok
    assert result.dry_run is False


async def test_what_the_adapter_left_out_is_passed_on():
    left_out = [Skipped("a.md", "too short")]
    result = await call(spec(FakeAdapter([doc()], skipped=left_out)))
    assert result.adapter_skipped == left_out


async def test_the_adapter_is_built_with_the_gateway():
    s = spec(FakeAdapter([doc()]))
    gw = gateway()
    await call(s, gw=gw)
    assert s.build.built == [gw]


async def test_a_refused_document_is_not_a_failure():
    outcomes = {"https://example.com/a": (REFUSED, "licence GPL-3.0 is not allowed")}
    result = await call(spec(FakeAdapter([doc()])), pipeline=FakePipeline(outcomes))
    assert result.summary.counts == {REFUSED: 1}
    assert result.ok


async def test_a_failed_document_makes_the_run_not_ok():
    outcomes = {"https://example.com/a": (FAILED, "RuntimeError: model is down")}
    result = await call(spec(FakeAdapter([doc()])), pipeline=FakePipeline(outcomes))
    assert not result.ok
    assert result.error == ""  # the source itself worked


# ── a source that cannot be read ─────────────────────────────


async def test_a_source_error_is_reported_in_its_own_words():
    adapter = FakeAdapter(error=SourceError("the licence file is not MIT"))
    pipeline = FakePipeline()
    result = await call(spec(adapter), pipeline=pipeline)
    assert result.error == "the licence file is not MIT"
    assert not result.ok
    assert result.summary is None
    assert pipeline.batches == []


async def test_any_other_error_names_its_type():
    result = await call(spec(FakeAdapter(error=KeyError("x"))))
    assert result.error == "KeyError: 'x'"


async def test_an_error_while_building_the_adapter_is_reported_too():
    result = await call(spec(SourceError("cannot read the link list")))
    assert result.error == "cannot read the link list"
    assert not result.ok


async def test_no_expiry_sweep_happens_when_the_source_could_not_be_read():
    store = AdminStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0))
    result = await call(spec(FakeAdapter(error=SourceError("down"))), store=store)
    assert result.expiry is None
    assert store.filters == []
    assert store.deletes == []


# ── expiry ───────────────────────────────────────────────────


async def test_expired_chunks_of_this_source_are_removed_after_storing():
    store = AdminStore()
    put(store, LEARNING_COLLECTION, chunk("gone", 0, source="src-a", expires=PAST))
    put(store, LEARNING_COLLECTION, chunk("kept", 0, source="src-a", expires=FUTURE))
    put(store, LEARNING_COLLECTION, chunk("other", 0, source="src-b", expires=PAST))
    result = await call(spec(FakeAdapter([doc()])), store=store)
    assert result.expiry.chunks_deleted == 1
    assert sorted(store.collections[LEARNING_COLLECTION]) == [
        "kept:0000",
        "other:0000",
    ]


async def test_the_sweep_uses_the_source_id_of_the_adapter():
    store = AdminStore()
    adapter = FakeAdapter([doc(source_id="real-id")], source_id="real-id")
    await call(spec(adapter, name="cli-name"), store=store)
    assert "real-id" in str(store.filters)
    assert "cli-name" not in str(store.filters)


async def test_the_sweep_uses_the_clock_it_is_given():
    store = AdminStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, expires=PAST))
    earlier = datetime.fromtimestamp(PAST - 10, tz=timezone.utc)
    result = await call(spec(FakeAdapter([doc()])), store=store, now_fn=lambda: earlier)
    assert result.expiry.chunks_deleted == 0


async def test_no_expire_skips_the_sweep():
    store = AdminStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, expires=PAST))
    result = await call(spec(FakeAdapter([doc()])), store=store, expire=False)
    assert result.expiry is None
    assert store.filters == []
    assert "aaaa:0000" in store.collections[LEARNING_COLLECTION]


async def test_a_failing_sweep_is_reported_but_keeps_the_stored_summary():
    class BrokenStore(AdminStore):
        def get_by_where(self, collection_name, where):
            raise RuntimeError("chroma went away")

    result = await call(spec(FakeAdapter([doc()])), store=BrokenStore())
    assert result.summary.counts == {ADDED: 1}
    assert "expiry sweep failed" in result.error
    assert "chroma went away" in result.error
    assert not result.ok


# ── dry run ──────────────────────────────────────────────────


async def test_a_dry_run_reports_what_it_would_do_and_deletes_nothing():
    store = AdminStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, expires=PAST))
    result = await call(spec(FakeAdapter([doc()])), store=store, dry_run=True)
    assert result.summary is None
    assert result.dry_run is True
    assert result.would_store == 1
    assert store.deletes == []


async def test_a_dry_run_gives_the_pipeline_no_documents():
    pipeline = FakePipeline()
    await call(spec(FakeAdapter([doc()])), pipeline=pipeline, dry_run=True)
    assert pipeline.batches == []


async def test_a_dry_run_counts_documents_the_licence_gate_would_refuse():
    docs = [
        doc("https://example.com/a"),
        doc("https://example.com/b", licence="GPL-3.0"),
    ]
    result = await call(spec(FakeAdapter(docs)), dry_run=True)
    assert result.would_store == 1
    assert [url for url, _ in result.would_refuse] == ["https://example.com/b"]
    assert result.would_refuse[0][1]  # a reason is given


async def test_a_dry_run_lists_expired_chunks_and_deletes_none():
    store = AdminStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, expires=PAST))
    result = await call(spec(FakeAdapter([doc()])), store=store, dry_run=True)
    assert result.expiry.dry_run is True
    assert result.expiry.documents == 1
    assert store.deletes == []
    assert "aaaa:0000" in store.collections[LEARNING_COLLECTION]


async def test_a_dry_run_before_the_collections_exist_says_so():
    store = AdminStore(existing=[])
    result = await call(spec(FakeAdapter([doc()])), store=store, dry_run=True)
    assert result.expiry is None
    assert "do not exist yet" in result.expiry_note
    assert store.filters == []
    assert result.ok


# ── several sources ──────────────────────────────────────────


async def test_sources_run_in_order_and_one_failure_does_not_stop_the_rest():
    broken = spec(FakeAdapter(error=SourceError("down")), name="first")
    fine = spec(FakeAdapter([doc()], source_id="second"), name="second")
    results = await run_all(
        [broken, fine],
        gateway=gateway(),
        pipeline=FakePipeline(),
        store=AdminStore(),
        now_fn=lambda: NOW,
    )
    assert [r.source for r in results] == ["first", "second"]
    assert [r.ok for r in results] == [False, True]


async def test_run_all_passes_its_options_to_every_source():
    store = AdminStore()
    results = await run_all(
        [spec(FakeAdapter([doc()])), spec(FakeAdapter([doc()], source_id="b"), "b")],
        gateway=gateway(),
        pipeline=None,
        store=store,
        dry_run=True,
        expire=False,
    )
    assert all(r.dry_run for r in results)
    assert all(r.expiry is None for r in results)
    assert store.filters == []


# ── the report ───────────────────────────────────────────────


def summary(*pairs) -> IngestSummary:
    """pairs: (url, status, reason)"""
    return IngestSummary([IngestResult("id", u, s, reason=r) for u, s, r in pairs])


def test_the_report_shows_every_count_even_when_zero():
    result = SourceRun(
        "src-a",
        docs_fetched=2,
        summary=summary(("u1", ADDED, ""), ("u2", UNCHANGED, "")),
        expiry=ExpiryReport(),
    )
    text = format_report(result)
    assert text.splitlines()[0] == "src-a"
    assert "fetched:  2 documents (0 left out by the adapter)" in text
    assert "added 1, updated 0, unchanged 1, refused 0, skipped 0, failed 0" in text
    assert "expired:  0 documents removed (0 chunks)" in text


def test_refused_skipped_and_failed_documents_are_listed_with_their_reasons():
    result = SourceRun(
        "src-a",
        docs_fetched=3,
        summary=summary(
            ("https://x/1", REFUSED, "licence GPL is not allowed"),
            ("https://x/2", SKIPPED, "no text to store"),
            ("https://x/3", FAILED, "RuntimeError: model is down"),
            ("https://x/4", ADDED, ""),
        ),
    )
    text = format_report(result)
    assert "refused  https://x/1: licence GPL is not allowed" in text
    assert "skipped  https://x/2: no text to store" in text
    assert "failed   https://x/3: RuntimeError: model is down" in text
    assert "https://x/4" not in text  # added documents are not listed


def test_what_the_adapter_left_out_is_listed():
    result = SourceRun(
        "src-a",
        docs_fetched=1,
        adapter_skipped=[Skipped("mock-interviews.md", "on the skip list")],
        summary=summary(),
    )
    text = format_report(result)
    assert "(1 left out by the adapter)" in text
    assert "left out mock-interviews.md: on the skip list" in text


def test_a_long_list_of_details_is_cut_and_says_how_many_more():
    many = [(f"https://x/{i}", FAILED, "boom") for i in range(MAX_DETAIL_LINES + 4)]
    text = format_report(
        SourceRun("src-a", docs_fetched=len(many), summary=summary(*many))
    )
    assert text.count("failed   https://x/") == MAX_DETAIL_LINES
    assert "... and 4 more" in text


def test_a_problem_is_shown_and_the_fetched_line_is_left_out():
    text = format_report(SourceRun("src-a", error="the licence file is not MIT"))
    assert "PROBLEM: the licence file is not MIT" in text
    assert "fetched:" not in text


def test_a_dry_run_report_says_nothing_was_written():
    result = SourceRun(
        "src-a",
        dry_run=True,
        docs_fetched=3,
        would_store=2,
        would_refuse=[("https://x/1", "licence GPL is not allowed")],
        expiry=ExpiryReport(
            docs=[ExpiredDoc("c", "d1", "s", "https://x/old", ["d1:0", "d1:1"])],
            dry_run=True,
        ),
    )
    text = format_report(result)
    assert "dry run: nothing written or deleted" in text.splitlines()[0]
    assert "would store 2, would refuse 1" in text
    assert "refused  https://x/1: licence GPL is not allowed" in text
    assert "1 documents (2 chunks) would be removed" in text
    assert "expired  https://x/old" in text


def test_an_expiry_note_is_shown_in_place_of_the_expired_line():
    text = format_report(
        SourceRun(
            "src-a",
            dry_run=True,
            docs_fetched=1,
            would_store=1,
            expiry_note="the collections do not exist yet, so nothing expired",
        )
    )
    assert "expired:  the collections do not exist yet" in text


def test_a_real_run_report_shows_how_many_chunks_were_removed():
    report = ExpiryReport(
        docs=[ExpiredDoc("c", "d1", "s", "u", ["d1:0", "d1:1"])], chunks_deleted=2
    )
    text = format_report(
        SourceRun("src-a", docs_fetched=1, summary=summary(), expiry=report)
    )
    assert "expired:  1 documents removed (2 chunks)" in text


# ── the command line (main) ──────────────────────────────────


@pytest.fixture
def fake_chroma(monkeypatch):
    """Put a fake VectorStore in place of the real module."""
    store = AdminStore()
    module = types.ModuleType("app.rag.vector_store")
    module.VectorStore = store
    monkeypatch.setitem(sys.modules, "app.rag.vector_store", module)
    return store


@pytest.fixture
def fake_pipeline(monkeypatch):
    created = []

    class Recording(FakePipeline):
        def __init__(self):
            super().__init__()
            created.append(self)

    monkeypatch.setattr(pipeline_module, "IngestPipeline", Recording)
    return created


def only_source(monkeypatch, adapter, name="src-a", servers=()):
    s = spec(adapter, name=name, servers=servers)
    monkeypatch.setattr(run_module, "SOURCES", {name: s})
    return s


def test_a_missing_source_argument_exits_with_2():
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2


def test_an_unknown_source_exits_with_2(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--source", "nope"])
    assert caught.value.code == 2


def test_the_real_source_names_are_accepted_by_the_parser():
    parser = run_module.build_parser()
    for name in ("tech-interview-handbook", "roadmap-sh", "all"):
        assert parser.parse_args(["--source", name]).source == name


def test_the_defaults_are_a_real_run_with_expiry_and_no_servers_enabled():
    args = run_module.build_parser().parse_args(["--source", "all"])
    assert (args.dry_run, args.expire, args.enable_server) == (False, True, [])


def test_an_unknown_server_name_exits_with_2_before_touching_chroma(
    fake_chroma, monkeypatch, capsys
):
    only_source(monkeypatch, FakeAdapter([doc()]))
    assert main(["--source", "src-a", "--enable-server", "nope"]) == 2
    assert "unknown MCP server 'nope'" in capsys.readouterr().out
    assert fake_chroma.initialised == 0


def test_an_unreachable_chroma_exits_with_1_and_says_what_to_check(
    fake_chroma, fake_pipeline, monkeypatch, capsys
):
    adapter = FakeAdapter([doc()])
    only_source(monkeypatch, adapter)
    fake_chroma.reachable = False
    assert main(["--source", "src-a"]) == 1
    out = capsys.readouterr().out
    assert "Cannot reach ChromaDB" in out
    assert "CHROMA_HOST" in out
    assert adapter.fetches == 0
    assert fake_chroma.initialised == 0


def test_a_real_run_creates_the_collections_stores_and_exits_with_0(
    fake_chroma, fake_pipeline, monkeypatch, capsys
):
    only_source(monkeypatch, FakeAdapter([doc()]))
    assert main(["--source", "src-a"]) == 0
    out = capsys.readouterr().out
    assert fake_chroma.initialised == 1
    assert len(fake_pipeline[0].batches[0]) == 1
    assert "added 1" in out
    assert out.rstrip().endswith("Done.")


def test_a_dry_run_creates_no_collections_and_no_pipeline(
    fake_chroma, fake_pipeline, monkeypatch, capsys
):
    only_source(monkeypatch, FakeAdapter([doc()]))
    assert main(["--source", "src-a", "--dry-run"]) == 0
    assert fake_chroma.initialised == 0
    assert fake_pipeline == []
    assert "would store 1" in capsys.readouterr().out


def test_a_failed_source_exits_with_1(fake_chroma, fake_pipeline, monkeypatch, capsys):
    only_source(monkeypatch, FakeAdapter(error=SourceError("the source is down")))
    assert main(["--source", "src-a"]) == 1
    out = capsys.readouterr().out
    assert "PROBLEM: the source is down" in out
    assert "Finished with problems" in out


def test_a_failed_document_exits_with_1(
    fake_chroma, fake_pipeline, monkeypatch, capsys
):
    only_source(monkeypatch, FakeAdapter([doc()]))

    def failing(self, docs):
        return IngestSummary(
            [IngestResult("id", d.url, FAILED, reason="boom") for d in docs]
        )

    monkeypatch.setattr(FakePipeline, "ingest_many", failing)
    assert main(["--source", "src-a"]) == 1


def test_no_expire_is_passed_through(fake_chroma, fake_pipeline, monkeypatch):
    only_source(monkeypatch, FakeAdapter([doc()]))
    put(fake_chroma, LEARNING_COLLECTION, chunk("aaaa", 0, expires=PAST))
    assert main(["--source", "src-a", "--no-expire"]) == 0
    assert "aaaa:0000" in fake_chroma.collections[LEARNING_COLLECTION]


def test_all_runs_every_known_source_in_order(
    fake_chroma, fake_pipeline, monkeypatch, capsys
):
    first = spec(FakeAdapter([doc()], source_id="one"), name="one")
    second = spec(FakeAdapter([doc()], source_id="two"), name="two")
    monkeypatch.setattr(run_module, "SOURCES", {"one": first, "two": second})
    assert main(["--source", "all"]) == 0
    out = capsys.readouterr().out
    assert out.index("one") < out.index("two")


def test_enable_server_reaches_the_sources_through_the_gateway(
    fake_chroma, fake_pipeline, monkeypatch
):
    s = only_source(monkeypatch, FakeAdapter([doc()]))
    assert main(["--source", "src-a", "--enable-server", "github"]) == 0
    assert s.build.built[0].registry["github"].enabled is True


def test_without_the_flag_the_gateway_keeps_github_off(
    fake_chroma, fake_pipeline, monkeypatch
):
    s = only_source(monkeypatch, FakeAdapter([doc()]))
    assert main(["--source", "src-a"]) == 0
    assert s.build.built[0].registry["github"].enabled is False


def test_the_real_handbook_source_asks_for_the_github_flag(
    fake_chroma, fake_pipeline, capsys
):
    assert main(["--source", "tech-interview-handbook"]) == 1
    assert "--enable-server github" in capsys.readouterr().out
