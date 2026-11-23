"""Run the retrieval golden set against the real knowledge base.

From backend/:

    python -m evals.run_retrieval                       # every case
    python -m evals.run_retrieval --split dev           # the cases to tune on
    python -m evals.run_retrieval --split test          # only to confirm a change
    python -m evals.run_retrieval --tag non-technical   # one slice (repeat --tag to add)
    python -m evals.run_retrieval --verbose             # one line per case
    python -m evals.run_retrieval --save                # also write the run to a file
    python -m evals.run_retrieval --compare retrieval.v1.baseline.json

Trying other settings, without editing code:

    python -m evals.run_retrieval --split dev --min-similarity 0.40
    python -m evals.run_retrieval --split dev --mmr-lambda 0.5 --max-per-doc 1
    python -m evals.run_retrieval --split dev --sweep min_similarity=0.30,0.35,0.40,0.45

`--sweep` runs the selected cases once for each value of ONE setting and prints one row
per value. Tune on the dev split only. When one setting has been chosen, run the test
split once with it, to confirm. Then change the default in `app/rag/retriever.py`.
The overrides here change this run only; nothing is saved to the app.

This uses the real ChromaDB (it must be running) and the real embedding model, and goes
through `retrieve_with_fallback()`, the same call the agents make. It writes nothing to
the knowledge base. Nothing is faked, so this is not part of `pytest tests/unit`.

Cases that do not pass are listed with what came back, so each one can be judged: is the
label wrong, or is the system wrong? A failing case is a finding, not an error, so the
exit code stays 0. The exit code is 1 for a bad dataset or bad options, and 2 when the
run itself could not be done (ChromaDB down, a case that raised).

The query text is printed here because the cases are our own invented queries. Never
point this at real user queries.
"""

import argparse
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.cases import (
    SPLITS,
    DatasetError,
    RetrievalCase,
    dataset_path,
    load_dataset,
    select,
    unreviewed,
)
from evals.metrics import (
    Hit,
    RetrievalScore,
    RetrievalSummary,
    fmt,
    render_table,
    report_retrieval,
    score_retrieval,
    summarise_retrieval,
)
from evals.reporting import (
    RESULTS_DIR,
    ReportError,
    compare_text,
    load_record,
    resolve_record_path,
    save_record,
)

AREA = "retrieval"
TITLE_WIDTH = 60  # longest title shown in a line
MAX_SWEEP_VALUES = 8

# The settings that can be tried from the command line: name -> (type, lowest, highest).
TUNABLE: dict[str, tuple[type, float, float]] = {
    "min_similarity": (float, 0.0, 1.0),
    "mmr_lambda": (float, 0.0, 1.0),
    "max_per_doc": (int, 1, 20),
}


@dataclass(frozen=True)
class Outcome:
    """What one search gave back. `error` is set when the search could not be done."""

    hits: tuple[Hit, ...] = ()
    similarities: tuple[float, ...] = ()
    route: str = ""
    reason: str = ""
    error: str = ""


Search = Callable[[RetrievalCase], Outcome]
# What a search factory returns: the search, the settings it uses, chunks per collection.
Built = tuple[Search, dict[str, Any], dict[str, int]]
SearchFactory = Callable[[Mapping[str, Any]], Built]


@dataclass
class RunResult:
    ran: list[tuple[RetrievalCase, Outcome, RetrievalScore]] = field(
        default_factory=list
    )
    not_run: list[tuple[str, str]] = field(default_factory=list)  # (case id, why)

    @property
    def scores(self) -> list[RetrievalScore]:
        return [score for _, _, score in self.ran]


def run_cases(cases: Sequence[RetrievalCase], search: Search) -> RunResult:
    """Search once per case and score it. A case whose search failed is not scored."""
    result = RunResult()
    for case in cases:
        outcome = search(case)
        if outcome.error:
            result.not_run.append((case.id, outcome.error))
            continue
        result.ran.append((case, outcome, score_retrieval(case, outcome.hits)))
    return result


# ── settings that can be tried ───────────────────────────────


def check_setting(name: str, value: float) -> float | int:
    """The value as the right type, or ValueError saying what is allowed."""
    kind, low, high = TUNABLE[name]
    if kind is int and int(value) != value:
        raise ValueError(f"{name} must be a whole number")
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return kind(value)


def parse_sweep(text: str) -> tuple[str, list[float | int]]:
    """'min_similarity=0.3,0.4' -> ('min_similarity', [0.3, 0.4]). ValueError if wrong."""
    name, sep, raw = text.partition("=")
    name = name.strip().replace("-", "_")
    if not sep or name not in TUNABLE:
        raise ValueError(
            f"--sweep looks like NAME=v1,v2,v3 with NAME one of {', '.join(TUNABLE)}"
        )
    values: list[float | int] = []
    for part in raw.split(","):
        try:
            number = float(part)
        except ValueError:
            raise ValueError(f"'{part.strip()}' is not a number") from None
        value = check_setting(name, number)
        if value not in values:
            values.append(value)
    if not 1 <= len(values) <= MAX_SWEEP_VALUES:
        raise ValueError(f"--sweep takes 1 to {MAX_SWEEP_VALUES} values")
    return name, values


# ── the real search ──────────────────────────────────────────


def build_search(overrides: Mapping[str, Any] | None = None) -> Built:
    """(search function, settings in use, chunks per collection). Needs ChromaDB.

    `overrides` replaces the retriever's defaults for this run only (see TUNABLE).

    The app is imported here, not at the top, so the checks and scoring above stay light
    and can be tested without the embedding model.
    """
    from app.ingest.schema import INTERVIEW_COLLECTION, LEARNING_COLLECTION
    from app.rag.embeddings import EMBEDDING_MODEL_NAME
    from app.rag.fallback import REASON_RETRIEVAL_ERROR, retrieve_with_fallback
    from app.rag.retriever import (
        DEFAULT_MAX_PER_DOC,
        DEFAULT_MIN_SIMILARITY,
        DEFAULT_MMR_LAMBDA,
        DEFAULT_TOP_K,
        RAGRetriever,
    )
    from app.rag.vector_store import VectorStore

    overrides = dict(overrides or {})
    VectorStore.heartbeat()  # fails here, with a clear message, if ChromaDB is down
    sizes = {
        name: VectorStore.count(name)
        for name in (INTERVIEW_COLLECTION, LEARNING_COLLECTION)
    }
    settings = {
        "embedding_model": EMBEDDING_MODEL_NAME,
        "top_k": DEFAULT_TOP_K,
        "min_similarity": overrides.get("min_similarity", DEFAULT_MIN_SIMILARITY),
        "mmr_lambda": overrides.get("mmr_lambda", DEFAULT_MMR_LAMBDA),
        "max_per_doc": overrides.get("max_per_doc", DEFAULT_MAX_PER_DOC),
    }
    retriever = RAGRetriever()

    def search(case: RetrievalCase) -> Outcome:
        inp = case.input
        options = {
            key: value
            for key, value in (
                ("role_family", inp.role_family),
                ("level", inp.level),
                ("domain", inp.domain),
            )
            if value is not None
        }
        try:
            retrieval = retrieve_with_fallback(
                inp.query,
                inp.collection,
                retriever=retriever,
                top_k=inp.top_k,
                min_similarity=settings["min_similarity"],
                mmr_lambda=settings["mmr_lambda"],
                max_per_doc=settings["max_per_doc"],
                **options,
            )
        except Exception as exc:  # a bad filter value, for one
            return Outcome(error=f"{type(exc).__name__}: {exc}")
        if retrieval.reason == REASON_RETRIEVAL_ERROR:
            # The fallback hides this on purpose, so an agent keeps working. Here it
            # would make an "expect nothing" case pass for the wrong reason.
            return Outcome(
                error="the vector store or embedding model failed (see the log)"
            )
        chunks = retrieval.chunks
        return Outcome(
            hits=tuple(Hit.from_chunk(c) for c in chunks),
            similarities=tuple(c.similarity for c in chunks),
            route=retrieval.route,
            reason=retrieval.reason,
        )

    return search, settings, sizes


# ── what gets printed and saved ──────────────────────────────


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TITLE_WIDTH else text[: TITLE_WIDTH - 1] + "…"


def top_line(outcome: Outcome, limit: int = 3) -> str:
    """The first few results as: "title" (0.52) [source-id]."""
    if not outcome.hits:
        return f"nothing (route {outcome.route or '-'}, reason {outcome.reason or '-'})"
    return ", ".join(
        f'"{_short(h.title or h.text)}" ({sim:.2f}) [{h.source_id}]'
        for h, sim in list(zip(outcome.hits, outcome.similarities))[:limit]
    )


def describe(case: RetrievalCase, outcome: Outcome, score: RetrievalScore) -> str:
    """One case that did not pass, with enough to judge it."""
    head = f"{case.id} [{' '.join(case.tags)}] \"{_short(case.input.query)}\""
    if case.expected.expect_empty:
        return (
            f"{head}\n    expected nothing, got {score.returned}: {top_line(outcome)}"
        )
    wanted = {
        "keywords": case.expected.relevant_keywords,
        "sources": case.expected.relevant_source_ids,
        "domains": case.expected.relevant_domains,
    }
    wanted_text = "; ".join(f"{k} {v}" for k, v in wanted.items() if v)
    return (
        f"{head}\n    no relevant result. Wanted {wanted_text}\n"
        f"    got: {top_line(outcome)}"
    )


def _empty_ok(s: RetrievalSummary) -> str | None:
    return f"{s.empty_correct}/{s.empty_cases}" if s.empty_cases else None


def _summary_row(label: str, s: RetrievalSummary) -> list[Any]:
    return [label, s.cases, fmt(s.precision), fmt(s.hit_rate), fmt(s.mrr), _empty_ok(s)]


def format_report(report: dict[str, RetrievalSummary]) -> str:
    return render_table(
        ["group", "cases", "precision", "hit rate", "MRR", "empty ok"],
        [_summary_row(label, s) for label, s in report.items()],
    )


def format_sweep(
    name: str,
    rows: Sequence[tuple[float | int, RunResult]],
    default: float | int | None,
) -> str:
    """One row per tried value: the scores over all selected cases, and which failed."""
    table_rows = []
    for value, result in rows:
        s = summarise_retrieval(result.scores)
        failed = [case.id for case, _, score in result.ran if not score.ok]
        label = f"{value}" + (" (default)" if value == default else "")
        table_rows.append(
            [
                label,
                fmt(s.precision),
                fmt(s.hit_rate),
                fmt(s.mrr),
                _empty_ok(s),
                " ".join(failed) or "none",
            ]
        )
    return render_table(
        [name, "precision", "hit rate", "MRR", "empty ok", "cases that did not pass"],
        table_rows,
    )


def case_record(
    case: RetrievalCase, outcome: Outcome, score: RetrievalScore
) -> dict[str, Any]:
    return {
        "id": case.id,
        "ok": score.ok,
        "tags": list(case.tags),
        "split": case.split,
        "expect_empty": score.expect_empty,
        "returned": score.returned,
        "relevant": score.relevant,
        "precision": score.precision,
        "route": outcome.route,
        "reason": outcome.reason,
        "top": [
            {"title": h.title, "source_id": h.source_id, "similarity": round(sim, 3)}
            for h, sim in zip(outcome.hits, outcome.similarities)
        ],
    }


def build_record(
    result: RunResult,
    *,
    dataset: str,
    version: int,
    filters: dict[str, Any],
    settings: dict[str, Any],
    sizes: dict[str, int],
    now: datetime,
) -> dict[str, Any]:
    report = report_retrieval(result.scores)
    return {
        "area": AREA,
        "dataset": dataset,
        "version": version,
        "run_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "filters": filters,
        "settings": settings,
        "knowledge_base": sizes,
        "report": {label: summary.as_dict() for label, summary in report.items()},
        "cases": [case_record(c, o, s) for c, o, s in result.ran],
        "not_run": [{"id": i, "reason": why} for i, why in result.not_run],
    }


# ── command line ─────────────────────────────────────────────


def _prepare_environment() -> None:
    """Quiet ChromaDB's telemetry and read backend/.env, before the app is imported."""
    try:
        import posthog

        posthog.capture = lambda *args, **kwargs: None
        posthog.Posthog.capture = lambda *args, **kwargs: None
    except ImportError:
        pass
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run_retrieval",
        description="Run the retrieval golden set against the real knowledge base.",
    )
    parser.add_argument("--split", choices=SPLITS, help="only this split")
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="only cases with this tag (repeatable)",
    )
    parser.add_argument("--version", type=int, help="dataset version (default: latest)")
    parser.add_argument("--verbose", action="store_true", help="one line per case")
    parser.add_argument("--save", action="store_true", help="write the run to a file")
    parser.add_argument("--compare", metavar="FILE", help="compare with a saved run")
    parser.add_argument(
        "--min-similarity", type=float, help="try another cutoff for this run only"
    )
    parser.add_argument(
        "--mmr-lambda", type=float, help="try another MMR balance for this run only"
    )
    parser.add_argument(
        "--max-per-doc",
        type=int,
        help="try another most-chunks-per-page for this run only",
    )
    parser.add_argument(
        "--sweep",
        metavar="NAME=V1,V2,..",
        help="run once per value of one setting and print one row per value",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    search_factory: SearchFactory | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    results_dir: Path = RESULTS_DIR,
) -> int:
    args = _make_parser().parse_args(argv)

    overrides: dict[str, Any] = {}
    sweep: tuple[str, list[float | int]] | None = None
    try:
        for name in TUNABLE:
            value = getattr(args, name)
            if value is not None:
                overrides[name] = check_setting(name, value)
        if args.sweep:
            sweep = parse_sweep(args.sweep)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if sweep and (args.save or args.compare):
        print("--sweep cannot be combined with --save or --compare.", file=sys.stderr)
        return 1

    try:
        path = dataset_path(AREA, args.version)
        cases = load_dataset(AREA, args.version)
    except DatasetError as exc:
        print("The dataset has problems:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    chosen = select(cases, split=args.split, tags=args.tag)
    if not chosen:
        print("No case matches these options.", file=sys.stderr)
        return 1

    baseline = None
    if args.compare:
        try:
            baseline = load_record(
                resolve_record_path(args.compare, path.parent, results_dir)
            )
        except ReportError as exc:
            print(exc, file=sys.stderr)
            return 1
        if baseline["area"] != AREA:
            print(
                f"{args.compare} is a {baseline['area']} run, not {AREA}.",
                file=sys.stderr,
            )
            return 1

    if search_factory is None:
        _prepare_environment()
        search_factory = build_search
    try:
        search, settings, sizes = search_factory(overrides)
    except Exception as exc:
        print(f"Cannot start the run: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Is ChromaDB running, and is the knowledge base filled?", file=sys.stderr)
        return 2

    version = int(re.search(r"\.v(\d+)\.jsonl$", path.name).group(1))
    print(f"Retrieval golden set: {path.name}, {len(chosen)} of {len(cases)} cases")
    print(
        "Knowledge base: "
        + ", ".join(f"{name} {count} chunks" for name, count in sizes.items())
    )
    print(
        f"Settings: top_k {settings.get('top_k')}, cutoff {settings.get('min_similarity')}, "
        f"MMR {settings.get('mmr_lambda')}, max {settings.get('max_per_doc')} per page"
    )
    if overrides:
        print(
            "Overrides for this run: "
            + ", ".join(f"{k}={v}" for k, v in overrides.items())
        )
    drafts = unreviewed(chosen)
    if drafts:
        print(
            f"{len(drafts)} case(s) are model-drafts that a person has not checked yet."
        )
    print()

    if sweep:
        return _sweep(sweep, chosen, overrides, search_factory, args.split)

    started = now()
    result = run_cases(chosen, search)
    if result.ran:
        record = build_record(
            result,
            dataset=path.name,
            version=version,
            filters={"split": args.split, "tags": sorted(args.tag)},
            settings=settings,
            sizes=sizes,
            now=started,
        )
        print(format_report(report_retrieval(result.scores)))
        print()

        if args.verbose:
            for case, outcome, score in result.ran:
                mark = "ok  " if score.ok else "FAIL"
                print(
                    f"{mark} {case.id}  returned {score.returned}, "
                    f"relevant {score.relevant}: {top_line(outcome, 2)}"
                )
            print()

        failed = [(c, o, s) for c, o, s in result.ran if not s.ok]
        if failed:
            print(
                f"{len(failed)} case(s) did not pass. "
                "Decide for each: label wrong, or system wrong?"
            )
            for case, outcome, score in failed:
                print(f"  {describe(case, outcome, score)}")
        else:
            print(
                "Every case that ran passed."
                if result.not_run
                else "Every case passed."
            )

        if baseline is not None:
            print()
            print(compare_text(baseline, record))
        if args.save:
            saved = save_record(record, now=started, results_dir=results_dir)
            print(f"\nSaved: {saved}")

    return _report_not_run(result)


def _report_not_run(result: RunResult) -> int:
    if not result.not_run:
        return 0
    print(f"\n{len(result.not_run)} case(s) could not run and are not in the scores:")
    for case_id, why in result.not_run:
        print(f"  {case_id}: {why}")
    return 2


def _sweep(
    sweep: tuple[str, list[float | int]],
    chosen: Sequence[RetrievalCase],
    overrides: Mapping[str, Any],
    search_factory: SearchFactory,
    split: str | None,
) -> int:
    name, values = sweep
    if split != "dev":
        print(
            "Warning: tune on the dev split only (--split dev). The test split is for\n"
            "confirming one chosen setting, once.\n"
        )
    rows: list[tuple[float | int, RunResult]] = []
    problems = RunResult()
    for value in values:
        search, _, _ = search_factory({**overrides, name: value})
        result = run_cases(chosen, search)
        rows.append((value, result))
        problems.not_run.extend(
            (f"{name}={value} {i}", why) for i, why in result.not_run
        )

    # The settings with no overrides at all say which value is the current default.
    default = search_factory({})[1].get(name)
    print(f"Sweep of {name} over {len(chosen)} cases:")
    print(format_sweep(name, rows, default=default))
    return _report_not_run(problems)


if __name__ == "__main__":
    sys.exit(main())
