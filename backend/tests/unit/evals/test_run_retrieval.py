"""Tests for evals.run_retrieval, with a fake search. No ChromaDB, no embedding model.

`build_search` (the real search) is not tested here: it needs ChromaDB and is covered by
running the runner itself. Everything around it is.
"""

import json
from datetime import datetime, timezone

import pytest

from evals import run_retrieval as rr
from evals.metrics import Hit, score_retrieval
from evals.run_retrieval import (
    Outcome,
    check_setting,
    describe,
    format_report,
    format_sweep,
    main,
    parse_sweep,
    run_cases,
    top_line,
)
from tests.unit.evals._builders import retrieval_case, retrieval_dict, write_dataset

NOW = datetime(2026, 11, 23, 9, 0, 0, tzinfo=timezone.utc)
GOOD = Hit(title="Dynamic programming guide", source_id="handbook")
BAD = Hit(title="Unrelated page", source_id="other")
SIZES = {"interview_knowledge": 100, "learning_knowledge": 50}
SETTINGS = {
    "top_k": 5,
    "min_similarity": 0.3,
    "mmr_lambda": 0.7,
    "max_per_doc": 2,
}


def found(*hits, route="knowledge", reason=""):
    return Outcome(
        hits=tuple(hits),
        similarities=tuple(0.5 for _ in hits),
        route=route,
        reason=reason,
    )


class FakeFactory:
    """A search factory. `answers` maps a case id to an Outcome (nothing by default)."""

    def __init__(self, answers=None, *, settings=None, fail=None):
        self.answers = answers or {}
        self.settings = settings or dict(SETTINGS)
        self.fail = fail
        self.calls: list[dict] = []

    def __call__(self, overrides):
        self.calls.append(dict(overrides))
        if self.fail:
            raise self.fail
        merged = {**self.settings, **overrides}

        def search(case):
            answer = self.answers.get(case.id, found())
            return answer(overrides) if callable(answer) else answer

        return search, merged, dict(SIZES)


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    rows = [
        retrieval_dict("ret-001", tags=("technical", "clear")),
        retrieval_dict(
            "ret-002",
            query="how to sell",
            tags=("non-technical", "no-match"),
            expected={"expect_empty": True},
            source="model-draft",
        ),
        retrieval_dict("ret-003", tags=("technical", "clear"), split="test"),
    ]
    return write_dataset(tmp_path, monkeypatch, "retrieval", rows)


def run(argv, factory, tmp_path, capsys):
    code = main(
        argv, search_factory=factory, now=lambda: NOW, results_dir=tmp_path / "results"
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ── settings that can be tried ───────────────────────────────


def test_check_setting_gives_the_right_type():
    assert check_setting("min_similarity", 0.4) == 0.4
    assert check_setting("max_per_doc", 3.0) == 3
    assert isinstance(check_setting("max_per_doc", 3.0), int)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("min_similarity", 1.5, "between"),
        ("min_similarity", -0.1, "between"),
        ("mmr_lambda", 2, "between"),
        ("max_per_doc", 0, "between"),
        ("max_per_doc", 21, "between"),
        ("max_per_doc", 2.5, "whole number"),
    ],
)
def test_check_setting_rejects_out_of_range_values(name, value, message):
    with pytest.raises(ValueError, match=message):
        check_setting(name, value)


def test_parse_sweep():
    assert parse_sweep("min_similarity=0.3, 0.4,0.45") == (
        "min_similarity",
        [0.3, 0.4, 0.45],
    )
    assert parse_sweep("max-per-doc=1,2,2") == (
        "max_per_doc",
        [1, 2],
    )  # dashes, repeats


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("min_similarity", "looks like NAME"),
        ("top_k=1,2", "looks like NAME"),
        ("min_similarity=a,b", "is not a number"),
        ("min_similarity=0.3,2", "between"),
        ("min_similarity=" + ",".join(str(i / 20) for i in range(9)), "1 to 8"),
    ],
)
def test_parse_sweep_rejects_mistakes(text, message):
    with pytest.raises(ValueError, match=message):
        parse_sweep(text)


# ── running cases ────────────────────────────────────────────


def test_run_cases_scores_each_case():
    cases = [retrieval_case("ret-001"), retrieval_case("ret-002")]
    answers = {"ret-001": found(GOOD), "ret-002": found(BAD)}
    result = run_cases(cases, lambda c: answers[c.id])
    assert [s.ok for s in result.scores] == [True, False]
    assert [c.id for c, _, _ in result.ran] == ["ret-001", "ret-002"]
    assert result.not_run == []


def test_a_failed_search_is_not_scored_and_says_why():
    """An error must never turn into a score: for an expect-empty case it would pass."""
    empty = retrieval_case("ret-002", expected={"expect_empty": True})
    result = run_cases([empty], lambda c: Outcome(error="ChromaDB is down"))
    assert result.ran == []
    assert result.scores == []
    assert result.not_run == [("ret-002", "ChromaDB is down")]


# ── printing ─────────────────────────────────────────────────


def test_top_line_shows_title_similarity_and_source():
    outcome = Outcome(hits=(GOOD, BAD), similarities=(0.52, 0.31))
    assert top_line(outcome) == (
        '"Dynamic programming guide" (0.52) [handbook], "Unrelated page" (0.31) [other]'
    )


def test_top_line_limits_and_shortens():
    long = Hit(title="x" * 100, source_id="s")
    outcome = Outcome(hits=(long,) * 5, similarities=(0.5,) * 5)
    line = top_line(outcome, limit=2)
    assert line.count("[s]") == 2
    assert "…" in line


def test_top_line_falls_back_to_the_text_when_there_is_no_title():
    outcome = Outcome(
        hits=(Hit(text="some  body\ntext", source_id="s"),), similarities=(0.4,)
    )
    assert '"some body text"' in top_line(outcome)


def test_top_line_for_nothing_names_the_route_and_reason():
    line = top_line(Outcome(route="general_knowledge", reason="no_match"))
    assert line == "nothing (route general_knowledge, reason no_match)"
    assert top_line(Outcome()) == "nothing (route -, reason -)"


def test_describe_a_case_that_missed():
    case = retrieval_case()
    outcome = found(BAD)
    text = describe(case, outcome, score_retrieval(case, outcome.hits))
    assert text.startswith('ret-001 [technical clear] "dynamic programming"')
    assert "no relevant result. Wanted keywords ['dynamic programming']" in text
    assert "got:" in text and "Unrelated page" in text


def test_describe_an_expect_empty_case_that_returned_something():
    case = retrieval_case("ret-002", expected={"expect_empty": True})
    outcome = found(GOOD, BAD)
    text = describe(case, outcome, score_retrieval(case, outcome.hits))
    assert "expected nothing, got 2" in text


def test_format_report_has_a_row_per_group():
    from evals.metrics import report_retrieval

    case = retrieval_case()
    text = format_report(report_retrieval([score_retrieval(case, [GOOD])]))
    assert text.splitlines()[0].split() == [
        "group",
        "cases",
        "precision",
        "hit",
        "rate",
        "MRR",
        "empty",
        "ok",
    ]
    assert "split=dev" in text and "tag=technical" in text


def test_format_sweep_marks_the_default_and_lists_failures():
    case = retrieval_case()
    good = run_cases([case], lambda c: found(GOOD))
    bad = run_cases([case], lambda c: found(BAD))
    text = format_sweep("min_similarity", [(0.3, good), (0.5, bad)], default=0.3)
    lines = text.splitlines()
    assert "0.3 (default)" in lines[2]
    assert lines[2].rstrip().endswith("none")
    assert lines[3].split()[0] == "0.5" and "(default)" not in lines[3]
    assert lines[3].rstrip().endswith("ret-001")


# ── the record that is saved ─────────────────────────────────


def test_build_record_shape():
    case = retrieval_case()
    result = run_cases([case], lambda c: found(GOOD))
    record = rr.build_record(
        result,
        dataset="retrieval.v1.jsonl",
        version=1,
        filters={"split": None, "tags": []},
        settings=dict(SETTINGS),
        sizes=dict(SIZES),
        now=NOW,
    )
    assert record["area"] == "retrieval" and record["version"] == 1
    assert record["run_at"] == "2026-11-23T09:00:00+00:00"
    assert record["knowledge_base"] == SIZES
    assert record["report"]["all"]["hit_rate"] == 1.0
    (entry,) = record["cases"]
    assert entry["id"] == "ret-001" and entry["ok"] is True
    assert entry["top"] == [
        {
            "title": "Dynamic programming guide",
            "source_id": "handbook",
            "similarity": 0.5,
        }
    ]
    json.dumps(record)  # everything in it can be saved


# ── main: a normal run ───────────────────────────────────────


def test_a_run_prints_the_report_and_exits_zero(dataset, tmp_path, capsys):
    factory = FakeFactory({"ret-001": found(GOOD), "ret-003": found(GOOD)})
    code, out, _ = run([], factory, tmp_path, capsys)
    assert code == 0
    assert "retrieval.v1.jsonl, 3 of 3 cases" in out
    assert "interview_knowledge 100 chunks, learning_knowledge 50 chunks" in out
    assert "cutoff 0.3" in out
    assert "1 case(s) are model-drafts" in out
    assert "Every case passed." in out
    assert "group" in out and "split=test" in out


def test_a_failing_case_is_a_finding_not_an_error(dataset, tmp_path, capsys):
    factory = FakeFactory({"ret-001": found(BAD), "ret-003": found(GOOD)})
    code, out, _ = run([], factory, tmp_path, capsys)
    assert code == 0
    assert "1 case(s) did not pass." in out
    assert "ret-001" in out and "no relevant result" in out


def test_an_expect_empty_case_that_returns_something_is_listed(
    dataset, tmp_path, capsys
):
    factory = FakeFactory(
        {"ret-001": found(GOOD), "ret-002": found(GOOD), "ret-003": found(GOOD)}
    )
    code, out, _ = run([], factory, tmp_path, capsys)
    assert code == 0
    assert "expected nothing, got 1" in out


def test_verbose_prints_a_line_per_case(dataset, tmp_path, capsys):
    factory = FakeFactory({"ret-001": found(GOOD), "ret-003": found(BAD)})
    _, out, _ = run(["--verbose"], factory, tmp_path, capsys)
    assert "ok   ret-001  returned 1, relevant 1" in out
    assert "FAIL ret-003  returned 1, relevant 0" in out
    assert "ok   ret-002" in out  # nothing returned, and nothing was expected


def test_split_and_tag_choose_the_cases(dataset, tmp_path, capsys):
    factory = FakeFactory()
    _, out, _ = run(["--split", "test"], factory, tmp_path, capsys)
    assert "1 of 3 cases" in out
    _, out, _ = run(["--tag", "technical", "--tag", "clear"], factory, tmp_path, capsys)
    assert "2 of 3 cases" in out


def test_no_matching_case_exits_one(dataset, tmp_path, capsys):
    code, _, err = run(
        ["--split", "test", "--tag", "no-match"], FakeFactory(), tmp_path, capsys
    )
    assert code == 1
    assert "No case matches" in err


# ── main: runs that could not be done ────────────────────────


def test_a_search_that_failed_exits_two_and_is_not_in_the_scores(
    dataset, tmp_path, capsys
):
    factory = FakeFactory(
        {
            "ret-001": found(GOOD),
            "ret-002": Outcome(error="vector store failed"),
            "ret-003": found(GOOD),
        }
    )
    code, out, _ = run([], factory, tmp_path, capsys)
    assert code == 2
    assert "1 case(s) could not run" in out
    assert "ret-002: vector store failed" in out
    assert "Every case that ran passed." in out


def test_every_search_failing_exits_two_without_a_report(dataset, tmp_path, capsys):
    factory = FakeFactory(
        {i: Outcome(error="down") for i in ("ret-001", "ret-002", "ret-003")}
    )
    code, out, _ = run([], factory, tmp_path, capsys)
    assert code == 2
    assert "precision" not in out


def test_a_factory_that_cannot_start_exits_two_with_a_hint(dataset, tmp_path, capsys):
    code, _, err = run(
        [], FakeFactory(fail=ConnectionError("refused")), tmp_path, capsys
    )
    assert code == 2
    assert "Cannot start the run: ConnectionError: refused" in err
    assert "Is ChromaDB running" in err


# ── main: bad options and bad data ───────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["--min-similarity", "1.5"],
        ["--max-per-doc", "0"],
        ["--sweep", "top_k=1,2"],
        ["--sweep", "min_similarity=0.3", "--save"],
        ["--sweep", "min_similarity=0.3", "--compare", "x.json"],
    ],
)
def test_bad_options_exit_one(argv, dataset, tmp_path, capsys):
    code, _, err = run(argv, FakeFactory(), tmp_path, capsys)
    assert code == 1
    assert err


def test_a_broken_dataset_exits_one_and_lists_the_problems(
    tmp_path, monkeypatch, capsys
):
    write_dataset(tmp_path, monkeypatch, "retrieval", [retrieval_dict(split="x")])
    code, _, err = run([], FakeFactory(), tmp_path, capsys)
    assert code == 1
    assert "The dataset has problems:" in err and "retrieval.v1.jsonl:1" in err


def test_the_search_is_not_started_when_options_are_wrong(dataset, tmp_path, capsys):
    factory = FakeFactory()
    run(["--min-similarity", "9"], factory, tmp_path, capsys)
    assert factory.calls == []


# ── main: overrides ──────────────────────────────────────────


def test_overrides_reach_the_search_and_are_printed(dataset, tmp_path, capsys):
    factory = FakeFactory()
    _, out, _ = run(
        ["--min-similarity", "0.4", "--mmr-lambda", "0.5", "--max-per-doc", "1"],
        factory,
        tmp_path,
        capsys,
    )
    assert factory.calls == [
        {"min_similarity": 0.4, "mmr_lambda": 0.5, "max_per_doc": 1}
    ]
    assert (
        "Overrides for this run: min_similarity=0.4, mmr_lambda=0.5, max_per_doc=1"
        in out
    )
    assert "cutoff 0.4" in out


# ── main: sweeps ─────────────────────────────────────────────


def cutoff_sensitive():
    """ret-001 is found only while the cutoff is 0.4 or lower."""
    return lambda overrides: (
        found(GOOD) if overrides.get("min_similarity", 0.3) <= 0.4 else found()
    )


def test_a_sweep_prints_one_row_per_value_and_marks_the_default(
    dataset, tmp_path, capsys
):
    factory = FakeFactory({"ret-001": cutoff_sensitive(), "ret-003": found(GOOD)})
    code, out, _ = run(
        ["--split", "dev", "--sweep", "min_similarity=0.3,0.4,0.5"],
        factory,
        tmp_path,
        capsys,
    )
    assert code == 0
    assert "Sweep of min_similarity over 2 cases:" in out
    assert "0.3 (default)" in out
    row_05 = next(line for line in out.splitlines() if line.startswith("0.5"))
    assert "ret-001" in row_05
    row_04 = next(line for line in out.splitlines() if line.startswith("0.4"))
    assert row_04.rstrip().endswith("none")
    assert "Warning" not in out  # dev split: no warning


def test_a_sweep_on_anything_but_dev_warns(dataset, tmp_path, capsys):
    _, out, _ = run(["--sweep", "min_similarity=0.3"], FakeFactory(), tmp_path, capsys)
    assert "tune on the dev split only" in out


def test_a_sweep_starts_the_search_once_per_value_and_once_for_the_default(
    dataset, tmp_path, capsys
):
    factory = FakeFactory()
    run(
        ["--split", "dev", "--sweep", "mmr_lambda=0.5,0.9", "--max-per-doc", "1"],
        factory,
        tmp_path,
        capsys,
    )
    assert factory.calls == [
        {"max_per_doc": 1},  # the first start, before the sweep
        {"max_per_doc": 1, "mmr_lambda": 0.5},
        {"max_per_doc": 1, "mmr_lambda": 0.9},
        {},  # the plain defaults, to know which row is the default
    ]


def test_a_sweep_with_a_failed_search_exits_two(dataset, tmp_path, capsys):
    factory = FakeFactory({"ret-001": Outcome(error="down")})
    code, out, _ = run(
        ["--split", "dev", "--sweep", "min_similarity=0.3"], factory, tmp_path, capsys
    )
    assert code == 2
    assert "min_similarity=0.3 ret-001: down" in out


# ── main: saving and comparing ───────────────────────────────


def test_save_writes_the_run_and_says_where(dataset, tmp_path, capsys):
    factory = FakeFactory({"ret-001": found(GOOD), "ret-003": found(GOOD)})
    code, out, _ = run(["--save"], factory, tmp_path, capsys)
    assert code == 0
    saved = tmp_path / "results" / "retrieval_v1_20261123T090000Z.json"
    assert f"Saved: {saved}" in out
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["area"] == "retrieval"
    assert data["dataset"] == "retrieval.v1.jsonl"
    assert data["settings"]["min_similarity"] == 0.3
    assert len(data["cases"]) == 3


def test_a_run_without_save_writes_nothing(dataset, tmp_path, capsys):
    run([], FakeFactory(), tmp_path, capsys)
    assert not (tmp_path / "results").exists()


def test_compare_with_a_saved_run_shows_what_moved(dataset, tmp_path, capsys):
    good = FakeFactory({"ret-001": found(GOOD), "ret-003": found(GOOD)})
    run(["--save"], good, tmp_path, capsys)
    saved = next((tmp_path / "results").glob("*.json"))
    worse = FakeFactory({"ret-001": found(BAD), "ret-003": found(GOOD)})
    code, out, _ = run(["--compare", str(saved)], worse, tmp_path, capsys)
    assert code == 0
    assert "Compared with the run of 2026-11-23T09:00:00+00:00:" in out
    assert "Started failing: ret-001" in out


def test_compare_finds_a_bare_name_in_the_results_folder(dataset, tmp_path, capsys):
    run(["--save"], FakeFactory(), tmp_path, capsys)
    saved = next((tmp_path / "results").glob("*.json"))
    code, out, _ = run(["--compare", saved.name], FakeFactory(), tmp_path, capsys)
    assert code == 0 and "Compared with" in out


def test_compare_with_a_missing_file_exits_one(dataset, tmp_path, capsys):
    code, _, err = run(["--compare", "nope.json"], FakeFactory(), tmp_path, capsys)
    assert code == 1
    assert "no such file: nope.json" in err


def test_compare_with_a_run_of_another_area_exits_one(dataset, tmp_path, capsys):
    other = tmp_path / "roadmap.json"
    other.write_text(json.dumps({"area": "roadmap", "report": {}, "cases": []}))
    code, _, err = run(["--compare", str(other)], FakeFactory(), tmp_path, capsys)
    assert code == 1
    assert "is a roadmap run, not retrieval" in err
