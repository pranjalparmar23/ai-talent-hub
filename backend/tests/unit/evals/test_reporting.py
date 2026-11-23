"""Tests for evals.reporting: saving, finding, loading and comparing runs."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from evals.metrics import Delta
from evals.reporting import (
    RESULTS_DIR,
    ReportError,
    changed_cases,
    compare_text,
    format_changes,
    load_record,
    resolve_record_path,
    save_record,
    setting_changes,
    stamp,
)

NOW = datetime(2026, 11, 23, 9, 0, 0, tzinfo=timezone.utc)


def record(**changes):
    base = {
        "area": "retrieval",
        "dataset": "retrieval.v1.jsonl",
        "version": 1,
        "run_at": "2026-11-23T09:00:00+00:00",
        "filters": {"split": None, "tags": []},
        "settings": {"min_similarity": 0.3},
        "report": {"all": {"cases": 2, "precision": 0.5, "hit_rate": 1.0}},
        "cases": [{"id": "ret-001", "ok": True}, {"id": "ret-002", "ok": False}],
    }
    base.update(changes)
    return base


# ── where results go ─────────────────────────────────────────


def test_results_go_to_the_repository_data_folder():
    assert RESULTS_DIR.parts[-3:] == ("data", "eval", "results")


def test_stamp_is_utc_whatever_the_zone_of_the_time_given():
    india = timezone(timedelta(hours=5, minutes=30))
    assert stamp(NOW) == "20261123T090000Z"
    assert stamp(NOW.astimezone(india)) == "20261123T090000Z"


# ── saving ───────────────────────────────────────────────────


def test_save_writes_a_named_json_file_and_creates_the_folder(tmp_path):
    folder = tmp_path / "deep" / "results"
    path = save_record(record(), now=NOW, results_dir=folder)
    assert path == folder / "retrieval_v1_20261123T090000Z.json"
    assert json.loads(path.read_text(encoding="utf-8")) == record()
    assert path.read_text(encoding="utf-8").endswith("}\n")


def test_save_keeps_non_ascii_text_readable(tmp_path):
    path = save_record(record(note="café"), now=NOW, results_dir=tmp_path)
    assert "café" in path.read_text(encoding="utf-8")


def test_two_areas_do_not_share_a_file_name(tmp_path):
    a = save_record(record(), now=NOW, results_dir=tmp_path)
    b = save_record(record(area="roadmap"), now=NOW, results_dir=tmp_path)
    assert a != b


def test_save_to_an_impossible_place_is_a_report_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(ReportError, match="cannot write"):
        save_record(record(), now=NOW, results_dir=blocker / "inside")


# ── finding a file ───────────────────────────────────────────


def test_a_path_that_exists_is_used_as_given(tmp_path):
    file = tmp_path / "run.json"
    file.write_text("{}")
    assert resolve_record_path(str(file)) == file


def test_a_bare_name_is_looked_for_in_each_folder_in_order(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    (second / "base.json").write_text("{}")
    assert resolve_record_path("base.json", first, second) == second / "base.json"
    (first / "base.json").write_text("{}")
    assert resolve_record_path("base.json", first, second) == first / "base.json"


def test_a_file_that_is_nowhere_is_a_report_error(tmp_path):
    with pytest.raises(ReportError, match="no such file: nope.json"):
        resolve_record_path("nope.json", tmp_path)


def test_a_directory_is_not_taken_for_a_file(tmp_path):
    (tmp_path / "name.json").mkdir()
    with pytest.raises(ReportError):
        resolve_record_path("name.json", tmp_path)


# ── loading ──────────────────────────────────────────────────


def test_load_round_trips_a_saved_run(tmp_path):
    path = save_record(record(), now=NOW, results_dir=tmp_path)
    assert load_record(path) == record()


def test_load_rejects_bad_json(tmp_path):
    file = tmp_path / "bad.json"
    file.write_text("{oops")
    with pytest.raises(ReportError, match="not valid JSON"):
        load_record(file)


@pytest.mark.parametrize("content", ["[]", '"text"', "{}", '{"area": "x"}'])
def test_load_rejects_files_that_are_not_runs(tmp_path, content):
    file = tmp_path / "other.json"
    file.write_text(content)
    with pytest.raises(ReportError, match="not a saved golden-set run"):
        load_record(file)


def test_load_of_a_missing_file(tmp_path):
    with pytest.raises(ReportError, match="cannot read"):
        load_record(tmp_path / "missing.json")


# ── what changed ─────────────────────────────────────────────


def test_changed_cases_names_only_the_ones_that_flipped():
    before = [
        {"id": "a", "ok": True},
        {"id": "b", "ok": False},
        {"id": "c", "ok": True},
        {"id": "d", "ok": False},
        {"id": "gone", "ok": True},
    ]
    after = [
        {"id": "a", "ok": False},
        {"id": "b", "ok": True},
        {"id": "c", "ok": True},
        {"id": "d", "ok": False},
        {"id": "new", "ok": False},  # not in the earlier run: not a change
    ]
    assert changed_cases(before, after) == (["a"], ["b"])


def test_changed_cases_are_sorted():
    before = [{"id": i, "ok": True} for i in ("c", "a", "b")]
    after = [{"id": i, "ok": False} for i in ("c", "a", "b")]
    assert changed_cases(before, after)[0] == ["a", "b", "c"]


def test_changed_cases_of_empty_runs():
    assert changed_cases([], []) == ([], [])


# ── tables of change ─────────────────────────────────────────

CHANGES = [
    Delta("all", "precision", 0.5, 0.75),
    Delta("all", "mrr", 0.9, 0.9),
    Delta("split=dev", "precision", 0.6, 0.55),
]


def test_format_changes_shows_only_the_metrics_that_moved():
    text = format_changes(CHANGES)
    assert "+0.25" in text and "-0.05" in text
    assert "mrr" not in text


def test_a_move_under_half_a_point_does_not_count():
    assert format_changes([Delta("all", "mrr", 0.500, 0.504)]) == "No metric moved."


def test_format_changes_can_show_everything():
    assert "mrr" in format_changes(CHANGES, only_moved=False)


def test_nothing_moved():
    assert format_changes([]) == "No metric moved."


# ── settings ─────────────────────────────────────────────────


def test_setting_changes_lists_what_differs_and_what_was_added_or_dropped():
    before = {"settings": {"min_similarity": 0.3, "top_k": 5, "old": 1}}
    after = {"settings": {"min_similarity": 0.4, "top_k": 5, "new": 2}}
    assert setting_changes(before, after) == [
        "min_similarity 0.3 -> 0.4",
        "new None -> 2",
        "old 1 -> None",
    ]


def test_setting_changes_with_no_settings_saved():
    assert setting_changes({}, {"settings": None}) == []


def test_identical_settings_are_no_change():
    same = {"settings": {"a": {"x": 1}}}
    assert setting_changes(same, same) == []


# ── the comparison text ──────────────────────────────────────


def test_compare_text_for_two_like_runs():
    before = record()
    after = record(
        report={"all": {"cases": 2, "precision": 0.75, "hit_rate": 1.0}},
        cases=[{"id": "ret-001", "ok": False}, {"id": "ret-002", "ok": True}],
        run_at="2026-11-24T09:00:00+00:00",
    )
    text = compare_text(before, after)
    assert "Compared with the run of 2026-11-23T09:00:00+00:00:" in text
    assert "+0.25" in text
    assert "Started failing: ret-001" in text
    assert "Started passing: ret-002" in text
    assert "Settings that differ" not in text
    assert "Note:" not in text


def test_compare_text_names_a_different_dataset_file():
    text = compare_text(record(), record(dataset="retrieval.v2.jsonl"))
    assert "used retrieval.v1.jsonl, this one retrieval.v2.jsonl" in text


def test_compare_text_warns_when_the_filters_differ():
    text = compare_text(record(), record(filters={"split": "dev", "tags": []}))
    assert "different filters" in text
    assert "split= and tag= rows" in text


def test_compare_text_names_the_settings_that_moved():
    text = compare_text(record(), record(settings={"min_similarity": 0.4}))
    assert "Settings that differ: min_similarity 0.3 -> 0.4" in text


def test_compare_text_when_nothing_changed():
    text = compare_text(record(), record())
    assert "No metric moved." in text
    assert "Started" not in text


def test_compare_text_without_a_run_date():
    before = record()
    del before["run_at"]
    assert "run of unknown date" in compare_text(before, record())
