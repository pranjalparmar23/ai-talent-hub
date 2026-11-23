"""Tests for evals.run_roadmap, with a fake planner. No ChromaDB, no model, no network.

`build_planner` (the real planner) is not tested here: it needs ChromaDB and a Groq key,
and is covered by running the runner itself. Everything around it is.

`main()` runs its own event loop, so the tests of `main` are plain (not async) functions.
"""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from evals import run_roadmap as rm
from evals.metrics import report_roadmap
from evals.run_roadmap import (
    Attempt,
    CaseRun,
    case_label,
    check_setting,
    default_of,
    describe,
    format_report,
    format_sweep,
    main,
    parse_pair,
    parse_sweep,
    run_cases,
    score_attempt,
    score_ranking,
)
from tests.unit.evals._builders import (
    make_plan,
    roadmap_case,
    roadmap_dict,
    write_dataset,
)

NOW = datetime(2026, 11, 23, 9, 0, 0, tzinfo=timezone.utc)
K8S = "https://kubernetes.io/docs/a"
ROADMAP = "https://roadmap.sh/kubernetes"
SIZES = {"interview_knowledge": 100, "learning_knowledge": 50}
SETTINGS = {
    "model": "test-model",
    "rank_weights": {"base": 0.6, "trust": 0.3, "fresh": 0.1},
    "source_trust": {"official-docs": 1.0, "freecodecamp": 0.8},
}
# the page each dataset case ranks first, unless a test says otherwise
TOP = {"rdm-001": K8S, "rdm-002": K8S, "rdm-003": ROADMAP}


def good_attempt(case, *, ranking_only=False, top=None):
    top = top or TOP.get(case.id, K8S)
    if ranking_only:
        return Attempt(retrieved_urls=(K8S, ROADMAP), top_ranked_url=top)
    return Attempt(
        plan=make_plan([top]), retrieved_urls=(K8S, ROADMAP), top_ranked_url=top
    )


class FakeFactory:
    """A planner factory. `behave(overrides, ranking_only, case)` returns an Attempt."""

    def __init__(self, behave=None, *, fail=None):
        self.behave = behave or (
            lambda overrides, ranking_only, case: good_attempt(
                case, ranking_only=ranking_only
            )
        )
        self.fail = fail
        self.calls: list[tuple[dict, bool]] = []
        self.planned: list[str] = []

    def __call__(self, overrides, ranking_only):
        self.calls.append((dict(overrides), ranking_only))
        if self.fail:
            raise self.fail
        overrides = dict(overrides)

        async def planner(case):
            self.planned.append(case.id)
            return self.behave(overrides, ranking_only, case)

        return planner, {**SETTINGS}, dict(SIZES)


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    rows = [
        roadmap_dict(
            "rdm-001",
            tags=("technical", "ranking"),
            expected={
                "expect_grounded": True,
                "min_links": 1,
                "top_ranked_hosts_any": ["kubernetes.io"],
            },
        ),
        roadmap_dict(
            "rdm-002",
            tags=("non-technical", "clear"),
            split="test",
            source="model-draft",
        ),
        roadmap_dict(
            "rdm-003",
            tags=("non-technical", "ranking", "borderline"),
            expected={
                "expect_grounded": True,
                "min_links": 1,
                "top_ranked_hosts_any": ["roadmap.sh"],
            },
        ),
    ]
    return write_dataset(tmp_path, monkeypatch, "roadmap", rows)


def run(argv, factory, tmp_path, capsys):
    code = main(
        ["--delay", "0", *argv],
        planner_factory=factory,
        now=lambda: NOW,
        results_dir=tmp_path / "results",
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ── scoring an attempt ───────────────────────────────────────


def test_score_attempt_runs_every_check():
    case = roadmap_case(
        expected={"min_links": 1, "top_ranked_hosts_any": ["kubernetes.io"]}
    )
    score = score_attempt(case, good_attempt(case))
    assert score.ok
    assert "top_ranked_host" in {c.name for c in score.checks}
    assert "model_answered" in {c.name for c in score.checks}


def test_score_ranking_keeps_only_the_ranking_check():
    case = roadmap_case(
        expected={"min_links": 1, "top_ranked_hosts_any": ["kubernetes.io"]}
    )
    # an empty plan would fail grounded and link_count, but ranking-only has no plan
    attempt = Attempt(retrieved_urls=(K8S,), top_ranked_url=K8S)
    score = score_ranking(case, attempt)
    assert [c.name for c in score.checks] == ["top_ranked_host"]
    assert score.ok
    wrong = score_ranking(
        case, Attempt(retrieved_urls=(ROADMAP,), top_ranked_url=ROADMAP)
    )
    assert not wrong.ok


# ── running cases ────────────────────────────────────────────


async def test_each_case_runs_repeat_times_and_every_run_is_scored():
    case = roadmap_case()
    seen = []

    async def planner(c):
        seen.append(c.id)
        return good_attempt(c)

    result = await run_cases([case], planner, repeat=3)
    assert seen == ["rdm-001"] * 3
    (run_,) = result.ran
    assert (run_.passed, len(run_.scores), run_.ok) == (3, 3, True)
    assert len(result.scores) == 3  # a repeated case counts once per run


async def test_a_case_passes_only_if_every_run_passes():
    case = roadmap_case(
        expected={"min_links": 1, "top_ranked_hosts_any": ["kubernetes.io"]}
    )
    tops = iter([K8S, ROADMAP, K8S])

    async def planner(c):
        return good_attempt(c, top=next(tops))

    result = await run_cases([case], planner, repeat=3)
    (run_,) = result.ran
    assert run_.passed == 2 and run_.ok is False


async def test_a_delay_is_waited_between_runs_but_not_before_the_first(monkeypatch):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(rm.asyncio, "sleep", fake_sleep)

    async def planner(c):
        return good_attempt(c)

    cases = [roadmap_case("rdm-001"), roadmap_case("rdm-002")]
    await run_cases(cases, planner, repeat=2, delay=1.5)
    assert waits == [1.5, 1.5, 1.5]  # four runs, three gaps


async def test_no_delay_means_no_sleep(monkeypatch):
    async def fail(seconds):
        raise AssertionError("slept")

    monkeypatch.setattr(rm.asyncio, "sleep", fail)

    async def planner(c):
        return good_attempt(c)

    await run_cases([roadmap_case()], planner, repeat=2, delay=0)


async def test_a_run_that_takes_too_long_is_stopped_and_not_scored():
    async def stuck(case):
        await asyncio.sleep(10)

    result = await run_cases([roadmap_case()], stuck, timeout=0.01)
    assert result.ran == []
    assert result.not_run == [("rdm-001", "timed out after 0.01 seconds")]


async def test_a_failed_first_run_means_the_case_is_not_scored():
    async def planner(case):
        return Attempt(error="retrieval failed: down")

    calls = []
    result = await run_cases(
        [roadmap_case()], planner, repeat=3, progress=lambda *a: calls.append(a)
    )
    assert result.ran == []
    assert result.not_run == [("rdm-001", "retrieval failed: down")]
    assert len(calls) == 1  # it did not try the other two


async def test_a_later_failure_keeps_the_runs_that_worked_and_says_which_failed():
    outcomes = iter([None, Attempt(error="rate limited")])

    async def planner(case):
        outcome = next(outcomes)
        return outcome or good_attempt(case)

    result = await run_cases([roadmap_case()], planner, repeat=3)
    (run_,) = result.ran
    assert len(run_.scores) == 1  # a run lost half way is not counted as a bad plan
    assert result.not_run == [("rdm-001", "run 2: rate limited")]


async def test_progress_is_told_after_every_run():
    told = []

    async def planner(case):
        return good_attempt(case)

    cases = [roadmap_case("rdm-001"), roadmap_case("rdm-002")]
    await run_cases(
        cases,
        planner,
        repeat=2,
        progress=lambda done, total, case, seconds, error: told.append(
            (done, total, case.id, seconds >= 0, error)
        ),
    )
    assert told == [
        (1, 4, "rdm-001", True, ""),
        (2, 4, "rdm-001", True, ""),
        (3, 4, "rdm-002", True, ""),
        (4, 4, "rdm-002", True, ""),
    ]


async def test_a_custom_scorer_is_used():
    async def planner(case):
        return good_attempt(case)

    result = await run_cases([roadmap_case()], planner, scorer=score_ranking)
    assert result.scores[0].checks == ()


# ── settings that can be tried ───────────────────────────────


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("base_weight", 0),
        ("trust_weight", 2),
        ("fresh_weight", 0.5),
        ("trust.official-docs", 0.7),
    ],
)
def test_check_setting_accepts(name, value):
    assert check_setting(name, value) == float(value)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("trust_weight", 2.1, "between 0 and 2"),
        ("base_weight", -0.1, "between 0 and 2"),
        ("trust.official-docs", 1.1, "between 0 and 1"),
        ("trust.", 0.5, "unknown setting"),
        ("temperature", 0.5, "unknown setting"),
    ],
)
def test_check_setting_rejects(name, value, message):
    with pytest.raises(ValueError, match=message):
        check_setting(name, value)


def test_parse_pair():
    assert parse_pair(" freecodecamp = 0.5 ", "--x") == ("freecodecamp", "0.5")


@pytest.mark.parametrize("text", ["freecodecamp", "=0.5", "freecodecamp=", " = "])
def test_parse_pair_rejects(text):
    with pytest.raises(ValueError, match="--source-trust looks like NAME=VALUE"):
        parse_pair(text, "--source-trust")


def test_parse_sweep_turns_dashes_into_underscores_except_in_source_ids():
    assert parse_sweep("trust-weight=0.3,0.5,0.3") == ("trust_weight", [0.3, 0.5])
    assert parse_sweep("trust.official-docs=1,0.5") == (
        "trust.official-docs",
        [1.0, 0.5],
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("trust_weight", "NAME=VALUE"),
        ("trust_weight=high", "is not a number"),
        ("trust_weight=3", "between"),
        ("nonsense=1", "unknown setting"),
        ("trust_weight=" + ",".join(str(i / 10) for i in range(9)), "1 to 8"),
    ],
)
def test_parse_sweep_rejects(text, message):
    with pytest.raises(ValueError, match=message):
        parse_sweep(text)


# ── applying ranking settings to the agent ───────────────────


@pytest.fixture
def agent_module(monkeypatch):
    monkeypatch.setattr(rm, "_ORIGINAL", {})
    return SimpleNamespace(
        RANK_BASE_WEIGHT=0.6,
        RANK_TRUST_WEIGHT=0.3,
        RANK_FRESH_WEIGHT=0.1,
        SOURCE_TRUST={"official-docs": 1.0, "freecodecamp": 0.8},
    )


def test_overrides_change_the_agents_constants(agent_module):
    rm._apply_ranking(agent_module, {"trust_weight": 0.5, "trust.freecodecamp": 0.6})
    assert agent_module.RANK_TRUST_WEIGHT == 0.5
    assert agent_module.RANK_BASE_WEIGHT == 0.6
    assert agent_module.SOURCE_TRUST == {"official-docs": 1.0, "freecodecamp": 0.6}


def test_each_call_starts_again_from_the_agents_own_values(agent_module):
    """A sweep calls this many times. A value must not carry over to the next call."""
    rm._apply_ranking(agent_module, {"trust_weight": 0.5, "trust.freecodecamp": 0.6})
    rm._apply_ranking(agent_module, {"base_weight": 0.9})
    assert agent_module.RANK_TRUST_WEIGHT == 0.3
    assert agent_module.RANK_BASE_WEIGHT == 0.9
    assert agent_module.SOURCE_TRUST["freecodecamp"] == 0.8
    rm._apply_ranking(agent_module, {})
    assert agent_module.RANK_BASE_WEIGHT == 0.6


def test_the_trust_table_is_changed_in_place(agent_module):
    """The agent holds a reference to the dict, so it must be the same object."""
    table = agent_module.SOURCE_TRUST
    rm._apply_ranking(agent_module, {"trust.official-docs": 0.4})
    assert agent_module.SOURCE_TRUST is table
    assert table["official-docs"] == 0.4


def test_an_unknown_source_is_an_error_that_lists_the_known_ones(agent_module):
    with pytest.raises(
        ValueError, match="unknown source 'nope'. Known: freecodecamp, official-docs"
    ):
        rm._apply_ranking(agent_module, {"trust.nope": 0.5})


def test_default_of():
    assert default_of(SETTINGS, "trust_weight") == 0.3
    assert default_of(SETTINGS, "base_weight") == 0.6
    assert default_of(SETTINGS, "fresh_weight") == 0.1
    assert default_of(SETTINGS, "trust.freecodecamp") == 0.8
    assert default_of(SETTINGS, "trust.unknown") is None
    assert default_of({}, "trust_weight") is None


# ── printing ─────────────────────────────────────────────────


def test_case_label_shows_skills_role_and_tags():
    case = roadmap_case(skills=("Docker", "  ", "Git"), role="DevOps")
    assert case_label(case) == "rdm-001 [technical clear] Docker, (blank), Git / DevOps"


def test_case_label_with_no_skills():
    assert "(none)" in case_label(roadmap_case(skills=()))


def test_case_label_shortens_long_text():
    label = case_label(roadmap_case(skills=("x" * 200,), role="y" * 200))
    assert label.count("…") == 2


def failing_run(*, runs=1, fail=1):
    case = roadmap_case(
        expected={"min_links": 1, "top_ranked_hosts_any": ["kubernetes.io"]}
    )
    attempts = [
        good_attempt(case, top=ROADMAP if i < fail else K8S) for i in range(runs)
    ]
    return CaseRun(case, attempts, [score_attempt(case, a) for a in attempts])


def test_describe_names_each_failed_check_once():
    text = describe(failing_run(runs=3, fail=3))
    assert text.count("top_ranked_host:") == 1
    assert "best page was roadmap.sh" in text


def test_describe_a_single_run_needs_no_pass_count():
    assert "passed" not in describe(failing_run(runs=1, fail=1))


def test_describe_a_repeated_case_gives_the_pass_count():
    assert "(passed 1 of 2 runs)" in describe(failing_run(runs=2, fail=1))


def test_format_report_has_a_row_per_group():
    run_ = failing_run()
    text = format_report(report_roadmap(run_.scores))
    assert text.splitlines()[0].split() == ["group", "runs", "passed", "pass", "rate"]
    assert "tag=technical" in text


def test_format_sweep_marks_the_default_and_names_the_best_page_of_failures():
    passing = failing_run(fail=0)
    failing = failing_run(fail=1)
    ok = rm.RunResult(ran=[passing])
    bad = rm.RunResult(ran=[passing, failing])
    text = format_sweep("trust_weight", [(0.3, ok), (0.5, bad)], default=0.3)
    lines = text.splitlines()
    assert "0.3 (default)" in lines[2] and "1/1" in lines[2]
    assert lines[2].rstrip().endswith("none")
    assert "1/2" in lines[3] and "rdm-001 (roadmap.sh)" in lines[3]


def test_case_record_and_build_record_can_be_saved_as_json():
    run_ = failing_run(runs=2, fail=1)
    result = rm.RunResult(ran=[run_], not_run=[("rdm-009", "down")])
    record = rm.build_record(
        result,
        dataset="links.v1.jsonl",
        version=1,
        filters={"split": None, "tags": [], "repeat": 2, "ranking_only": False},
        settings=dict(SETTINGS),
        sizes=dict(SIZES),
        now=NOW,
    )
    json.dumps(record)
    assert record["area"] == "roadmap"
    assert record["not_run"] == [{"id": "rdm-009", "reason": "down"}]
    (case,) = record["cases"]
    assert case["passed_runs"] == 1 and case["ok"] is False
    first, second = case["runs"]
    assert first["ok"] is False and first["failed"][0]["check"] == "top_ranked_host"
    assert first["top_ranked_url"] == ROADMAP
    assert second["links"] == [K8S]
    assert second["notice"] is False


# ── main: a normal run ───────────────────────────────────────


def test_a_run_prints_the_report_and_exits_zero(dataset, tmp_path, capsys):
    code, out, err = run([], FakeFactory(), tmp_path, capsys)
    assert code == 0
    assert "links.v1.jsonl, 3 of 3 cases" in out
    assert "Runs per case: 1. Model: test-model" in out
    assert "Ranking weights: match 0.6, trust 0.3, freshness 0.1" in out
    assert "Source trust: official-docs 1.0, freecodecamp 0.8" in out
    assert "1 case(s) are model-drafts" in out
    assert "Every case passed." in out
    assert "tag=ranking" in out
    assert "[3/3] rdm-003" in err  # progress goes to stderr


def test_a_failing_check_is_listed_by_name_and_counted(dataset, tmp_path, capsys):
    def behave(overrides, ranking_only, case):
        return good_attempt(case, top=ROADMAP if case.id == "rdm-001" else None)

    code, out, _ = run([], FakeFactory(behave), tmp_path, capsys)
    assert code == 0  # a failing case is a finding, not an error
    assert "Checks that failed, by number of runs:\n  top_ranked_host: 1" in out
    assert "1 case(s) did not pass." in out
    assert "rdm-001 [technical ranking]" in out
    assert "best page was roadmap.sh, wanted one of ['kubernetes.io']" in out


def test_verbose_prints_a_line_per_case(dataset, tmp_path, capsys):
    def behave(overrides, ranking_only, case):
        return good_attempt(case, top=ROADMAP if case.id == "rdm-001" else None)

    _, out, _ = run(["--verbose"], FakeFactory(behave), tmp_path, capsys)
    assert "FAIL rdm-001  passed 0/1, best page roadmap.sh, up to 1 link(s)" in out
    assert "ok   rdm-002  passed 1/1" in out


def test_repeat_runs_every_case_that_many_times(dataset, tmp_path, capsys):
    factory = FakeFactory()
    _, out, err = run(["--repeat", "2"], factory, tmp_path, capsys)
    assert factory.planned == ["rdm-001"] * 2 + ["rdm-002"] * 2 + ["rdm-003"] * 2
    assert "Runs per case: 2." in out
    assert "[6/6]" in err


def test_split_and_tag_choose_the_cases(dataset, tmp_path, capsys):
    factory = FakeFactory()
    run(["--split", "test"], factory, tmp_path, capsys)
    assert factory.planned == ["rdm-002"]


def test_no_matching_case_exits_one(dataset, tmp_path, capsys):
    code, _, err = run(["--tag", "safety"], FakeFactory(), tmp_path, capsys)
    assert code == 1
    assert "No case matches these options." in err


# ── main: runs that could not be done ────────────────────────


def test_a_run_that_failed_exits_two_and_is_left_out_of_the_scores(
    dataset, tmp_path, capsys
):
    def behave(overrides, ranking_only, case):
        if case.id == "rdm-002":
            return Attempt(error="retrieval failed: down")
        return good_attempt(case)

    code, out, _ = run([], FakeFactory(behave), tmp_path, capsys)
    assert code == 2
    assert "1 run(s) could not be done" in out
    assert "rdm-002: retrieval failed: down" in out
    assert "Every case that ran passed." in out


def test_a_factory_that_cannot_start_exits_two_with_a_hint(dataset, tmp_path, capsys):
    code, _, err = run([], FakeFactory(fail=KeyError("GROQ_API_KEY")), tmp_path, capsys)
    assert code == 2
    assert "Cannot start the run: KeyError" in err
    assert "is GROQ_API_KEY set?" in err


# ── main: bad options and bad data ───────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["--repeat", "0"],
        ["--repeat", "11"],
        ["--delay", "-1"],
        ["--timeout", "-5"],
        ["--trust-weight", "0.5"],  # weights need --ranking-only
        ["--source-trust", "freecodecamp=0.5"],
        ["--sweep", "trust_weight=0.3,0.5"],  # a sweep needs --ranking-only
        ["--ranking-only", "--trust-weight", "9"],
        ["--ranking-only", "--source-trust", "freecodecamp"],
        ["--ranking-only", "--source-trust", "freecodecamp=high"],
        ["--ranking-only", "--sweep", "trust_weight=0.3", "--save"],
        ["--ranking-only", "--sweep", "trust_weight=0.3", "--compare", "x.json"],
    ],
)
def test_bad_options_exit_one_before_anything_starts(argv, dataset, tmp_path, capsys):
    factory = FakeFactory()
    code, _, err = run(argv, factory, tmp_path, capsys)
    assert code == 1
    assert err
    assert factory.calls == []


def test_a_broken_dataset_exits_one(tmp_path, monkeypatch, capsys):
    write_dataset(tmp_path, monkeypatch, "roadmap", [roadmap_dict(split="x")])
    code, _, err = run([], FakeFactory(), tmp_path, capsys)
    assert code == 1
    assert "The dataset has problems:" in err


# ── main: ranking only ───────────────────────────────────────


def test_ranking_only_runs_just_the_ranking_cases_without_the_model(
    dataset, tmp_path, capsys
):
    factory = FakeFactory()
    code, out, err = run(["--ranking-only"], factory, tmp_path, capsys)
    assert code == 0
    assert factory.planned == ["rdm-001", "rdm-003"]  # rdm-002 names no best page
    assert factory.calls == [({}, True)]
    assert "2 of 3 cases" in out
    assert "Ranking only: the model is not called." in out
    assert "Runs per case" not in out
    assert err == ""  # no progress lines: it is over in a moment
    assert "Every case passed." in out


def test_ranking_only_ignores_repeat(dataset, tmp_path, capsys):
    factory = FakeFactory()
    run(["--ranking-only", "--repeat", "5"], factory, tmp_path, capsys)
    assert factory.planned == ["rdm-001", "rdm-003"]


def test_ranking_only_verbose_shows_the_best_page(dataset, tmp_path, capsys):
    _, out, _ = run(["--ranking-only", "--verbose"], FakeFactory(), tmp_path, capsys)
    assert f"ok   rdm-001  best page: {K8S}" in out
    assert f"ok   rdm-003  best page: {ROADMAP}" in out


def test_ranking_only_reports_a_wrong_best_page(dataset, tmp_path, capsys):
    def behave(overrides, ranking_only, case):
        return good_attempt(case, ranking_only=True, top=K8S)

    code, out, _ = run(["--ranking-only"], FakeFactory(behave), tmp_path, capsys)
    assert code == 0
    assert "rdm-003" in out and "best page was kubernetes.io" in out


def test_ranking_only_with_no_ranking_case_says_so(tmp_path, monkeypatch, capsys):
    write_dataset(tmp_path, monkeypatch, "roadmap", [roadmap_dict()])
    code, _, err = run(["--ranking-only"], FakeFactory(), tmp_path, capsys)
    assert code == 1
    assert "a case must name a best page" in err


def test_overrides_reach_the_factory_and_are_printed(dataset, tmp_path, capsys):
    factory = FakeFactory()
    _, out, _ = run(
        [
            "--ranking-only",
            "--trust-weight",
            "0.5",
            "--base-weight",
            "0.7",
            "--fresh-weight",
            "0",
            "--source-trust",
            "freecodecamp=0.6",
        ],
        factory,
        tmp_path,
        capsys,
    )
    assert factory.calls == [
        (
            {
                "base_weight": 0.7,
                "trust_weight": 0.5,
                "fresh_weight": 0.0,
                "trust.freecodecamp": 0.6,
            },
            True,
        )
    ]
    assert (
        "Overrides for this run: base_weight=0.7, trust_weight=0.5, "
        "fresh_weight=0, trust.freecodecamp=0.6"
    ) in out


# ── main: sweeps ─────────────────────────────────────────────


def trust_sensitive(overrides, ranking_only, case):
    """rdm-003 (a community roadmap should win) breaks once trust is weighted 0.5+."""
    trust = overrides.get("trust_weight", 0.3)
    top = K8S if case.id == "rdm-003" and trust >= 0.5 else TOP[case.id]
    return good_attempt(case, ranking_only=True, top=top)


def test_a_sweep_prints_one_row_per_value_and_marks_the_default(
    dataset, tmp_path, capsys
):
    code, out, _ = run(
        ["--ranking-only", "--split", "dev", "--sweep", "trust_weight=0.3,0.4,0.5"],
        FakeFactory(trust_sensitive),
        tmp_path,
        capsys,
    )
    assert code == 0
    assert "Sweep of trust_weight over 2 ranking cases:" in out
    rows = {
        line.split()[0]: line
        for line in out.splitlines()
        if line[:3] in {"0.3", "0.4", "0.5"}
    }
    assert "(default)" in rows["0.3"] and rows["0.3"].rstrip().endswith("none")
    assert rows["0.4"].rstrip().endswith("none")
    assert "1/2" in rows["0.5"] and "rdm-003 (kubernetes.io)" in rows["0.5"]
    assert "Warning" not in out


def test_a_sweep_outside_the_dev_split_warns(dataset, tmp_path, capsys):
    _, out, _ = run(
        ["--ranking-only", "--sweep", "trust_weight=0.3"],
        FakeFactory(),
        tmp_path,
        capsys,
    )
    assert "tune on the dev split only" in out


def test_a_sweep_keeps_other_overrides_and_asks_for_the_defaults_last(
    dataset, tmp_path, capsys
):
    factory = FakeFactory()
    run(
        [
            "--ranking-only",
            "--split",
            "dev",
            "--base-weight",
            "0.7",
            "--sweep",
            "trust.freecodecamp=0.8,0.6",
        ],
        factory,
        tmp_path,
        capsys,
    )
    assert factory.calls == [
        ({"base_weight": 0.7}, True),
        ({"base_weight": 0.7, "trust.freecodecamp": 0.8}, True),
        ({"base_weight": 0.7, "trust.freecodecamp": 0.6}, True),
        ({}, True),
    ]


def test_a_sweep_never_calls_the_model(dataset, tmp_path, capsys):
    seen = []

    def behave(overrides, ranking_only, case):
        seen.append(ranking_only)
        return good_attempt(case, ranking_only=ranking_only)

    run(
        ["--ranking-only", "--sweep", "trust_weight=0.3,0.4"],
        FakeFactory(behave),
        tmp_path,
        capsys,
    )
    assert seen and all(seen)


def test_a_sweep_with_a_failed_run_exits_two(dataset, tmp_path, capsys):
    def behave(overrides, ranking_only, case):
        return Attempt(error="retrieval failed")

    code, out, _ = run(
        ["--ranking-only", "--split", "dev", "--sweep", "trust_weight=0.3"],
        FakeFactory(behave),
        tmp_path,
        capsys,
    )
    assert code == 2
    assert "trust_weight=0.3 rdm-001: retrieval failed" in out


# ── main: saving and comparing ───────────────────────────────


def test_save_writes_the_run_with_its_filters(dataset, tmp_path, capsys):
    code, out, _ = run(["--save", "--repeat", "2"], FakeFactory(), tmp_path, capsys)
    assert code == 0
    saved = tmp_path / "results" / "roadmap_v1_20261123T090000Z.json"
    assert f"Saved: {saved}" in out
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["filters"] == {
        "split": None,
        "tags": [],
        "repeat": 2,
        "ranking_only": False,
    }
    assert data["settings"]["model"] == "test-model"
    assert [c["id"] for c in data["cases"]] == ["rdm-001", "rdm-002", "rdm-003"]


def test_a_run_without_save_writes_nothing(dataset, tmp_path, capsys):
    run([], FakeFactory(), tmp_path, capsys)
    assert not (tmp_path / "results").exists()


def test_compare_names_what_got_worse(dataset, tmp_path, capsys):
    run(["--save"], FakeFactory(), tmp_path, capsys)
    saved = next((tmp_path / "results").glob("*.json"))

    def worse(overrides, ranking_only, case):
        return good_attempt(case, top=ROADMAP if case.id == "rdm-001" else None)

    code, out, _ = run(["--compare", saved.name], FakeFactory(worse), tmp_path, capsys)
    assert code == 0
    assert "Compared with the run of 2026-11-23T09:00:00+00:00:" in out
    assert "Started failing: rdm-001" in out


def test_compare_with_a_missing_file_exits_one(dataset, tmp_path, capsys):
    code, _, err = run(["--compare", "nope.json"], FakeFactory(), tmp_path, capsys)
    assert code == 1
    assert "no such file: nope.json" in err


def test_compare_with_a_run_of_another_area_exits_one(dataset, tmp_path, capsys):
    other = tmp_path / "retrieval.json"
    other.write_text(json.dumps({"area": "retrieval", "report": {}, "cases": []}))
    code, _, err = run(["--compare", str(other)], FakeFactory(), tmp_path, capsys)
    assert code == 1
    assert "is a retrieval run, not roadmap" in err
