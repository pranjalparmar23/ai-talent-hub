"""Tests for evals.metrics: scoring, reports, comparisons and table printing.

Pure functions, so no fakes are needed beyond hand-built chunks and plans.
"""

import pytest

from evals.metrics import (
    Check,
    Delta,
    Hit,
    RoadmapScore,
    deltas,
    fmt,
    host_of,
    is_relevant,
    plan_links,
    render_table,
    report_retrieval,
    report_roadmap,
    score_retrieval,
    score_roadmap,
    summarise_retrieval,
    summarise_roadmap,
)
from tests.unit.evals._builders import (
    chunk,
    make_plan,
    retrieval_case,
    roadmap_case,
)

K8S = "https://kubernetes.io/docs/a"
ROADMAP = "https://roadmap.sh/kubernetes"


# ── Hit and relevance ────────────────────────────────────────


def test_hit_from_chunk_reads_text_and_metadata():
    hit = Hit.from_chunk(
        chunk("Title", "body", source_id="s", domain="d", url="https://x.io")
    )
    assert hit == Hit("Title", "body", "s", "d", "https://x.io")


def test_hit_from_chunk_survives_missing_metadata():
    bare = chunk()
    bare.metadata = None
    bare.text = None
    assert Hit.from_chunk(bare) == Hit()


def test_hit_from_chunk_turns_values_into_text():
    odd = chunk()
    odd.metadata = {"title": 7, "source_id": None}
    assert Hit.from_chunk(odd).title == "7"


def expected_of(**expected):
    return retrieval_case(expected=expected).expected


@pytest.mark.parametrize(
    ("hit", "expected", "relevant"),
    [
        (
            Hit(title="Dynamic Programming 101"),
            {"relevant_keywords": ["dynamic"]},
            True,
        ),
        (
            Hit(text="we cover Binary Trees"),
            {"relevant_keywords": ["binary tree"]},
            True,
        ),
        (Hit(title="Sorting"), {"relevant_keywords": ["graph"]}, False),
        (
            Hit(source_id="Official-Docs"),
            {"relevant_source_ids": ["official-docs"]},
            True,
        ),
        (Hit(source_id="other"), {"relevant_source_ids": ["official-docs"]}, False),
        (Hit(domain="Backend"), {"relevant_domains": ["backend"]}, True),
        (Hit(domain="design"), {"relevant_domains": ["backend"]}, False),
        # any one kind of match is enough
        (
            Hit(title="x", domain="backend"),
            {"relevant_keywords": ["zzz"], "relevant_domains": ["backend"]},
            True,
        ),
    ],
)
def test_is_relevant(hit, expected, relevant):
    assert is_relevant(hit, expected_of(**expected)) is relevant


def test_a_blank_hit_matches_nothing():
    assert not is_relevant(Hit(), expected_of(relevant_keywords=["sql"]))


# ── retrieval scores ─────────────────────────────────────────

GOOD = Hit(title="dynamic programming guide")
BAD = Hit(title="unrelated")


def test_precision_hit_and_rank_are_measured_over_what_came_back():
    score = score_retrieval(retrieval_case(), [BAD, GOOD, GOOD, BAD])
    assert (score.returned, score.relevant) == (4, 2)
    assert score.precision == 0.5
    assert score.hit is True
    assert score.reciprocal_rank == 0.5
    assert score.ok is True


def test_rank_is_the_first_relevant_one():
    score = score_retrieval(retrieval_case(), [BAD, BAD, GOOD])
    assert score.reciprocal_rank == pytest.approx(1 / 3)


def test_nothing_relevant_scores_zero():
    score = score_retrieval(retrieval_case(), [BAD, BAD])
    assert (score.precision, score.reciprocal_rank) == (0.0, 0.0)
    assert score.hit is False and score.ok is False


def test_returning_nothing_when_results_are_expected_scores_zero():
    score = score_retrieval(retrieval_case(), [])
    assert score.returned == 0
    assert (score.precision, score.hit, score.reciprocal_rank) == (0.0, False, 0.0)
    assert score.ok is False


def test_only_the_first_top_k_hits_count():
    case = retrieval_case(top_k=2)
    score = score_retrieval(case, [BAD, BAD, GOOD])
    assert score.returned == 2
    assert score.hit is False


def test_an_expect_empty_case_passes_when_nothing_comes_back():
    case = retrieval_case(
        expected={"expect_empty": True}, tags=("technical", "no-match")
    )
    passing = score_retrieval(case, [])
    assert passing.ok is True
    assert (passing.precision, passing.hit, passing.reciprocal_rank) == (
        None,
        None,
        None,
    )
    failing = score_retrieval(case, [GOOD])
    assert failing.ok is False
    assert failing.returned == 1


def test_a_score_keeps_the_case_tags_and_split():
    case = retrieval_case(tags=("non-technical", "borderline"), split="test")
    score = score_retrieval(case, [GOOD])
    assert score.tags == ("non-technical", "borderline")
    assert score.split == "test"
    assert score.case_id == "ret-001"


# ── retrieval summaries ──────────────────────────────────────


def scores_for(*cases_and_hits):
    return [score_retrieval(c, h) for c, h in cases_and_hits]


def test_summary_means_are_over_cases_that_expect_results():
    empty = retrieval_case("ret-003", expected={"expect_empty": True})
    scores = scores_for(
        (retrieval_case("ret-001"), [GOOD, GOOD]),  # precision 1, rr 1
        (retrieval_case("ret-002"), [BAD, GOOD]),  # precision .5, rr .5
        (empty, []),
    )
    summary = summarise_retrieval(scores)
    assert summary.cases == 3 and summary.scored == 2
    assert summary.precision == 0.75
    assert summary.hit_rate == 1.0
    assert summary.mrr == 0.75
    assert (summary.empty_cases, summary.empty_correct) == (1, 1)


def test_a_missed_case_pulls_the_hit_rate_down():
    scores = scores_for(
        (retrieval_case("ret-001"), [GOOD]), (retrieval_case("ret-002"), [BAD])
    )
    assert summarise_retrieval(scores).hit_rate == 0.5


def test_summary_with_only_empty_cases_has_no_means():
    scores = scores_for((retrieval_case(expected={"expect_empty": True}), [GOOD]))
    summary = summarise_retrieval(scores)
    assert (summary.precision, summary.hit_rate, summary.mrr) == (None, None, None)
    assert (summary.empty_cases, summary.empty_correct) == (1, 0)


def test_summary_of_nothing():
    summary = summarise_retrieval([])
    assert summary.cases == 0 and summary.precision is None


def test_summary_as_dict_has_every_field():
    keys = summarise_retrieval([]).as_dict().keys()
    assert {"cases", "precision", "hit_rate", "mrr", "empty_correct"} <= set(keys)


def test_retrieval_report_has_all_split_and_tag_groups():
    scores = scores_for(
        (retrieval_case("ret-001", tags=("technical", "clear")), [GOOD]),
        (
            retrieval_case("ret-002", tags=("non-technical", "clear"), split="test"),
            [BAD],
        ),
    )
    report = report_retrieval(scores)
    assert list(report) == [
        "all",
        "split=dev",
        "split=test",
        "tag=clear",
        "tag=non-technical",
        "tag=technical",
    ]
    assert report["all"].hit_rate == 0.5
    assert (
        report["tag=non-technical"].hit_rate == 0.0
    )  # the slice an average would hide
    assert report["tag=technical"].hit_rate == 1.0
    assert report["split=test"].cases == 1


# ── plan helpers ─────────────────────────────────────────────


def test_host_of():
    assert host_of("https://Kubernetes.IO/docs") == "kubernetes.io"
    assert host_of("not a url") == ""
    assert host_of("") == ""


def test_plan_links_keeps_order_and_repeats_and_skips_blanks():
    plan = {
        "weeks": [
            {"resources": [{"url": " https://a.io "}, {"url": ""}, {"title": "x"}]},
            {"resources": [{"url": "https://a.io"}]},
            {"resources": None},
            {},
        ]
    }
    assert plan_links(plan) == ["https://a.io", "https://a.io"]


def test_plan_links_of_an_empty_plan():
    assert plan_links({}) == []
    assert plan_links({"weeks": None}) == []


# ── roadmap checks ───────────────────────────────────────────


def check(plan, *, retrieved=(K8S, ROADMAP), top=K8S, **expected):
    case = roadmap_case(expected=expected or None)
    return score_roadmap(case, plan, retrieved_urls=retrieved, top_ranked_url=top)


def by_name(score):
    return {c.name: c for c in score.checks}


def test_a_clean_grounded_plan_passes_every_check():
    score = check(make_plan([K8S, ROADMAP]), expect_grounded=True, min_links=1)
    assert score.ok and not score.failed
    assert [c.name for c in score.checks] == [
        "model_answered",
        "no_invented_links",
        "sources_match_links",
        "sources_and_notice_exclusive",
        "grounded",
        "link_count",
    ]


def test_only_the_checks_a_case_asks_for_are_run():
    score = check(make_plan(), min_links=0)
    assert [c.name for c in score.checks][-1] == "link_count"
    assert "grounded" not in by_name(score)
    assert "top_ranked_host" not in by_name(score)


@pytest.mark.parametrize(
    "extra", [{"_parse_error": True}, {"_error_reason": "rate limit"}]
)
def test_the_fallback_plan_fails_model_answered(extra):
    score = check(make_plan(**extra), min_links=1)
    assert not by_name(score)["model_answered"].passed


def test_an_invented_link_is_caught():
    plan = make_plan([K8S, "https://evil.example/x"])
    score = check(plan, min_links=1)
    found = by_name(score)["no_invented_links"]
    assert not found.passed
    assert "evil.example" in found.detail


def test_a_repeated_link_counts_once():
    plan = make_plan([K8S, K8S])
    assert by_name(check(plan, min_links=2))["link_count"].passed is False
    assert by_name(check(plan, min_links=1, max_links=1))["link_count"].passed is True


def test_a_source_without_a_link_is_caught():
    plan = make_plan([K8S], sources=[K8S, ROADMAP])
    assert not by_name(check(plan, min_links=1))["sources_match_links"].passed


def test_sources_and_notice_together_fail():
    plan = make_plan([K8S], notice="not reviewed")
    assert not by_name(check(plan, min_links=1))["sources_and_notice_exclusive"].passed


def test_a_notice_alone_is_fine_for_the_exclusive_check():
    plan = make_plan([], sources=[], notice="not reviewed")
    assert by_name(check(plan, expect_grounded=False))[
        "sources_and_notice_exclusive"
    ].passed


@pytest.mark.parametrize(
    ("plan", "passes"),
    [
        (make_plan([K8S]), True),
        (make_plan([K8S], sources=[]), False),  # links but no sources
        (make_plan([K8S], notice="x"), False),  # a notice
        (make_plan([], sources=[]), False),  # no links
    ],
)
def test_grounded(plan, passes):
    score = check(plan, expect_grounded=True, min_links=0)
    assert by_name(score)["grounded"].passed is passes


@pytest.mark.parametrize(
    ("plan", "passes"),
    [
        (make_plan([], sources=[], notice="not reviewed"), True),
        (make_plan([K8S], notice="not reviewed", sources=[]), False),  # has a link
        (make_plan([], sources=[]), False),  # no notice
    ],
)
def test_not_grounded(plan, passes):
    score = check(plan, expect_grounded=False)
    assert by_name(score)["not_grounded"].passed is passes


def test_empty_plan_needs_no_weeks_and_zero_total():
    ok = {"weeks": [], "total_weeks": 0, "sources": []}
    assert by_name(check(ok, empty_plan=True))["empty_plan"].passed
    assert not by_name(check(make_plan(), empty_plan=True))["empty_plan"].passed
    assert not by_name(check({"weeks": []}, empty_plan=True))["empty_plan"].passed


def test_link_count_limits():
    plan = make_plan([K8S, ROADMAP])
    assert by_name(check(plan, min_links=1, max_links=2))["link_count"].passed
    assert not by_name(check(plan, min_links=3))["link_count"].passed
    too_many = by_name(check(plan, min_links=0, max_links=1))["link_count"]
    assert not too_many.passed
    assert "2 links" in too_many.detail


def test_link_hosts_match_a_word_in_the_host():
    plan = make_plan([K8S])
    assert by_name(check(plan, min_links=1, link_hosts_any=["kubernetes"]))[
        "link_hosts"
    ].passed
    missed = by_name(check(plan, min_links=1, link_hosts_any=["docker"]))["link_hosts"]
    assert not missed.passed
    assert "kubernetes.io" in missed.detail


def test_top_ranked_host():
    plan = make_plan([K8S])
    wanted = {"min_links": 1, "top_ranked_hosts_any": ["kubernetes.io"]}
    assert by_name(check(plan, top=K8S, **wanted))["top_ranked_host"].passed
    wrong = by_name(check(plan, top=ROADMAP, **wanted))["top_ranked_host"]
    assert not wrong.passed
    assert "roadmap.sh" in wrong.detail


def test_top_ranked_host_with_nothing_retrieved_fails_and_says_so():
    wanted = {"min_links": 1, "top_ranked_hosts_any": ["kubernetes.io"]}
    result = by_name(check(make_plan([]), retrieved=(), top=None, **wanted))
    assert not result["top_ranked_host"].passed
    assert "nothing" in result["top_ranked_host"].detail


def test_must_not_contain_finds_a_word_anywhere_in_any_letter_case():
    plan = make_plan([K8S], summary="Done. pwned by the resume")
    result = by_name(check(plan, min_links=1, must_not_contain=["PWNED"]))
    assert not result["must_not_contain"].passed
    assert "PWNED" in result["must_not_contain"].detail


def test_must_not_contain_passes_on_a_clean_plan():
    plan = make_plan([K8S])
    assert by_name(check(plan, min_links=1, must_not_contain=["PWNED"]))[
        "must_not_contain"
    ].passed


def test_a_passed_check_has_no_detail():
    score = check(make_plan([K8S]), min_links=1)
    assert all(c.detail == "" for c in score.checks if c.passed)


def test_score_keeps_the_case_details():
    case = roadmap_case(split="test", tags=("non-technical", "ranking"))
    score = score_roadmap(case, make_plan(), retrieved_urls=[K8S], top_ranked_url=K8S)
    assert (score.case_id, score.split) == ("rdm-001", "test")
    assert score.tags == ("non-technical", "ranking")


def test_roadmap_score_failed_and_ok():
    score = RoadmapScore(
        "rdm-001", (), "dev", (Check("a", True), Check("b", False, "why"))
    )
    assert [c.name for c in score.failed] == ["b"]
    assert score.ok is False
    assert RoadmapScore("rdm-001", (), "dev").ok is True


# ── roadmap summaries ────────────────────────────────────────


def roadmap_scores():
    return [
        RoadmapScore("rdm-001", ("technical",), "dev", (Check("a", True),)),
        RoadmapScore(
            "rdm-002",
            ("non-technical",),
            "test",
            (Check("b", False, "x"), Check("a", False, "x")),
        ),
        RoadmapScore("rdm-003", ("technical",), "dev", (Check("b", False, "x"),)),
    ]


def test_roadmap_summary_counts_and_names_the_failed_checks():
    summary = summarise_roadmap(roadmap_scores())
    assert (summary.cases, summary.passed) == (3, 1)
    assert summary.pass_rate == pytest.approx(1 / 3)
    assert summary.failed_checks == {"a": 1, "b": 2}  # sorted by name


def test_roadmap_summary_of_nothing():
    summary = summarise_roadmap([])
    assert summary.pass_rate is None and summary.failed_checks == {}


def test_roadmap_report_groups():
    report = report_roadmap(roadmap_scores())
    assert set(report) == {
        "all",
        "split=dev",
        "split=test",
        "tag=technical",
        "tag=non-technical",
    }
    assert report["tag=technical"].pass_rate == 0.5
    assert report["split=test"].pass_rate == 0.0
    assert report["all"].as_dict()["failed_checks"] == {"a": 1, "b": 2}


# ── comparing runs ───────────────────────────────────────────


def test_deltas_compare_only_rates_and_means_present_in_both():
    before = {
        "all": {
            "cases": 5,
            "precision": 0.5,
            "hit_rate": 0.8,
            "mrr": None,
            "failed_checks": {"x": 1},
            "flag": True,
        },
        "split=dev": {"precision": 0.1},
    }
    after = {
        "all": {
            "cases": 9,
            "precision": 0.6,
            "hit_rate": 0.7,
            "mrr": 0.5,
            "failed_checks": {},
            "flag": False,
        },
        "tag=new": {"precision": 0.9},
    }
    found = deltas(before, after)
    assert [(d.group, d.metric) for d in found] == [
        ("all", "hit_rate"),
        ("all", "precision"),
    ]
    assert found[1].change == pytest.approx(0.1)
    assert found[0].change == pytest.approx(-0.1)


def test_deltas_skip_counts_even_when_both_have_them():
    both = {"all": {"cases": 5, "scored": 5, "passed": 3, "empty_cases": 1}}
    assert deltas(both, both) == []


def test_delta_change():
    assert Delta("all", "mrr", 0.5, 0.75).change == 0.25


# ── printing ─────────────────────────────────────────────────


def test_render_table_aligns_columns_and_shows_none_as_a_dash():
    text = render_table(["a", "long header"], [[1, None], [22, "x"]])
    lines = text.splitlines()
    assert [line.split() for line in lines] == [
        ["a", "long", "header"],
        ["--", "-----------"],
        ["1", "-"],
        ["22", "x"],
    ]
    # the second column starts in the same place on every line
    assert lines[2].index("-") == lines[3].index("x") == lines[0].index("long")


def test_render_table_with_no_rows_prints_the_headers():
    text = render_table(["one", "two"], [])
    assert text.splitlines()[0].split() == ["one", "two"]
    assert len(text.splitlines()) == 2


def test_fmt():
    assert fmt(0.5) == "0.50"
    assert fmt(0.666) == "0.67"
    assert fmt(0) == "0.00"
    assert fmt(None) is None
