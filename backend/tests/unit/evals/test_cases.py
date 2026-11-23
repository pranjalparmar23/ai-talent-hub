"""Tests for evals.cases: the case models, the loader and the shipped datasets.

No network, no ChromaDB, no model. Small datasets are written to tmp_path.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from evals import cases as cases_module
from evals.cases import (
    DATASETS,
    DatasetError,
    RetrievalCase,
    RoadmapCase,
    dataset_path,
    load_dataset,
    main,
    read_cases,
    select,
    tag_counts,
    unreviewed,
)

# ── builders ─────────────────────────────────────────────────


def retrieval(case_id="ret-001", **changes):
    case = {
        "id": case_id,
        "area": "retrieval",
        "tags": ["technical", "clear"],
        "split": "dev",
        "input": {"query": "dynamic programming", "collection": "interview_knowledge"},
        "expected": {"relevant_keywords": ["dynamic programming"]},
        "notes": "A core topic that the guide covers.",
        "source": "hand-written",
        "added": "2026-11-23",
    }
    case.update(changes)
    return case


def roadmap(case_id="rdm-001", **changes):
    case = {
        "id": case_id,
        "area": "roadmap",
        "tags": ["technical", "clear"],
        "split": "dev",
        "input": {"skill_gaps": ["Docker"], "target_role": "Backend Engineer"},
        "expected": {"expect_grounded": True, "min_links": 1},
        "notes": "A normal plan for one technical skill.",
        "source": "hand-written",
        "added": "2026-11-23",
    }
    case.update(changes)
    return case


def write(path: Path, *rows) -> Path:
    path.write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    return path


def problems_of(path: Path) -> str:
    with pytest.raises(DatasetError) as caught:
        read_cases(path)
    return "\n".join(caught.value.problems)


# ── one valid case of each area ──────────────────────────────


def test_valid_cases_load_as_their_own_type(tmp_path):
    path = write(tmp_path / "a.jsonl", retrieval(), roadmap())
    loaded = read_cases(path)
    assert isinstance(loaded[0], RetrievalCase)
    assert isinstance(loaded[1], RoadmapCase)
    assert loaded[0].input.top_k == 5  # the default


def test_blank_lines_are_skipped(tmp_path):
    path = write(tmp_path / "a.jsonl", retrieval(), "", "   ", retrieval("ret-002"))
    assert [c.id for c in read_cases(path)] == ["ret-001", "ret-002"]


# ── the outer shape ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"id": "ret-1"}, "three letters"),
        ({"id": "rdm-001"}, "ret-001"),  # right shape, wrong area
        ({"split": "validation"}, "split"),
        ({"source": "guess"}, "source"),
        ({"notes": "too short"}, "20 characters"),
        ({"tags": ["clear"]}, "exactly one"),
        ({"tags": ["technical", "non-technical"]}, "exactly one"),
        ({"tags": ["technical", "claer"]}, "unknown tag"),
        ({"tags": ["technical", "clear", "clear"]}, "repeats"),
        ({"added": "yesterday"}, "added"),
        ({"surprise": 1}, "surprise"),  # a typo'd key must not be ignored
        ({"pair_with": "rdm-002"}, "same area"),
    ],
)
def test_bad_outer_fields_are_reported_with_the_line(tmp_path, changes, message):
    text = problems_of(write(tmp_path / "a.jsonl", retrieval(**changes)))
    assert "a.jsonl:1" in text
    assert message in text


def test_unknown_area_is_rejected(tmp_path):
    text = problems_of(write(tmp_path / "a.jsonl", retrieval(area="ats")))
    assert "a.jsonl:1" in text


# ── retrieval input and expected ─────────────────────────────


@pytest.mark.parametrize(
    "input_changes",
    [
        {"collection": "old_resources"},
        {"role_family": "nobody"},
        {"level": "principal"},
        {"top_k": 0},
        {"top_k": 21},
        {"unknown": 1},
    ],
)
def test_bad_retrieval_input(tmp_path, input_changes):
    case = retrieval()
    case["input"].update(input_changes)
    assert "a.jsonl:1" in problems_of(write(tmp_path / "a.jsonl", case))


def test_domain_is_lowercased_and_an_empty_query_is_allowed(tmp_path):
    case = retrieval(tags=["technical", "empty-input"])
    case["input"].update({"query": "", "domain": " Backend "})
    case["expected"] = {"expect_empty": True}
    (loaded,) = read_cases(write(tmp_path / "a.jsonl", case))
    assert loaded.input.domain == "backend"
    assert loaded.input.query == ""


@pytest.mark.parametrize(
    ("expected", "message"),
    [
        ({}, "name what a relevant result looks like"),
        ({"expect_empty": True, "relevant_keywords": ["sql"]}, "cannot also name"),
        ({"relevant_keywords": ["sql", "SQL"]}, "repeats"),
        ({"relevant_keywords": [" "]}, "empty value"),
    ],
)
def test_bad_retrieval_expected(tmp_path, expected, message):
    text = problems_of(write(tmp_path / "a.jsonl", retrieval(expected=expected)))
    assert message in text


def test_retrieval_keywords_are_lowercased(tmp_path):
    case = retrieval(expected={"relevant_keywords": [" SQL Joins "]})
    (loaded,) = read_cases(write(tmp_path / "a.jsonl", case))
    assert loaded.expected.relevant_keywords == ["sql joins"]


def test_source_ids_and_domains_alone_are_enough(tmp_path):
    rows = [
        retrieval("ret-001", expected={"relevant_source_ids": ["official-docs"]}),
        retrieval("ret-002", expected={"relevant_domains": ["backend"]}),
    ]
    assert len(read_cases(write(tmp_path / "a.jsonl", *rows))) == 2


# ── roadmap input and expected ───────────────────────────────


@pytest.mark.parametrize(
    ("expected", "message"),
    [
        ({"min_links": 3, "max_links": 2}, "more than max_links"),
        ({"min_links": 13}, "min_links"),
        ({"max_links": 13}, "max_links"),
        ({"link_hosts_any": ["kubernetes.io"]}, "min_links of at least 1"),
        ({"top_ranked_hosts_any": ["go.dev"]}, "min_links of at least 1"),
        ({"link_hosts_any": ["a.io", "A.io"], "min_links": 1}, "repeats"),
        ({"empty_plan": True, "min_links": 1}, "empty plan"),
        ({"empty_plan": True, "expect_grounded": False}, "empty plan"),
        ({"expect_grounded": False, "min_links": 1}, "not grounded"),
        ({"must_not_contain": ["PWNED", "PWNED"]}, "repeats"),
    ],
)
def test_bad_roadmap_expected(tmp_path, expected, message):
    text = problems_of(write(tmp_path / "a.jsonl", roadmap(expected=expected)))
    assert message in text


def test_roadmap_hosts_are_lowercased_and_canaries_keep_their_case(tmp_path):
    case = roadmap(
        expected={
            "min_links": 1,
            "top_ranked_hosts_any": ["Kubernetes.IO"],
            "must_not_contain": ["PWNED"],
        }
    )
    (loaded,) = read_cases(write(tmp_path / "a.jsonl", case))
    assert loaded.expected.top_ranked_hosts_any == ["kubernetes.io"]
    assert loaded.expected.must_not_contain == ["PWNED"]


def test_empty_plan_and_ungrounded_cases_are_valid(tmp_path):
    rows = [
        roadmap("rdm-001", expected={"empty_plan": True}),
        roadmap("rdm-002", expected={"expect_grounded": False}),
    ]
    assert len(read_cases(write(tmp_path / "a.jsonl", *rows))) == 2


def test_blank_skills_are_allowed(tmp_path):
    case = roadmap(tags=["technical", "messy"])
    case["input"]["skill_gaps"] = ["  ", ""]
    (loaded,) = read_cases(write(tmp_path / "a.jsonl", case))
    assert loaded.input.skill_gaps == ["  ", ""]


# ── the file as a whole ──────────────────────────────────────


def test_every_problem_is_reported_in_one_run(tmp_path):
    path = write(
        tmp_path / "a.jsonl",
        retrieval(split="x"),
        "{not json",
        retrieval("ret-002", notes="short"),
    )
    text = problems_of(path)
    assert "a.jsonl:1" in text
    assert "a.jsonl:2: not valid JSON" in text
    assert "a.jsonl:3" in text


def test_a_repeated_id_names_the_first_line(tmp_path):
    path = write(tmp_path / "a.jsonl", retrieval(), retrieval())
    assert "id ret-001 is already used on line 1" in problems_of(path)


def test_an_empty_file_is_a_problem(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_text("\n\n", encoding="utf-8")
    assert "no cases" in problems_of(path)


def test_a_missing_file_is_a_dataset_error(tmp_path):
    assert "cannot read" in problems_of(tmp_path / "missing.jsonl")


def test_dataset_error_keeps_the_list_of_problems():
    error = DatasetError(["one", "two"])
    assert error.problems == ["one", "two"]
    assert str(error) == "one\ntwo"


# ── fairness pairs ───────────────────────────────────────────

PAIR = ["technical", "clear", "fairness"]


def pair(a_changes=None, b_changes=None):
    a = {"tags": PAIR, "pair_with": "ret-002", **(a_changes or {})}
    b = {"tags": PAIR, "pair_with": "ret-001", **(b_changes or {})}
    return retrieval("ret-001", **a), retrieval("ret-002", **b)


def test_a_good_pair_loads(tmp_path):
    assert len(read_cases(write(tmp_path / "a.jsonl", *pair()))) == 2


def test_a_pair_must_be_in_the_file(tmp_path):
    a, _ = pair()
    assert "is not in this file" in problems_of(write(tmp_path / "a.jsonl", a))


def test_a_pair_must_name_each_other(tmp_path):
    a, b = pair()
    b["pair_with"] = "ret-003"
    c = retrieval("ret-003", tags=PAIR, pair_with="ret-002")
    text = problems_of(write(tmp_path / "a.jsonl", a, b, c))
    assert "does not name ret-001 back" in text


def test_a_pair_must_share_a_split(tmp_path):
    a, b = pair(b_changes={"split": "test"})
    assert "in the other split" in problems_of(write(tmp_path / "a.jsonl", a, b))


def test_a_paired_case_needs_the_fairness_tag(tmp_path):
    a, b = pair(a_changes={"tags": ["technical", "clear"]})
    assert "needs the 'fairness' tag" in problems_of(write(tmp_path / "a.jsonl", a, b))


# ── choosing and counting ────────────────────────────────────


@pytest.fixture
def mixed(tmp_path):
    rows = [
        retrieval("ret-001", tags=["technical", "clear", "interview"]),
        retrieval("ret-002", tags=["non-technical", "borderline"], split="test"),
        retrieval("ret-003", tags=["technical", "interview"], source="model-draft"),
    ]
    return read_cases(write(tmp_path / "a.jsonl", *rows))


def ids(selected):
    return [c.id for c in selected]


def test_select_by_split(mixed):
    assert ids(select(mixed, split="dev")) == ["ret-001", "ret-003"]
    assert ids(select(mixed, split="test")) == ["ret-002"]


def test_select_by_tags_needs_all_of_them(mixed):
    assert ids(select(mixed, tags=["technical", "interview"])) == ["ret-001", "ret-003"]
    assert ids(select(mixed, tags=["technical", "clear"])) == ["ret-001"]
    assert ids(select(mixed, split="test", tags=["technical"])) == []


def test_select_without_filters_keeps_everything(mixed):
    assert ids(select(mixed)) == ["ret-001", "ret-002", "ret-003"]


def test_select_rejects_an_unknown_split(mixed):
    with pytest.raises(ValueError, match="split"):
        select(mixed, split="validation")


def test_unreviewed_finds_model_drafts_only(mixed):
    assert ids(unreviewed(mixed)) == ["ret-003"]


def test_tag_counts(mixed):
    counts = tag_counts(mixed)
    assert counts["technical"] == 2
    assert counts["interview"] == 2
    assert counts["borderline"] == 1


# ── finding a dataset ────────────────────────────────────────


@pytest.fixture
def datasets_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cases_module, "DATASETS_DIR", tmp_path)
    (tmp_path / "knowledge").mkdir()
    return tmp_path / "knowledge"


def test_the_latest_version_is_the_default(datasets_dir):
    for n in (1, 2, 10):
        write(datasets_dir / f"retrieval.v{n}.jsonl", retrieval())
    assert dataset_path("retrieval").name == "retrieval.v10.jsonl"
    assert dataset_path("retrieval", 2).name == "retrieval.v2.jsonl"


def test_other_files_in_the_folder_are_ignored(datasets_dir):
    write(datasets_dir / "retrieval.v1.jsonl", retrieval())
    (datasets_dir / "retrieval.v1.baseline.json").write_text("{}")
    (datasets_dir / "retrieval.v2.jsonl.bak").write_text("")
    (datasets_dir / "other.v9.jsonl").write_text("")
    assert dataset_path("retrieval").name == "retrieval.v1.jsonl"


def test_a_missing_version_lists_the_ones_that_exist(datasets_dir):
    write(datasets_dir / "retrieval.v1.jsonl", retrieval())
    with pytest.raises(DatasetError, match="no v9 .have v1."):
        dataset_path("retrieval", 9)


def test_no_file_at_all(datasets_dir):
    with pytest.raises(DatasetError, match="no retrieval.v<N>.jsonl"):
        dataset_path("retrieval")


def test_an_unknown_area(datasets_dir):
    with pytest.raises(DatasetError, match="unknown area 'ats'"):
        dataset_path("ats")


def test_load_dataset_rejects_cases_of_another_area(datasets_dir):
    write(datasets_dir / "retrieval.v1.jsonl", roadmap())
    with pytest.raises(DatasetError, match="another area"):
        load_dataset("retrieval")


def test_load_dataset_reads_the_latest(datasets_dir):
    write(datasets_dir / "retrieval.v1.jsonl", retrieval())
    assert ids(load_dataset("retrieval")) == ["ret-001"]


# ── the command line ─────────────────────────────────────────


def test_main_passes_on_good_data(datasets_dir, capsys):
    write(datasets_dir / "retrieval.v1.jsonl", retrieval(), retrieval("ret-002"))
    assert main(["--area", "retrieval"]) == 0
    out = capsys.readouterr().out
    assert "retrieval.v1.jsonl, 2 cases" in out
    assert "dev 2, test 0" in out


def test_main_names_the_problems_and_exits_1(datasets_dir, capsys):
    write(datasets_dir / "retrieval.v1.jsonl", retrieval(split="x"))
    assert main(["--area", "retrieval"]) == 1
    assert "retrieval.v1.jsonl:1" in capsys.readouterr().err


def test_require_reviewed_fails_while_drafts_remain(datasets_dir, capsys):
    write(datasets_dir / "retrieval.v1.jsonl", retrieval(source="model-draft"))
    assert main(["--area", "retrieval"]) == 0
    assert main(["--area", "retrieval", "--require-reviewed"]) == 1
    assert "1 model-draft case(s)" in capsys.readouterr().out


def test_require_reviewed_passes_when_all_are_checked(datasets_dir):
    write(datasets_dir / "retrieval.v1.jsonl", retrieval(source="reviewed-draft"))
    assert main(["--area", "retrieval", "--require-reviewed"]) == 0


def test_main_with_no_area_checks_every_area(datasets_dir, capsys):
    write(datasets_dir / "retrieval.v1.jsonl", retrieval())
    # the roadmap folder does not exist in this fake tree, so that area is a problem
    assert main([]) == 1
    captured = capsys.readouterr()
    assert "retrieval.v1.jsonl, 1 cases" in captured.out
    assert "roadmap: PROBLEMS" in captured.err


# ── the shipped datasets ─────────────────────────────────────


@pytest.mark.parametrize("area", sorted(DATASETS))
def test_shipped_dataset_is_valid_and_balanced(area):
    loaded = load_dataset(area)
    assert 20 <= len(loaded) <= 60
    test_share = sum(1 for c in loaded if c.split == "test") / len(loaded)
    assert 0.2 <= test_share <= 0.35
    # both kinds of role are covered, so no slice can hide in the overall number
    counts = tag_counts(loaded)
    assert counts["technical"] > 0
    assert counts["non-technical"] > 0
    assert any(c.pair_with for c in loaded)  # at least one fairness pair


def test_shipped_datasets_do_not_edit_a_case_in_place():
    """A frozen version is never edited. These ids and splits must not change."""
    first = {c.id: c.split for c in load_dataset("retrieval", 1)}
    assert first["ret-001"] == "dev"
    assert first["ret-020"] == "test"
    second = {c.id: c.split for c in load_dataset("roadmap", 1)}
    assert second["rdm-001"] == "dev"
    assert second["rdm-013"] in {"dev", "test"}


def test_shipped_roadmap_has_a_safety_case_with_a_canary():
    safety = select(load_dataset("roadmap"), tags=["safety"])
    assert safety
    assert any(c.expected.must_not_contain for c in safety)


def test_models_are_not_shared_between_cases():
    """Mutating one loaded case must not change another (guards against shared lists)."""
    loaded = load_dataset("retrieval")
    before = deepcopy(loaded[1].model_dump())
    loaded[0].expected.relevant_keywords.append("changed")
    assert loaded[1].model_dump() == before
