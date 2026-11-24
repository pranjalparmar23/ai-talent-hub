"""Tests for scripts/remove_legacy_collections.py.

ChromaDB is replaced by a fake that keeps collections in a dict and records every
delete, so these run with no database. The snapshots are built in a temporary folder in
the same format that scripts/export_chroma.py writes.
"""

import json
from pathlib import Path

import pytest

from scripts import remove_legacy_collections as script
from scripts.remove_legacy_collections import (
    KNOWLEDGE_COLLECTIONS,
    LEGACY_COLLECTIONS,
    find_snapshot,
    main,
    run,
    snapshot_problems,
)

COUNTS = {
    "behavioral_questions": 14,
    "company_interviews": 23,
    "dsa_notes": 18,
    "interview_experiences": 36,
    "learning_resources": 28,
}
NEW = {"interview_knowledge": 829, "learning_knowledge": 127}


class FakeStore:
    def __init__(self, collections, *, down=False, fail_on=None):
        self.collections = dict(collections)
        self.down = down
        self.fail_on = fail_on
        self.deleted: list[str] = []

    def heartbeat(self):
        if self.down:
            raise ConnectionError("connection refused")
        return 1

    def list_collections(self):
        return sorted(self.collections)

    def count(self, name):
        return self.collections[name]

    def delete_collection(self, name):
        if name == self.fail_on:
            raise RuntimeError("boom")
        self.deleted.append(name)
        del self.collections[name]


def make_snapshot(root, name="pre-rag-rebuild-20261110T064200Z", counts=None, **tweaks):
    """A snapshot folder in the format export_chroma.py writes."""
    counts = COUNTS if counts is None else counts
    folder = Path(root) / name
    folder.mkdir(parents=True)
    entries = []
    for coll, n in counts.items():
        rows = tweaks.get("rows", {}).get(coll, n)
        if coll not in tweaks.get("skip_files", ()):
            lines = [json.dumps({"id": f"{coll}:{i}"}) for i in range(rows)]
            (folder / f"{coll}.jsonl").write_text("\n".join(lines) + "\n")
        entries.append(
            {
                "name": coll,
                "count_in_chroma": n,
                "rows_exported": tweaks.get("exported", {}).get(coll, n),
            }
        )
    manifest = {"created_at": "2026-11-10T06:42:00+00:00", "collections": entries}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return folder


def everything():
    return FakeStore({**COUNTS, **NEW})


def go(store, root, *, delete=False, snapshot=None):
    lines: list[str] = []
    code = run(
        store, snapshot_root=root, snapshot=snapshot, delete=delete, out=lines.append
    )
    return code, "\n".join(lines)


# ── the lists ────────────────────────────────────────────────


def test_the_old_names_are_the_five_original_collections():
    assert set(LEGACY_COLLECTIONS) == set(COUNTS)


def test_no_new_collection_is_ever_in_the_delete_list():
    assert not set(LEGACY_COLLECTIONS) & set(KNOWLEDGE_COLLECTIONS)
    assert set(KNOWLEDGE_COLLECTIONS) == set(NEW)


# ── the plan (nothing is deleted without --yes) ──────────────


def test_without_yes_it_shows_the_plan_and_deletes_nothing(tmp_path):
    make_snapshot(tmp_path)
    store = everything()
    code, text = go(store, tmp_path)
    assert code == 0
    assert store.deleted == []
    assert "Nothing was deleted" in text
    for name in COUNTS:
        assert name in text


# ── deleting ─────────────────────────────────────────────────


def test_with_yes_it_deletes_the_five_old_collections_and_only_those(tmp_path):
    make_snapshot(tmp_path)
    store = everything()
    code, text = go(store, tmp_path, delete=True)
    assert code == 0
    assert sorted(store.deleted) == sorted(LEGACY_COLLECTIONS)
    assert store.list_collections() == sorted(NEW)
    assert store.collections == NEW  # the new data is untouched
    assert "5 old collection(s) removed" in text


def test_a_collection_that_is_not_in_the_list_is_never_deleted(tmp_path):
    make_snapshot(tmp_path)
    store = FakeStore({**COUNTS, **NEW, "someone_elses": 7})
    go(store, tmp_path, delete=True)
    assert "someone_elses" not in store.deleted
    assert "someone_elses" in store.collections


def test_only_the_old_collections_that_still_exist_are_checked_and_deleted(tmp_path):
    left = {k: v for k, v in COUNTS.items() if k in ("dsa_notes", "learning_resources")}
    make_snapshot(tmp_path)  # the snapshot still has all five
    store = FakeStore({**left, **NEW})
    code, _ = go(store, tmp_path, delete=True)
    assert code == 0
    assert sorted(store.deleted) == ["dsa_notes", "learning_resources"]


def test_running_again_after_the_delete_finds_nothing_to_do(tmp_path):
    store = FakeStore(NEW)  # no snapshot folder at all, and that is fine
    code, text = go(store, tmp_path, delete=True)
    assert code == 0
    assert store.deleted == []
    assert "Nothing to do" in text


def test_a_failed_delete_stops_and_says_what_is_left(tmp_path):
    make_snapshot(tmp_path)
    store = FakeStore({**COUNTS, **NEW}, fail_on="dsa_notes")
    code, text = go(store, tmp_path, delete=True)
    assert code == 1
    assert "Could not delete dsa_notes" in text
    assert "dsa_notes" in store.collections
    assert store.collections.keys() >= set(NEW)  # the new data is still there
    assert "Still there:" in text


# ── refusals ─────────────────────────────────────────────────


def test_it_refuses_when_chromadb_cannot_be_reached(tmp_path):
    code, text = go(FakeStore({}, down=True), tmp_path, delete=True)
    assert code == 1
    assert "Cannot reach ChromaDB" in text


@pytest.mark.parametrize("missing", KNOWLEDGE_COLLECTIONS)
def test_it_refuses_when_a_new_collection_is_missing(tmp_path, missing):
    make_snapshot(tmp_path)
    store = FakeStore({**COUNTS, **{k: v for k, v in NEW.items() if k != missing}})
    code, text = go(store, tmp_path, delete=True)
    assert code == 1
    assert f"{missing} does not exist" in text
    assert store.deleted == []


@pytest.mark.parametrize("name", KNOWLEDGE_COLLECTIONS)
def test_it_refuses_when_a_new_collection_is_empty(tmp_path, name):
    make_snapshot(tmp_path)
    store = FakeStore({**COUNTS, **NEW, name: 0})
    code, text = go(store, tmp_path, delete=True)
    assert code == 1
    assert f"{name} is empty" in text
    assert store.deleted == []


def test_it_refuses_when_there_is_no_snapshot(tmp_path):
    store = everything()
    code, text = go(store, tmp_path, delete=True)
    assert code == 1
    assert "export_chroma.py" in text
    assert store.deleted == []


def test_it_refuses_when_the_snapshot_folder_does_not_exist(tmp_path):
    store = everything()
    code, _ = go(store, tmp_path / "missing", delete=True)
    assert code == 1
    assert store.deleted == []


def test_it_refuses_when_a_collection_is_not_in_the_snapshot(tmp_path):
    counts = {k: v for k, v in COUNTS.items() if k != "dsa_notes"}
    make_snapshot(tmp_path, counts=counts)
    store = everything()
    code, text = go(store, tmp_path, delete=True)
    assert code == 1
    assert "dsa_notes: not in the snapshot" in text
    assert store.deleted == []


def test_it_refuses_when_the_collection_changed_after_the_snapshot(tmp_path):
    make_snapshot(tmp_path)
    store = FakeStore({**COUNTS, "dsa_notes": 19, **NEW})
    code, text = go(store, tmp_path, delete=True)
    assert code == 1
    assert "dsa_notes: ChromaDB holds 19 chunks now, the snapshot has 18" in text
    assert store.deleted == []


def test_it_refuses_when_the_snapshot_was_not_exported_in_full(tmp_path):
    make_snapshot(tmp_path, exported={"dsa_notes": 10})
    store = everything()
    code, text = go(store, tmp_path, delete=True)
    assert code == 1
    assert "not exported in full" in text
    assert store.deleted == []


def test_it_refuses_when_a_data_file_is_missing(tmp_path):
    make_snapshot(tmp_path, skip_files=("dsa_notes",))
    store = everything()
    code, text = go(store, tmp_path, delete=True)
    assert code == 1
    assert "dsa_notes.jsonl is missing" in text
    assert store.deleted == []


def test_it_refuses_when_a_data_file_is_short(tmp_path):
    make_snapshot(tmp_path, rows={"dsa_notes": 5})
    store = everything()
    code, text = go(store, tmp_path, delete=True)
    assert code == 1
    assert "dsa_notes.jsonl does not have 18 rows" in text
    assert store.deleted == []


def test_every_problem_is_listed_not_only_the_first(tmp_path):
    make_snapshot(tmp_path, rows={"dsa_notes": 5}, skip_files=("learning_resources",))
    store = FakeStore({**COUNTS, "interview_knowledge": 0, "learning_knowledge": 127})
    code, text = go(store, tmp_path, delete=True)
    assert code == 1
    assert "interview_knowledge is empty" in text
    assert "dsa_notes.jsonl does not have" in text
    assert "learning_resources.jsonl is missing" in text


def test_a_broken_manifest_is_a_refusal_not_a_crash(tmp_path):
    folder = make_snapshot(tmp_path)
    (folder / "manifest.json").write_text("{not json")
    store = everything()
    code, text = go(store, tmp_path, delete=True)
    assert code == 1
    assert "cannot read" in text
    assert store.deleted == []


# ── finding the snapshot ─────────────────────────────────────


def test_the_newest_snapshot_is_used(tmp_path):
    make_snapshot(tmp_path, name="pre-rag-rebuild-20261101T000000Z")
    newest = make_snapshot(tmp_path, name="pre-rag-rebuild-20261120T000000Z")
    assert find_snapshot(tmp_path) == newest


def test_a_folder_without_a_manifest_or_with_another_name_is_not_a_snapshot(tmp_path):
    (tmp_path / "pre-rag-rebuild-20261201T000000Z").mkdir()  # half-written, no manifest
    (tmp_path / "something-else").mkdir()
    good = make_snapshot(tmp_path, name="pre-rag-rebuild-20261110T000000Z")
    assert find_snapshot(tmp_path) == good


def test_no_snapshot_folder_gives_none(tmp_path):
    assert find_snapshot(tmp_path) is None
    assert find_snapshot(tmp_path / "missing") is None


def test_an_explicit_snapshot_overrides_the_search(tmp_path):
    make_snapshot(tmp_path, name="pre-rag-rebuild-20261120T000000Z")
    chosen = tmp_path / "my-copy"
    assert find_snapshot(tmp_path, chosen) == chosen


def test_a_snapshot_that_also_holds_the_new_collections_is_fine(tmp_path):
    make_snapshot(tmp_path, counts={**COUNTS, **NEW})
    store = everything()
    code, _ = go(store, tmp_path, delete=True)
    assert code == 0


def test_snapshot_problems_is_empty_for_a_good_snapshot(tmp_path):
    folder = make_snapshot(tmp_path)
    assert snapshot_problems(folder, COUNTS) == []


def test_snapshot_problems_without_a_manifest(tmp_path):
    assert "has no manifest.json" in snapshot_problems(tmp_path, COUNTS)[0]


# ── the command line ─────────────────────────────────────────


def test_main_passes_the_options_through(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(script, "SNAPSHOT_ROOT", tmp_path / "unused")
    folder = make_snapshot(tmp_path, name="my-copy")
    store = everything()
    assert main(["--snapshot", str(folder)], store=store) == 0
    assert store.deleted == []  # no --yes
    assert main(["--snapshot", str(folder), "--yes"], store=store) == 0
    assert len(store.deleted) == 5
    assert "removed" in capsys.readouterr().out


def test_main_rejects_an_unknown_option(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--force"], store=everything())
    assert exc.value.code == 2
