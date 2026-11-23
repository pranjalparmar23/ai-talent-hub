"""Run the learning plan golden set against the real knowledge base and the real model.

From backend/:

    python -m evals.run_roadmap                       # every case
    python -m evals.run_roadmap --split dev           # the cases to tune on
    python -m evals.run_roadmap --split test          # only to confirm a change
    python -m evals.run_roadmap --tag ranking         # one slice (repeat --tag to add)
    python -m evals.run_roadmap --repeat 3            # run every case 3 times
    python -m evals.run_roadmap --verbose             # one line per case
    python -m evals.run_roadmap --save                # also write the run to a file
    python -m evals.run_roadmap --compare links.v1.baseline.json

Trying other ranking weights, without editing code and without calling the model:

    python -m evals.run_roadmap --ranking-only --split dev
    python -m evals.run_roadmap --ranking-only --split dev --trust-weight 0.5
    python -m evals.run_roadmap --ranking-only --split dev --source-trust freecodecamp=0.5
    python -m evals.run_roadmap --ranking-only --split dev --sweep trust_weight=0.3,0.4,0.5,0.6

`--ranking-only` runs only the agent's retrieval and ranking, checks the cases that name
a `top_ranked_hosts_any`, and never calls the model. It is fast and free, and gives the
same answer every time. `--sweep` (ranking-only mode only) tries one setting at several
values and prints one row per value. Tune on the dev split only. When one setting has
been chosen, run the test split once with it, to confirm. Then change the value in
`app/agents/candidate/roadmap_agent.py`. The overrides here change this run only.

A normal run does two things for each case with the real `RoadmapAgent`:

1. Runs the agent's retrieval step on its own (no model) to see which pages it retrieved
   and which one it ranked first. The ranking check reads this, so it gives the same
   answer every time.
2. Calls `generate()`, which retrieves again (the answer is the same, because the
   knowledge base does not change during a run) and asks the model for the plan. The
   plan is then checked against the case.

Step 1 uses two private names of the agent, `_clean_skills` and `_retrieve_resources`.
If either is renamed, this fails loudly and the runner must be updated with it.

The plan comes from a model with a little randomness, so one failing run can be chance.
For a result that matters, use `--repeat` and look at the pass rate. The runner waits
`--delay` seconds between model calls (default 2) to stay inside the Groq rate limit,
and stops a run that takes longer than `--timeout` seconds (default 180). A rate-limit
error makes the agent return its fallback plan, and the `model_answered` check then
fails for that run.

A failing case is a finding, not an error, so the exit code stays 0. It is 1 for a bad
dataset or bad options, and 2 when a run could not be done (ChromaDB down, retrieval
raised, no API key).

The inputs are our own invented skills and roles. Never point this at real candidates.
"""

import argparse
import asyncio
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.cases import (
    SPLITS,
    DatasetError,
    RoadmapCase,
    dataset_path,
    load_dataset,
    select,
    unreviewed,
)
from evals.metrics import (
    RoadmapScore,
    RoadmapSummary,
    fmt,
    host_of,
    plan_links,
    render_table,
    report_roadmap,
    score_roadmap,
)
from evals.reporting import (
    RESULTS_DIR,
    ReportError,
    compare_text,
    load_record,
    resolve_record_path,
    save_record,
)

AREA = "roadmap"
TEXT_WIDTH = 70  # longest input shown in a line
MAX_REPEAT = 10
MAX_SWEEP_VALUES = 8

# The ranking settings that can be tried: the three weights (0 to 2), and the trust of
# one source, written trust.<source-id> (0 to 1).
WEIGHT_NAMES = ("base_weight", "trust_weight", "fresh_weight")
TRUST_PREFIX = "trust."


@dataclass(frozen=True)
class Attempt:
    """One run of the agent for one case. `error` is set when the run could not be done."""

    plan: dict[str, Any] = field(default_factory=dict)
    retrieved_urls: tuple[str, ...] = ()
    top_ranked_url: str | None = None
    error: str = ""


Planner = Callable[[RoadmapCase], Awaitable[Attempt]]
# Called after each run: (runs done, runs in all, the case, seconds taken, error text).
Progress = Callable[[int, int, RoadmapCase, float, str], None]
# What a planner factory returns: the planner, the settings in use, chunks per collection.
Built = tuple[Planner, dict[str, Any], dict[str, int]]
# Called with (overrides, ranking_only).
PlannerFactory = Callable[[Mapping[str, Any], bool], Built]
Scorer = Callable[[RoadmapCase, Attempt], RoadmapScore]


def score_attempt(case: RoadmapCase, attempt: Attempt) -> RoadmapScore:
    """All the checks for a plan."""
    return score_roadmap(
        case,
        attempt.plan,
        retrieved_urls=attempt.retrieved_urls,
        top_ranked_url=attempt.top_ranked_url,
    )


def score_ranking(case: RoadmapCase, attempt: Attempt) -> RoadmapScore:
    """Only the ranking check, for `--ranking-only` (there is no plan to check)."""
    full = score_attempt(case, attempt)
    return RoadmapScore(
        case_id=full.case_id,
        tags=full.tags,
        split=full.split,
        checks=tuple(c for c in full.checks if c.name == "top_ranked_host"),
    )


@dataclass
class CaseRun:
    case: RoadmapCase
    attempts: list[Attempt]
    scores: list[RoadmapScore]

    @property
    def ok(self) -> bool:
        """The case passes when every one of its runs passes."""
        return all(s.ok for s in self.scores)

    @property
    def passed(self) -> int:
        return sum(s.ok for s in self.scores)


@dataclass
class RunResult:
    ran: list[CaseRun] = field(default_factory=list)
    not_run: list[tuple[str, str]] = field(default_factory=list)  # (case id, why)

    @property
    def scores(self) -> list[RoadmapScore]:
        """One score per run, so a case repeated three times counts three times."""
        return [s for run in self.ran for s in run.scores]


async def run_cases(
    cases: Sequence[RoadmapCase],
    planner: Planner,
    *,
    repeat: int = 1,
    delay: float = 0.0,
    timeout: float | None = None,
    progress: Progress | None = None,
    scorer: Scorer = score_attempt,
) -> RunResult:
    """Run every case `repeat` times, one after another (the model has a rate limit).

    `timeout` (seconds) ends one run that takes too long, so a stuck model call cannot
    hold up the whole set. `progress` is told after each run, so a long run shows life.

    A case whose first run could not be done is listed in `not_run` and not scored. A
    later run of a case that did start is kept only if it worked: a run lost half way
    would otherwise count as a plan that failed its checks.
    """
    result = RunResult()
    first = True
    total = len(cases) * repeat
    done = 0
    for case in cases:
        attempts: list[Attempt] = []
        scores: list[RoadmapScore] = []
        failure = ""
        for _ in range(repeat):
            if not first and delay > 0:
                await asyncio.sleep(delay)
            first = False
            began = time.monotonic()
            try:
                if timeout:
                    attempt = await asyncio.wait_for(planner(case), timeout)
                else:
                    attempt = await planner(case)
            except asyncio.TimeoutError:
                attempt = Attempt(error=f"timed out after {timeout:g} seconds")
            done += 1
            if progress:
                progress(done, total, case, time.monotonic() - began, attempt.error)
            if attempt.error:
                failure = attempt.error
                break
            attempts.append(attempt)
            scores.append(scorer(case, attempt))
        if failure and not attempts:
            result.not_run.append((case.id, failure))
        else:
            if failure:
                result.not_run.append((case.id, f"run {len(attempts) + 1}: {failure}"))
            result.ran.append(CaseRun(case, attempts, scores))
    return result


# ── ranking settings that can be tried ───────────────────────


def check_setting(name: str, value: float) -> float:
    """The value, or ValueError saying what is allowed for this setting."""
    if name in WEIGHT_NAMES:
        low, high = 0.0, 2.0
    elif name.startswith(TRUST_PREFIX) and len(name) > len(TRUST_PREFIX):
        low, high = 0.0, 1.0
    else:
        raise ValueError(
            f"unknown setting '{name}'. Use {', '.join(WEIGHT_NAMES)} "
            f"or {TRUST_PREFIX}<source-id>"
        )
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}")
    return float(value)


def parse_pair(text: str, option: str) -> tuple[str, str]:
    """'name=value' -> ('name', 'value'), or ValueError."""
    name, sep, raw = text.partition("=")
    if not sep or not name.strip() or not raw.strip():
        raise ValueError(f"{option} looks like NAME=VALUE")
    return name.strip(), raw.strip()


def parse_sweep(text: str) -> tuple[str, list[float]]:
    """'trust_weight=0.3,0.5' -> ('trust_weight', [0.3, 0.5]). ValueError if wrong."""
    name, raw = parse_pair(text, "--sweep")
    if not name.startswith(TRUST_PREFIX):
        name = name.replace("-", "_")  # a source id keeps its dashes
    values: list[float] = []
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


_ORIGINAL: dict[str, Any] = (
    {}
)  # the agent's own values, saved before the first override


def _apply_ranking(ra: Any, overrides: Mapping[str, Any]) -> None:
    """Set the agent's ranking constants for this run: its own values plus `overrides`.

    The agent reads these module values every time it ranks, so setting them is enough.
    The originals are saved on the first call, so a sweep (many calls) always starts
    from the agent's real values and not from the previous override.
    """
    if not _ORIGINAL:
        _ORIGINAL.update(
            base=ra.RANK_BASE_WEIGHT,
            trust=ra.RANK_TRUST_WEIGHT,
            fresh=ra.RANK_FRESH_WEIGHT,
            source_trust=dict(ra.SOURCE_TRUST),
        )
    ra.RANK_BASE_WEIGHT = overrides.get("base_weight", _ORIGINAL["base"])
    ra.RANK_TRUST_WEIGHT = overrides.get("trust_weight", _ORIGINAL["trust"])
    ra.RANK_FRESH_WEIGHT = overrides.get("fresh_weight", _ORIGINAL["fresh"])
    trust = dict(_ORIGINAL["source_trust"])
    for name, value in overrides.items():
        if name.startswith(TRUST_PREFIX):
            source = name[len(TRUST_PREFIX) :]
            if source not in trust:
                raise ValueError(
                    f"unknown source '{source}'. Known: {', '.join(sorted(trust))}"
                )
            trust[source] = value
    ra.SOURCE_TRUST.clear()
    ra.SOURCE_TRUST.update(trust)


def default_of(settings: Mapping[str, Any], name: str) -> float | None:
    """The value a setting has in `settings` (the shape build_planner returns)."""
    if name.startswith(TRUST_PREFIX):
        return settings.get("source_trust", {}).get(name[len(TRUST_PREFIX) :])
    key = name.removesuffix("_weight")
    return settings.get("rank_weights", {}).get(key)


# ── the real planner ─────────────────────────────────────────


def build_planner(
    overrides: Mapping[str, Any] | None = None, ranking_only: bool = False
) -> Built:
    """(planner, settings in use, chunks per collection). Needs ChromaDB and a Groq key.

    `overrides` replaces the agent's ranking weights and source trust for this run only.
    With `ranking_only` the planner runs retrieval and ranking and never calls the model.

    The app is imported here, not at the top, so the scoring and printing can be tested
    without the embedding model or a network.
    """
    from app.agents.candidate import roadmap_agent as ra
    from app.ingest.schema import INTERVIEW_COLLECTION, LEARNING_COLLECTION
    from app.llm.models import DEFAULT_MODEL
    from app.rag.retriever import DEFAULT_MIN_SIMILARITY
    from app.rag.vector_store import VectorStore

    VectorStore.heartbeat()  # fails here, with a clear message, if ChromaDB is down
    _apply_ranking(ra, dict(overrides or {}))
    sizes = {
        name: VectorStore.count(name)
        for name in (INTERVIEW_COLLECTION, LEARNING_COLLECTION)
    }
    settings = {
        "model": os.getenv("GROQ_MODEL", DEFAULT_MODEL),
        "min_similarity": DEFAULT_MIN_SIMILARITY,
        "rank_weights": {
            "base": ra.RANK_BASE_WEIGHT,
            "trust": ra.RANK_TRUST_WEIGHT,
            "fresh": ra.RANK_FRESH_WEIGHT,
        },
        "source_trust": dict(ra.SOURCE_TRUST),
        "resources_per_skill": ra.RESOURCES_PER_SKILL,
        "max_total_resources": ra.MAX_TOTAL_RESOURCES,
        "max_skills_searched": ra.MAX_SKILLS_SEARCHED,
    }
    agent = ra.RoadmapAgent()  # raises here if GROQ_API_KEY is missing

    async def planner(case: RoadmapCase) -> Attempt:
        skills = agent._clean_skills(case.input.skill_gaps)
        urls: tuple[str, ...] = ()
        if skills:
            try:
                context = await asyncio.to_thread(agent._retrieve_resources, skills)
            except Exception as exc:
                return Attempt(error=f"retrieval failed: {type(exc).__name__}: {exc}")
            # `resources` is sorted best rank first, so the first one is the top page
            urls = tuple(r["url"] for r in context.resources if r.get("url"))
        top = urls[0] if urls else None
        if ranking_only:
            return Attempt(retrieved_urls=urls, top_ranked_url=top)
        plan = await agent.generate(case.input.skill_gaps, case.input.target_role)
        return Attempt(plan=plan, retrieved_urls=urls, top_ranked_url=top)

    return planner, settings, sizes


# ── what gets printed and saved ──────────────────────────────


def _short(text: str, width: int = TEXT_WIDTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def case_label(case: RoadmapCase) -> str:
    skills = (
        ", ".join(s.strip() or "(blank)" for s in case.input.skill_gaps) or "(none)"
    )
    return f"{case.id} [{' '.join(case.tags)}] {_short(skills)} / {_short(case.input.target_role, 40)}"


def describe(run: CaseRun) -> str:
    """One case that did not pass: each failed check, with what was seen."""
    runs = len(run.scores)
    head = case_label(run.case)
    if runs > 1:
        head += f"  (passed {run.passed} of {runs} runs)"
    lines = [head]
    seen: set[str] = set()
    for score in run.scores:
        for check in score.failed:
            if check.name in seen:
                continue
            seen.add(check.name)
            lines.append(f"    {check.name}: {check.detail}")
    return "\n".join(lines)


def _summary_row(label: str, s: RoadmapSummary) -> list[Any]:
    return [label, s.cases, s.passed, fmt(s.pass_rate)]


def format_report(report: dict[str, RoadmapSummary]) -> str:
    return render_table(
        ["group", "runs", "passed", "pass rate"],
        [_summary_row(label, s) for label, s in report.items()],
    )


def format_sweep(
    name: str,
    rows: Sequence[tuple[float, RunResult]],
    default: float | None,
) -> str:
    """One row per tried value: how many ranking cases passed, and the best page of
    each one that did not."""
    table_rows = []
    for value, result in rows:
        passed = sum(run.ok for run in result.ran)
        failed = [
            f"{run.case.id} ({_top_host(run)})" for run in result.ran if not run.ok
        ]
        label = f"{value:g}" + (" (default)" if value == default else "")
        table_rows.append(
            [label, f"{passed}/{len(result.ran)}", "; ".join(failed) or "none"]
        )
    return render_table(
        [name, "passed", "cases that did not pass (best page found)"], table_rows
    )


def case_record(run: CaseRun) -> dict[str, Any]:
    return {
        "id": run.case.id,
        "ok": run.ok,
        "tags": list(run.case.tags),
        "split": run.case.split,
        "passed_runs": run.passed,
        "runs": [
            {
                "ok": score.ok,
                "failed": [{"check": c.name, "detail": c.detail} for c in score.failed],
                "links": list(dict.fromkeys(plan_links(attempt.plan))),
                "top_ranked_url": attempt.top_ranked_url,
                "retrieved": len(attempt.retrieved_urls),
                "notice": bool(attempt.plan.get("notice")),
            }
            for attempt, score in zip(run.attempts, run.scores)
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
    report = report_roadmap(result.scores)
    return {
        "area": AREA,
        "dataset": dataset,
        "version": version,
        "run_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "filters": filters,
        "settings": settings,
        "knowledge_base": sizes,
        "report": {label: summary.as_dict() for label, summary in report.items()},
        "cases": [case_record(run) for run in result.ran],
        "not_run": [{"id": i, "reason": why} for i, why in result.not_run],
    }


def _top_host(run: CaseRun) -> str:
    tops = {host_of(a.top_ranked_url or "") or "-" for a in run.attempts}
    return "/".join(sorted(tops))


# ── command line ─────────────────────────────────────────────


def _prepare_environment() -> None:
    """Quiet ChromaDB's telemetry and read backend/.env, before the app is imported."""
    # The embedding library warns when the process forks after it has been used. Nothing
    # here depends on it, and the warning only hides the output that matters.
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
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


def _print_progress(
    done: int, total: int, case: RoadmapCase, seconds: float, error: str
) -> None:
    """One line on stderr after each run, so a long run shows that it is alive."""
    note = f"  ERROR: {error}" if error else ""
    print(
        f"  [{done}/{total}] {case.id} {seconds:.1f}s{note}",
        file=sys.stderr,
        flush=True,
    )


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run_roadmap",
        description="Run the learning plan golden set (real knowledge base and model).",
    )
    parser.add_argument("--split", choices=SPLITS, help="only this split")
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="only cases with this tag (repeatable)",
    )
    parser.add_argument("--version", type=int, help="dataset version (default: latest)")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help=f"runs per case, 1 to {MAX_REPEAT} (the model is not deterministic)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="seconds to wait between model calls (default 2)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="seconds one run may take before it is stopped (default 180, 0 = no limit)",
    )
    parser.add_argument("--verbose", action="store_true", help="one line per case")
    parser.add_argument("--save", action="store_true", help="write the run to a file")
    parser.add_argument("--compare", metavar="FILE", help="compare with a saved run")
    parser.add_argument(
        "--ranking-only",
        action="store_true",
        help="only retrieval and ranking: no model call, only the cases that name a best page",
    )
    parser.add_argument("--base-weight", type=float, help="try another match weight")
    parser.add_argument("--trust-weight", type=float, help="try another trust weight")
    parser.add_argument(
        "--fresh-weight", type=float, help="try another freshness weight"
    )
    parser.add_argument(
        "--source-trust",
        action="append",
        default=[],
        metavar="SOURCE=VALUE",
        help="try another trust (0 to 1) for one source, e.g. freecodecamp=0.5",
    )
    parser.add_argument(
        "--sweep",
        metavar="NAME=V1,V2,..",
        help="with --ranking-only: one row per value of one setting "
        "(base_weight, trust_weight, fresh_weight or trust.<source-id>)",
    )
    return parser


def _read_overrides(args: argparse.Namespace) -> tuple[dict[str, float], Any]:
    """(overrides, sweep) from the options. ValueError with a clear message if wrong."""
    overrides: dict[str, float] = {}
    for name in WEIGHT_NAMES:
        value = getattr(args, name)
        if value is not None:
            overrides[name] = check_setting(name, value)
    for text in args.source_trust:
        source, raw = parse_pair(text, "--source-trust")
        try:
            number = float(raw)
        except ValueError:
            raise ValueError(f"'{raw}' is not a number") from None
        overrides[TRUST_PREFIX + source] = check_setting(TRUST_PREFIX + source, number)
    sweep = parse_sweep(args.sweep) if args.sweep else None
    return overrides, sweep


def main(
    argv: Sequence[str] | None = None,
    *,
    planner_factory: PlannerFactory | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    results_dir: Path = RESULTS_DIR,
) -> int:
    args = _make_parser().parse_args(argv)

    if not 1 <= args.repeat <= MAX_REPEAT or args.delay < 0 or args.timeout < 0:
        print(
            f"--repeat must be 1 to {MAX_REPEAT}, and --delay and --timeout 0 or more.",
            file=sys.stderr,
        )
        return 1
    try:
        overrides, sweep = _read_overrides(args)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if overrides and not args.ranking_only:
        print(
            "Ranking weights can be tried only with --ranking-only, which does not call "
            "the model.",
            file=sys.stderr,
        )
        return 1
    if sweep and not args.ranking_only:
        print(
            "--sweep needs --ranking-only (a sweep must not call the model).",
            file=sys.stderr,
        )
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
    if args.ranking_only:
        chosen = [c for c in chosen if c.expected.top_ranked_hosts_any]
    if not chosen:
        print(
            "No case matches these options"
            + (
                " (with --ranking-only, a case must name a best page)."
                if args.ranking_only
                else "."
            ),
            file=sys.stderr,
        )
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

    if planner_factory is None:
        _prepare_environment()
        planner_factory = build_planner
    try:
        planner, settings, sizes = planner_factory(overrides, args.ranking_only)
    except Exception as exc:
        print(f"Cannot start the run: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "Is ChromaDB running, is the knowledge base filled, and is GROQ_API_KEY set?",
            file=sys.stderr,
        )
        return 2

    # Ranking does not use the model, so there is nothing to wait for or repeat.
    repeat = 1 if args.ranking_only else args.repeat
    delay = 0.0 if args.ranking_only else args.delay
    timeout = None if args.ranking_only else (args.timeout or None)
    scorer = score_ranking if args.ranking_only else score_attempt

    version = int(re.search(r"\.v(\d+)\.jsonl$", path.name).group(1))
    print(f"Learning plan golden set: {path.name}, {len(chosen)} of {len(cases)} cases")
    if args.ranking_only:
        print("Ranking only: the model is not called.")
    else:
        print(f"Runs per case: {repeat}. Model: {settings.get('model', 'unknown')}")
    print(
        "Knowledge base: "
        + ", ".join(f"{name} {count} chunks" for name, count in sizes.items())
    )
    weights = settings.get("rank_weights", {})
    print(
        f"Ranking weights: match {weights.get('base')}, trust {weights.get('trust')}, "
        f"freshness {weights.get('fresh')}"
    )
    print(
        "Source trust: "
        + ", ".join(f"{k} {v}" for k, v in settings.get("source_trust", {}).items())
    )
    if overrides:
        print(
            "Overrides for this run: "
            + ", ".join(f"{k}={v:g}" for k, v in overrides.items())
        )
    drafts = unreviewed(chosen)
    if drafts:
        print(
            f"{len(drafts)} case(s) are model-drafts that a person has not checked yet."
        )
    print()

    # A private loop, closed after use. asyncio.run() would reset the "current" loop to
    # None, which breaks tests that share one loop when they call main().
    loop = asyncio.new_event_loop()
    try:
        if sweep:
            return _sweep(sweep, chosen, overrides, planner_factory, loop, args.split)
        started = now()
        result = loop.run_until_complete(
            run_cases(
                chosen,
                planner,
                repeat=repeat,
                delay=delay,
                timeout=timeout,
                progress=None if args.ranking_only else _print_progress,
                scorer=scorer,
            )
        )
    finally:
        loop.close()

    filters = {
        "split": args.split,
        "tags": sorted(args.tag),
        "repeat": repeat,
        "ranking_only": args.ranking_only,
    }
    if result.ran:
        record = build_record(
            result,
            dataset=path.name,
            version=version,
            filters=filters,
            settings=settings,
            sizes=sizes,
            now=started,
        )
        report = report_roadmap(result.scores)
        print(format_report(report))
        counts = report["all"].failed_checks
        if counts:
            print()
            print("Checks that failed, by number of runs:")
            for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                print(f"  {name}: {count}")
        print()

        if args.verbose:
            for run in result.ran:
                mark = "ok  " if run.ok else "FAIL"
                if args.ranking_only:
                    best = run.attempts[0].top_ranked_url or "nothing retrieved"
                    print(f"{mark} {run.case.id}  best page: {best}")
                    continue
                links = max(
                    (len(set(plan_links(a.plan))) for a in run.attempts), default=0
                )
                print(
                    f"{mark} {run.case.id}  passed {run.passed}/{len(run.scores)}, "
                    f"best page {_top_host(run)}, up to {links} link(s)"
                )
            print()

        failed = [run for run in result.ran if not run.ok]
        if failed:
            print(
                f"{len(failed)} case(s) did not pass. "
                "Decide for each: label wrong, or system wrong?"
            )
            for run in failed:
                print(f"  {describe(run)}")
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
    print(
        f"\n{len(result.not_run)} run(s) could not be done and are not in the scores:"
    )
    for case_id, why in result.not_run:
        print(f"  {case_id}: {why}")
    return 2


def _sweep(
    sweep: tuple[str, list[float]],
    chosen: Sequence[RoadmapCase],
    overrides: Mapping[str, Any],
    planner_factory: PlannerFactory,
    loop: asyncio.AbstractEventLoop,
    split: str | None,
) -> int:
    """Rank the chosen cases once per value of one setting. Never calls the model."""
    name, values = sweep
    if split != "dev":
        print(
            "Warning: tune on the dev split only (--split dev). The test split is for\n"
            "confirming one chosen setting, once.\n"
        )
    rows: list[tuple[float, RunResult]] = []
    problems = RunResult()
    for value in values:
        planner, _, _ = planner_factory({**overrides, name: value}, True)
        result = loop.run_until_complete(
            run_cases(chosen, planner, scorer=score_ranking)
        )
        rows.append((value, result))
        problems.not_run.extend(
            (f"{name}={value:g} {i}", why) for i, why in result.not_run
        )

    # The settings with no overrides at all say which value is the agent's own default.
    own = planner_factory({}, True)[1]
    print(f"Sweep of {name} over {len(chosen)} ranking cases:")
    print(format_sweep(name, rows, default=default_of(own, name)))
    return _report_not_run(problems)


if __name__ == "__main__":
    sys.exit(main())
