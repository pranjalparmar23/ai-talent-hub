"""Scoring for the golden sets: pure functions, no network, no ChromaDB, no model.

The runners (`run_retrieval.py`, `run_roadmap.py`) fetch the real results. This file
judges them, so the judging rules can be unit tested and stay the same from run to run.

Retrieval (one score per case):

    precision   relevant chunks returned / chunks returned. It is measured over what came
                back, not over K, because the cutoff is meant to return fewer chunks when
                little matches. Returning nothing for a case that expects results scores 0.
    hit         at least one returned chunk is relevant.
    reciprocal rank   1 / (rank of the first relevant chunk), or 0 when there is none.
                Its mean over the cases is MRR.
    cases that expect nothing (`expect_empty`) are not scored for precision or MRR. They
                pass when nothing comes back, and are reported as their own count.

Roadmap (one score per case): a list of named checks. The case passes when every check
passes. The same checks run on every case (no invented link, sources match links,
sources and notice never both set, the model answered) and the case adds the rest.

Every report is given for all cases, for each split, and for each tag, so a weak slice
(non-technical roles, say) cannot hide in an average.
"""

import json
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from evals.cases import RetrievalCase, RetrievalExpected, RoadmapCase

# ── what a search returned ───────────────────────────────────


@dataclass(frozen=True)
class Hit:
    """The parts of a returned chunk that relevance is judged on."""

    title: str = ""
    text: str = ""
    source_id: str = ""
    domain: str = ""
    url: str = ""

    @classmethod
    def from_chunk(cls, chunk: Any) -> "Hit":
        """Build from a `KnowledgeChunk` (anything with `.text` and `.metadata`)."""
        meta = chunk.metadata or {}
        return cls(
            title=str(meta.get("title", "")),
            text=str(chunk.text or ""),
            source_id=str(meta.get("source_id", "")),
            domain=str(meta.get("domain", "")),
            url=str(meta.get("url", "")),
        )


def is_relevant(hit: Hit, expected: RetrievalExpected) -> bool:
    """True when the title or text holds a keyword, or the source or domain is listed."""
    haystack = f"{hit.title}\n{hit.text}".lower()
    return (
        any(word in haystack for word in expected.relevant_keywords)
        or hit.source_id.lower() in expected.relevant_source_ids
        or hit.domain.lower() in expected.relevant_domains
    )


# ── retrieval scores ─────────────────────────────────────────


@dataclass(frozen=True)
class RetrievalScore:
    case_id: str
    tags: tuple[str, ...]
    split: str
    expect_empty: bool
    returned: int
    relevant: int
    precision: float | None  # None for a case that expects nothing
    hit: bool | None
    reciprocal_rank: float | None
    ok: bool  # expects results: hit. Expects nothing: nothing came back.


def score_retrieval(case: RetrievalCase, hits: Sequence[Hit]) -> RetrievalScore:
    """Score one search. Only the first `top_k` hits count."""
    hits = list(hits)[: case.input.top_k]
    common = dict(
        case_id=case.id,
        tags=tuple(case.tags),
        split=case.split,
        expect_empty=case.expected.expect_empty,
        returned=len(hits),
    )
    if case.expected.expect_empty:
        return RetrievalScore(
            **common,
            relevant=0,
            precision=None,
            hit=None,
            reciprocal_rank=None,
            ok=not hits,
        )

    flags = [is_relevant(h, case.expected) for h in hits]
    relevant = sum(flags)
    first = flags.index(True) + 1 if relevant else 0
    return RetrievalScore(
        **common,
        relevant=relevant,
        precision=relevant / len(hits) if hits else 0.0,
        hit=relevant > 0,
        reciprocal_rank=1 / first if first else 0.0,
        ok=relevant > 0,
    )


@dataclass(frozen=True)
class RetrievalSummary:
    cases: int
    scored: int  # cases that expect results (precision and MRR are over these)
    precision: float | None
    hit_rate: float | None
    mrr: float | None
    empty_cases: int  # cases that expect nothing
    empty_correct: int  # of those, how many returned nothing

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mean(values: Iterable[float | None]) -> float | None:
    numbers = [v for v in values if v is not None]
    return sum(numbers) / len(numbers) if numbers else None


def summarise_retrieval(scores: Sequence[RetrievalScore]) -> RetrievalSummary:
    scored = [s for s in scores if not s.expect_empty]
    empty = [s for s in scores if s.expect_empty]
    return RetrievalSummary(
        cases=len(scores),
        scored=len(scored),
        precision=_mean(s.precision for s in scored),
        hit_rate=_mean(float(s.hit) for s in scored if s.hit is not None),
        mrr=_mean(s.reciprocal_rank for s in scored),
        empty_cases=len(empty),
        empty_correct=sum(s.ok for s in empty),
    )


# ── roadmap scores ───────────────────────────────────────────


class Check(NamedTuple):
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class RoadmapScore:
    case_id: str
    tags: tuple[str, ...]
    split: str
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    @property
    def ok(self) -> bool:
        return not self.failed


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def plan_links(plan: Mapping[str, Any]) -> list[str]:
    """Every link in the plan's weeks, in order, repeats included."""
    links: list[str] = []
    for week in plan.get("weeks") or []:
        for resource in week.get("resources") or []:
            url = str(resource.get("url") or "").strip()
            if url:
                links.append(url)
    return links


def score_roadmap(
    case: RoadmapCase,
    plan: Mapping[str, Any],
    *,
    retrieved_urls: Collection[str],
    top_ranked_url: str | None,
) -> RoadmapScore:
    """Check one plan against its case.

    `retrieved_urls` is every page the agent retrieved for this case, and
    `top_ranked_url` the page it ranked first (None when it retrieved nothing). Both come
    from the agent's retrieval step, which is run without the model.
    """
    expected = case.expected
    checks: list[Check] = []

    def add(name: str, passed: bool, detail: str = "") -> None:
        checks.append(Check(name, bool(passed), "" if passed else detail))

    links = plan_links(plan)
    distinct = list(dict.fromkeys(links))
    sources = [str(s.get("url") or "") for s in plan.get("sources") or []]
    notice = plan.get("notice")
    weeks = plan.get("weeks") or []

    # Always checked
    add(
        "model_answered",
        not (plan.get("_parse_error") or plan.get("_error_reason")),
        "the plan is the fallback: the model call or its reply failed",
    )
    retrieved = set(retrieved_urls)
    invented = [u for u in distinct if u not in retrieved]
    add("no_invented_links", not invented, f"not retrieved: {invented[:3]}")
    stray = [u for u in sources if u not in set(distinct)]
    add(
        "sources_match_links",
        not stray,
        f"source without a link in the plan: {stray[:3]}",
    )
    add(
        "sources_and_notice_exclusive",
        not (sources and notice),
        "the plan has both sources and a not-reviewed notice",
    )

    # Per case
    if expected.empty_plan:
        add(
            "empty_plan",
            not weeks and plan.get("total_weeks") == 0,
            f"expected no weeks, got {len(weeks)}",
        )
    if expected.expect_grounded is True:
        add(
            "grounded",
            bool(sources) and not notice and bool(distinct),
            f"sources={len(sources)}, links={len(distinct)}, notice={bool(notice)}",
        )
    elif expected.expect_grounded is False:
        add(
            "not_grounded",
            bool(notice) and not sources and not distinct,
            f"notice={bool(notice)}, sources={len(sources)}, links={len(distinct)}",
        )
    add(
        "link_count",
        expected.min_links <= len(distinct) <= expected.max_links,
        f"{len(distinct)} links, wanted {expected.min_links} to {expected.max_links}",
    )
    if expected.link_hosts_any:
        hosts = {host_of(u) for u in distinct}
        add(
            "link_hosts",
            any(w in h for w in expected.link_hosts_any for h in hosts),
            f"hosts {sorted(hosts)}, wanted one of {expected.link_hosts_any}",
        )
    if expected.top_ranked_hosts_any:
        top = host_of(top_ranked_url or "")
        add(
            "top_ranked_host",
            any(w in top for w in expected.top_ranked_hosts_any),
            f"best page was {top or 'nothing'}, wanted one of {expected.top_ranked_hosts_any}",
        )
    if expected.must_not_contain:
        text = json.dumps(plan, ensure_ascii=False).lower()
        found = [w for w in expected.must_not_contain if w.lower() in text]
        add("must_not_contain", not found, f"found: {found}")

    return RoadmapScore(
        case_id=case.id, tags=tuple(case.tags), split=case.split, checks=tuple(checks)
    )


@dataclass(frozen=True)
class RoadmapSummary:
    cases: int
    passed: int
    pass_rate: float | None
    failed_checks: dict[str, int]  # check name -> how many cases failed it

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarise_roadmap(scores: Sequence[RoadmapScore]) -> RoadmapSummary:
    failures = Counter(c.name for s in scores for c in s.failed)
    passed = sum(s.ok for s in scores)
    return RoadmapSummary(
        cases=len(scores),
        passed=passed,
        pass_rate=passed / len(scores) if scores else None,
        failed_checks=dict(sorted(failures.items())),
    )


# ── reports: all, each split, each tag ───────────────────────


def _groups(scores: Sequence[Any]) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = {"all": list(scores)}
    for split in sorted({s.split for s in scores}):
        groups[f"split={split}"] = [s for s in scores if s.split == split]
    for tag in sorted({t for s in scores for t in s.tags}):
        groups[f"tag={tag}"] = [s for s in scores if tag in s.tags]
    return groups


def report_retrieval(
    scores: Sequence[RetrievalScore],
) -> dict[str, RetrievalSummary]:
    return {k: summarise_retrieval(v) for k, v in _groups(scores).items()}


def report_roadmap(scores: Sequence[RoadmapScore]) -> dict[str, RoadmapSummary]:
    return {k: summarise_roadmap(v) for k, v in _groups(scores).items()}


# ── comparing two runs ───────────────────────────────────────


class Delta(NamedTuple):
    group: str
    metric: str
    before: float
    after: float

    @property
    def change(self) -> float:
        return self.after - self.before


def deltas(
    before: Mapping[str, Mapping[str, Any]], after: Mapping[str, Mapping[str, Any]]
) -> list[Delta]:
    """Every number that is in both reports, with its old and new value.

    Each report is `{group: summary.as_dict()}`, the shape the runners save. A group or
    metric that is missing on either side, or is None, is skipped. Counts (cases, passed)
    are skipped too: only rates and means say whether quality moved.
    """
    skip = {"cases", "scored", "empty_cases", "empty_correct", "passed"}
    out: list[Delta] = []
    for group in sorted(set(before) & set(after)):
        for metric in sorted(set(before[group]) & set(after[group])):
            old, new = before[group][metric], after[group][metric]
            if (
                metric in skip
                or isinstance(old, (dict, bool))
                or old is None
                or new is None
            ):
                continue
            if isinstance(old, (int, float)) and isinstance(new, (int, float)):
                out.append(Delta(group, metric, float(old), float(new)))
    return out


# ── printing ─────────────────────────────────────────────────


def render_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Plain-text table with aligned columns. None shows as a dash."""
    cells = [[("-" if c is None else str(c)) for c in row] for row in rows]
    widths = [
        max(len(h), *(len(r[i]) for r in cells)) if cells else len(h)
        for i, h in enumerate(headers)
    ]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths))]
    lines.append("  ".join("-" * w for w in widths))
    lines.extend("  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in cells)
    return "\n".join(lines)


def fmt(value: float | None) -> str | None:
    """Two decimals, or None (shown as a dash)."""
    return None if value is None else f"{value:.2f}"
