"""Golden-set cases: what a case looks like, and how to load and check a dataset.

A golden set is a small list of inputs with hand-checked expected outcomes. After a
change (a prompt, a model, a retrieval setting, a new source) we run the real system over
it and compare the scores with the baseline. This file only reads and checks the cases.
It runs nothing and needs no network, no ChromaDB and no model.

Where the files live (one JSON object per line):

    backend/evals/datasets/<folder>/<name>.v<N>.jsonl

    knowledge/retrieval.v1.jsonl   does search find the right pages?
    roadmap/links.v1.jsonl         does the learning plan link only to retrieved pages?

A published version is never edited. A change becomes `v2` with a line in the README.

Every case has the same outer shape:

    {"id": "ret-001", "area": "retrieval", "tags": ["technical", "clear"],
     "split": "dev", "input": {...}, "expected": {...},
     "notes": "why this case exists", "source": "hand-written", "added": "2026-11-23"}

`input` and `expected` depend on the area (see the models below). Unknown keys are
errors, so a typo in a label cannot quietly turn a check off.

Check every dataset (run from backend/):

    python -m evals.cases
    python -m evals.cases --require-reviewed     # before freezing a version

Cases hold no real people, companies or text copied from a site. Retrieval cases say what
a relevant page looks like (keywords, a source id, a domain) and never a chunk id, because
chunk ids change when the knowledge base is rebuilt.
"""

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from app.ingest.schema import (
    INTERVIEW_COLLECTION,
    LEARNING_COLLECTION,
    LEVELS,
    ROLE_FAMILIES,
)

DATASETS_DIR = Path(__file__).parent / "datasets"

# area -> (folder under datasets/, file name without ".v<N>.jsonl")
DATASETS: dict[str, tuple[str, str]] = {
    "retrieval": ("knowledge", "retrieval"),
    "roadmap": ("roadmap", "links"),
}

# A case id is "<prefix>-<number>", and the prefix says which area it belongs to.
ID_PREFIX = {"retrieval": "ret", "roadmap": "rdm"}
ID_PATTERN = re.compile(r"^[a-z]{3}-\d{3}$")

SPLITS = ("dev", "test")  # tune on dev; look at test only to confirm a change

# hand-written: a person wrote it and checked the expected values.
# model-draft:  Claude wrote it. It does not count until a person has checked it.
# reviewed-draft: a model-draft that a person has since checked line by line.
SOURCES = ("hand-written", "model-draft", "reviewed-draft")

ROLE_TAGS = ("technical", "non-technical")  # every case has exactly one of these
ALLOWED_TAGS = (
    *ROLE_TAGS,
    "clear",  # an obvious right answer
    "borderline",  # a reasonable person could argue either way
    "messy",  # typos, odd formatting, very short or very long input
    "empty-input",
    "no-match",  # nothing in our knowledge should match
    "regression",  # a bug we really hit
    "safety",  # instruction-like text that must be ignored
    "fairness",  # one of a pair that differs only in wording or casing
    "interview",
    "learning",
    "ranking",
    "coverage",
    "fallback",
)

# The learning plan never links to more than this many pages (RoadmapAgent keeps 12).
MAX_LINKS_CEILING = 12


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _tidy(values: list[str], what: str, *, lower: bool) -> list[str]:
    """Strip each value. An empty or repeated value is a mistake, not something to skip."""
    tidy: list[str] = []
    for value in values:
        value = value.strip()
        if not value:
            raise ValueError(f"{what} holds an empty value")
        tidy.append(value.lower() if lower else value)
    if len(set(tidy)) != len(tidy):
        raise ValueError(f"{what} repeats a value")
    return tidy


# ── retrieval ────────────────────────────────────────────────


class RetrievalInput(_Strict):
    """One search. The query may be empty or blank on purpose (an `empty-input` case)."""

    query: str
    collection: str
    role_family: str | None = None
    level: str | None = None
    domain: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("collection")
    @classmethod
    def _collection(cls, v: str) -> str:
        if v not in (INTERVIEW_COLLECTION, LEARNING_COLLECTION):
            raise ValueError(
                f"collection must be {INTERVIEW_COLLECTION} or {LEARNING_COLLECTION}"
            )
        return v

    @field_validator("role_family")
    @classmethod
    def _role_family(cls, v: str | None) -> str | None:
        if v is not None and v not in ROLE_FAMILIES:
            raise ValueError(f"role_family must be one of {ROLE_FAMILIES}")
        return v

    @field_validator("level")
    @classmethod
    def _level(cls, v: str | None) -> str | None:
        if v is not None and v not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}")
        return v

    @field_validator("domain")
    @classmethod
    def _domain(cls, v: str | None) -> str | None:
        return v.strip().lower() if v is not None else None


class RetrievalExpected(_Strict):
    """What counts as a relevant result, or that nothing should come back.

    A returned chunk is relevant when ANY of these hold: its title or text contains one of
    `relevant_keywords` (any letter case), its `source_id` is in `relevant_source_ids`, or
    its `domain` is in `relevant_domains`. Precision@K is the share of returned chunks
    that are relevant, and MRR looks at the rank of the first relevant one.
    """

    expect_empty: bool = False
    relevant_keywords: list[str] = Field(default_factory=list)
    relevant_source_ids: list[str] = Field(default_factory=list)
    relevant_domains: list[str] = Field(default_factory=list)

    @field_validator("relevant_keywords", "relevant_source_ids", "relevant_domains")
    @classmethod
    def _lower(cls, v: list[str], info) -> list[str]:
        return _tidy(v, info.field_name, lower=True)

    @model_validator(mode="after")
    def _one_of_the_two(self):
        defined = (
            self.relevant_keywords or self.relevant_source_ids or self.relevant_domains
        )
        if self.expect_empty and defined:
            raise ValueError("expect_empty cannot also name relevant results")
        if not self.expect_empty and not defined:
            raise ValueError(
                "name what a relevant result looks like "
                "(relevant_keywords, relevant_source_ids or relevant_domains), "
                "or set expect_empty"
            )
        return self


# ── roadmap ──────────────────────────────────────────────────


class RoadmapInput(_Strict):
    """What `RoadmapAgent.generate()` gets. A blank skill is allowed (a `messy` case)."""

    skill_gaps: list[str]
    target_role: str


class RoadmapExpected(_Strict):
    """Properties of the plan. Never the plan's text, which changes with every run.

    Checked on every case, whatever is written here: every link in the plan was
    retrieved by the agent (no invented link), and `sources` lists only pages that the
    plan links to.

    expect_grounded    True: the plan links to reviewed pages, has `sources` and no
                       `notice`. False: it has a `notice`, no `sources` and no links.
                       None: do not check.
    min_links          fewest different links the plan must contain.
    max_links          most different links it may contain.
    link_hosts_any     at least one link must be on a host containing one of these.
    top_ranked_hosts_any  the agent's best-ranked page (checked before the model writes
                       anything) must be on a host containing one of these. This is
                       what protects the trust and freshness weights.
    must_not_contain   words that must not appear anywhere in the plan (a canary that an
                       injected instruction would make the model repeat).
    empty_plan         there are no skill gaps, so the plan has no weeks.
    """

    empty_plan: bool = False
    expect_grounded: bool | None = None
    min_links: int = Field(default=0, ge=0, le=MAX_LINKS_CEILING)
    max_links: int = Field(default=MAX_LINKS_CEILING, ge=1, le=MAX_LINKS_CEILING)
    link_hosts_any: list[str] = Field(default_factory=list)
    top_ranked_hosts_any: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)

    @field_validator("link_hosts_any", "top_ranked_hosts_any")
    @classmethod
    def _hosts(cls, v: list[str], info) -> list[str]:
        return _tidy(v, info.field_name, lower=True)

    @field_validator("must_not_contain")
    @classmethod
    def _canaries(cls, v: list[str]) -> list[str]:
        return _tidy(v, "must_not_contain", lower=False)

    @model_validator(mode="after")
    def _consistent(self):
        wants_links = bool(
            self.min_links or self.link_hosts_any or self.top_ranked_hosts_any
        )
        if self.min_links > self.max_links:
            raise ValueError("min_links is more than max_links")
        if (self.link_hosts_any or self.top_ranked_hosts_any) and not self.min_links:
            raise ValueError("naming hosts needs a min_links of at least 1")
        if self.empty_plan and (wants_links or self.expect_grounded is not None):
            raise ValueError("an empty plan has no links or sources to check")
        if self.expect_grounded is False and wants_links:
            raise ValueError("a plan that is not grounded has no reviewed links")
        return self


# ── one case ─────────────────────────────────────────────────


class _CaseBase(_Strict):
    id: str
    area: str  # each case class narrows this to its own area
    tags: list[str]
    split: Literal["dev", "test"]
    notes: str  # why the case exists and what it protects
    source: Literal["hand-written", "model-draft", "reviewed-draft"]
    added: date
    # A fairness pair: two cases that differ only in wording or casing. The partner must
    # name this case back and sit in the same split, or tuning on one leaks into the other.
    pair_with: str | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID_PATTERN.match(v):
            raise ValueError(
                "id looks like 'ret-001' (three letters, dash, three digits)"
            )
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        v = _tidy(v, "tags", lower=True)
        unknown = [t for t in v if t not in ALLOWED_TAGS]
        if unknown:
            raise ValueError(
                f"unknown tag {unknown}. Add it to ALLOWED_TAGS on purpose, "
                "so a typo does not start a new slice"
            )
        roles = [t for t in v if t in ROLE_TAGS]
        if len(roles) != 1:
            raise ValueError(
                "give exactly one of the tags 'technical' or 'non-technical'"
            )
        return v

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: str) -> str:
        if len(v.strip()) < 20:
            raise ValueError(
                "notes must say why this case exists (20 characters or more)"
            )
        return v.strip()

    @field_validator("pair_with")
    @classmethod
    def _pair(cls, v: str | None) -> str | None:
        if v is not None and not ID_PATTERN.match(v):
            raise ValueError("pair_with must be a case id")
        return v

    @model_validator(mode="after")
    def _id_matches_area(self):
        prefix = ID_PREFIX[self.area]
        if not self.id.startswith(f"{prefix}-"):
            raise ValueError(f"a {self.area} case has an id like '{prefix}-001'")
        if self.pair_with is not None and not self.pair_with.startswith(f"{prefix}-"):
            raise ValueError("pair_with must be a case of the same area")
        return self


class RetrievalCase(_CaseBase):
    area: Literal["retrieval"]
    input: RetrievalInput
    expected: RetrievalExpected


class RoadmapCase(_CaseBase):
    area: Literal["roadmap"]
    input: RoadmapInput
    expected: RoadmapExpected


Case = Annotated[Union[RetrievalCase, RoadmapCase], Field(discriminator="area")]
_CASE_ADAPTER: TypeAdapter[Case] = TypeAdapter(Case)


class DatasetError(ValueError):
    """A dataset that cannot be used. `problems` lists every mistake found, so one run
    shows them all instead of one at a time."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


# ── reading ──────────────────────────────────────────────────


def _where(error: dict) -> str:
    place = ".".join(str(part) for part in error["loc"] if part != "")
    message = error["msg"].removeprefix("Value error, ")
    return f"{place}: {message}" if place else message


def read_cases(path: Path) -> list[Case]:
    """Read one JSONL file and check every case in it.

    Raises DatasetError with every problem found (line numbers included) when anything is
    wrong: bad JSON, an unknown key, a repeated id, a broken fairness pair, an empty file.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetError([f"{path}: cannot read ({exc.strerror or exc})"]) from exc

    name = Path(path).name
    problems: list[str] = []
    cases: list[Case] = []
    first_line: dict[str, int] = {}

    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"{name}:{number}: not valid JSON ({exc.msg})")
            continue
        try:
            case = _CASE_ADAPTER.validate_python(raw)
        except ValidationError as exc:
            problems.extend(f"{name}:{number}: {_where(e)}" for e in exc.errors())
            continue
        if case.id in first_line:
            problems.append(
                f"{name}:{number}: id {case.id} is already used on line {first_line[case.id]}"
            )
            continue
        first_line[case.id] = number
        cases.append(case)

    if not problems and not cases:
        problems.append(f"{name}: no cases")
    problems.extend(f"{name}: {p}" for p in _pair_problems(cases))
    if problems:
        raise DatasetError(problems)
    return cases


def _pair_problems(cases: Sequence[Case]) -> list[str]:
    by_id = {c.id: c for c in cases}
    problems: list[str] = []
    for case in cases:
        if case.pair_with is None:
            continue
        partner = by_id.get(case.pair_with)
        if partner is None:
            problems.append(
                f"{case.id}: pair_with {case.pair_with} is not in this file"
            )
        elif partner.pair_with != case.id:
            problems.append(f"{case.id}: {partner.id} does not name {case.id} back")
        elif partner.split != case.split:
            problems.append(f"{case.id}: its pair {partner.id} is in the other split")
        if "fairness" not in case.tags:
            problems.append(f"{case.id}: a paired case needs the 'fairness' tag")
    return problems


# ── finding and loading a dataset ────────────────────────────

_VERSION_FILE = re.compile(r"^(?P<name>[a-z0-9_-]+)\.v(?P<number>\d+)\.jsonl$")


def dataset_path(area: str, version: int | None = None) -> Path:
    """The file for an area. With no version, the highest one that exists."""
    if area not in DATASETS:
        raise DatasetError([f"unknown area '{area}'. Known: {', '.join(DATASETS)}"])
    folder, name = DATASETS[area]
    directory = DATASETS_DIR / folder

    found: dict[int, Path] = {}
    for path in directory.glob(f"{name}.v*.jsonl"):
        match = _VERSION_FILE.match(path.name)
        if match and match["name"] == name:
            found[int(match["number"])] = path
    if not found:
        raise DatasetError([f"no {name}.v<N>.jsonl in {directory}"])
    if version is None:
        return found[max(found)]
    if version not in found:
        have = ", ".join(f"v{n}" for n in sorted(found))
        raise DatasetError([f"{name} has no v{version} (have {have})"])
    return found[version]


def load_dataset(area: str, version: int | None = None) -> list[Case]:
    """Load and check the dataset for an area (latest version unless one is given)."""
    path = dataset_path(area, version)
    cases = read_cases(path)
    wrong = [c.id for c in cases if c.area != area]
    if wrong:
        raise DatasetError([f"{path.name}: cases of another area: {', '.join(wrong)}"])
    return cases


def select(
    cases: Iterable[Case], *, split: str | None = None, tags: Sequence[str] = ()
) -> list[Case]:
    """Keep the cases in one split and/or holding ALL of the given tags."""
    if split is not None and split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    return [
        c
        for c in cases
        if (split is None or c.split == split) and all(t in c.tags for t in tags)
    ]


def unreviewed(cases: Iterable[Case]) -> list[Case]:
    """Model-written cases that no person has checked yet. They must not be frozen."""
    return [c for c in cases if c.source == "model-draft"]


def tag_counts(cases: Iterable[Case]) -> Counter:
    return Counter(tag for c in cases for tag in c.tags)


# ── command line ─────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.cases", description="Check the golden-set datasets."
    )
    parser.add_argument(
        "--area", choices=sorted(DATASETS), help="one area (default: all)"
    )
    parser.add_argument(
        "--version", type=int, help="a version number (default: latest)"
    )
    parser.add_argument(
        "--require-reviewed",
        action="store_true",
        help="fail while any case is still an unchecked model-draft",
    )
    args = parser.parse_args(argv)

    areas = [args.area] if args.area else sorted(DATASETS)
    failed = False
    for area in areas:
        try:
            path = dataset_path(area, args.version)
            cases = load_dataset(area, args.version)
        except DatasetError as exc:
            failed = True
            print(f"{area}: PROBLEMS", file=sys.stderr)
            for problem in exc.problems:
                print(f"  {problem}", file=sys.stderr)
            continue

        test = sum(1 for c in cases if c.split == "test")
        drafts = unreviewed(cases)
        print(f"{area}: {path.name}, {len(cases)} cases")
        print(
            f"  split:   dev {len(cases) - test}, test {test} ({test / len(cases):.0%})"
        )
        print(f"  source:  {dict(Counter(c.source for c in cases))}")
        print(f"  tags:    {dict(sorted(tag_counts(cases).items()))}")
        if drafts:
            print(f"  {len(drafts)} model-draft case(s) not yet checked by a person")
            if args.require_reviewed:
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
