"""Tests for the expiry sweep (app/ingest/expiry.py).

A fake store plays the part of ChromaDB: it understands the two filter shapes the
sweep sends (one condition, or "$and" of conditions) and records every call. The
filter shapes themselves are asserted too, because the real ChromaDB is only
checked by the integration tests (task 14).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.ingest import expiry
from app.ingest.expiry import (
    ExpiredDoc,
    ExpiryReport,
    find_expired,
    remove_expired,
)
from app.ingest.schema import INTERVIEW_COLLECTION, LEARNING_COLLECTION

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc)
NOW_TS = int(NOW.timestamp())
PAST = NOW_TS - 86_400
FUTURE = NOW_TS + 86_400


class FakeStore:
    """Chunks in two dicts. Records the filters it was asked for and every delete."""

    def __init__(self):
        self.collections = {INTERVIEW_COLLECTION: {}, LEARNING_COLLECTION: {}}
        self.filters: list[tuple[str, dict]] = []
        self.deletes: list[tuple[str, list[str]]] = []

    def add(self, collection, chunk_id, **meta):
        self.collections[collection][chunk_id] = meta

    def get_by_where(self, collection_name, where):
        self.filters.append((collection_name, where))
        rows = self.collections[collection_name]
        return [
            {"id": i, "metadata": m} for i, m in rows.items() if self._match(m, where)
        ]

    def _match(self, meta, where):
        if "$and" in where:
            return all(self._match(meta, part) for part in where["$and"])
        ((key, value),) = where.items()
        if isinstance(value, dict):
            ((op, limit),) = value.items()
            assert op == "$lte", f"unexpected operator {op}"
            return key in meta and meta[key] <= limit
        return meta.get(key) == value

    def delete_ids(self, collection_name, ids):
        self.deletes.append((collection_name, list(ids)))
        for i in ids:
            del self.collections[collection_name][i]
        return len(ids)


def chunk(doc_id, index, source="src-a", expires=PAST, url=None, **extra):
    return (
        f"{doc_id}:{index:04d}",
        {
            "doc_id": doc_id,
            "source_id": source,
            "url": url or f"https://example.com/{doc_id}",
            "expires_at_ts": expires,
            **extra,
        },
    )


def put(store, collection, *chunks):
    for chunk_id, meta in chunks:
        store.add(collection, chunk_id, **meta)


# ── what counts as expired ───────────────────────────────────


def test_an_expired_chunk_is_found():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0))
    (doc,) = find_expired(now=NOW, store=store)
    assert doc.doc_id == "aaaa"
    assert doc.chunk_ids == ["aaaa:0000"]
    assert doc.collection == LEARNING_COLLECTION
    assert doc.source_id == "src-a"
    assert doc.url == "https://example.com/aaaa"


def test_a_chunk_that_has_not_expired_is_left_alone():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, expires=FUTURE))
    assert find_expired(now=NOW, store=store) == []


def test_a_chunk_expiring_this_very_second_counts_as_expired():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, expires=NOW_TS))
    assert len(find_expired(now=NOW, store=store)) == 1


def test_a_chunk_expiring_one_second_later_is_not_expired():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, expires=NOW_TS + 1))
    assert find_expired(now=NOW, store=store) == []


def test_a_chunk_with_no_expiry_date_is_left_alone():
    store = FakeStore()
    store.add(LEARNING_COLLECTION, "old:0000", doc_id="old", source_id="legacy")
    assert find_expired(now=NOW, store=store) == []


def test_now_must_have_a_time_zone():
    naive = datetime(2026, 11, 19, 9, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        find_expired(now=naive, store=FakeStore())
    with pytest.raises(ValueError, match="timezone-aware"):
        remove_expired(now=naive, store=FakeStore())


def test_a_time_zone_other_than_utc_gives_the_same_answer():
    ist = timezone(timedelta(hours=5, minutes=30))
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, expires=NOW_TS))
    assert len(find_expired(now=NOW.astimezone(ist), store=store)) == 1


# ── grouping ─────────────────────────────────────────────────


def test_chunks_of_one_document_are_grouped():
    store = FakeStore()
    put(
        store, LEARNING_COLLECTION, chunk("aaaa", 0), chunk("aaaa", 1), chunk("aaaa", 2)
    )
    (doc,) = find_expired(now=NOW, store=store)
    assert sorted(doc.chunk_ids) == ["aaaa:0000", "aaaa:0001", "aaaa:0002"]


def test_documents_are_listed_in_a_steady_order():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("bbbb", 0, url="https://example.com/z"))
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, url="https://example.com/a"))
    put(store, INTERVIEW_COLLECTION, chunk("cccc", 0, url="https://example.com/m"))
    docs = find_expired(now=NOW, store=store)
    assert [d.collection for d in docs] == [
        INTERVIEW_COLLECTION,
        LEARNING_COLLECTION,
        LEARNING_COLLECTION,
    ]
    assert [d.url for d in docs][1:] == [
        "https://example.com/a",
        "https://example.com/z",
    ]


def test_both_knowledge_collections_are_searched():
    store = FakeStore()
    put(store, INTERVIEW_COLLECTION, chunk("aaaa", 0))
    put(store, LEARNING_COLLECTION, chunk("bbbb", 0))
    docs = find_expired(now=NOW, store=store)
    assert {d.collection for d in docs} == {INTERVIEW_COLLECTION, LEARNING_COLLECTION}
    assert {c for c, _ in store.filters} == {INTERVIEW_COLLECTION, LEARNING_COLLECTION}


def test_the_same_doc_id_in_two_collections_is_two_documents():
    store = FakeStore()
    put(store, INTERVIEW_COLLECTION, chunk("aaaa", 0))
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0))
    assert len(find_expired(now=NOW, store=store)) == 2


def test_the_doc_id_comes_from_the_chunk_id_when_metadata_lacks_it():
    store = FakeStore()
    store.add(LEARNING_COLLECTION, "abcd1234:0000", expires_at_ts=PAST)
    (doc,) = find_expired(now=NOW, store=store)
    assert doc.doc_id == "abcd1234"
    assert doc.url == ""
    assert doc.source_id == ""


def test_a_chunk_returned_twice_is_counted_once():
    class TwiceStore(FakeStore):
        def get_by_where(self, collection_name, where):
            rows = super().get_by_where(collection_name, where)
            return rows + rows

    store = TwiceStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0))
    (doc,) = find_expired(now=NOW, store=store)
    assert doc.chunk_ids == ["aaaa:0000"]


# ── limiting to sources ──────────────────────────────────────


def test_only_the_named_source_is_found():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, source="src-a"))
    put(store, LEARNING_COLLECTION, chunk("bbbb", 0, source="src-b"))
    docs = find_expired(now=NOW, source_ids=["src-a"], store=store)
    assert [d.doc_id for d in docs] == ["aaaa"]


def test_several_sources_can_be_named():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, source="src-a"))
    put(store, LEARNING_COLLECTION, chunk("bbbb", 0, source="src-b"))
    put(store, LEARNING_COLLECTION, chunk("cccc", 0, source="src-c"))
    docs = find_expired(now=NOW, source_ids=["src-a", "src-b"], store=store)
    assert sorted(d.doc_id for d in docs) == ["aaaa", "bbbb"]


def test_a_source_name_that_matches_nothing_finds_nothing():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, source="src-a"))
    assert find_expired(now=NOW, source_ids=["nope"], store=store) == []


def test_an_empty_source_list_finds_nothing_and_asks_for_nothing():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0))
    assert find_expired(now=NOW, source_ids=[], store=store) == []
    assert store.filters == []


# ── the filters sent to the store ────────────────────────────


def test_without_a_source_the_filter_is_one_condition():
    store = FakeStore()
    find_expired(now=NOW, store=store)
    assert store.filters == [
        (INTERVIEW_COLLECTION, {"expires_at_ts": {"$lte": NOW_TS}}),
        (LEARNING_COLLECTION, {"expires_at_ts": {"$lte": NOW_TS}}),
    ]


def test_with_a_source_the_filter_uses_and():
    store = FakeStore()
    find_expired(now=NOW, source_ids=["src-a"], store=store)
    expected = {"$and": [{"source_id": "src-a"}, {"expires_at_ts": {"$lte": NOW_TS}}]}
    assert store.filters == [
        (INTERVIEW_COLLECTION, expected),
        (LEARNING_COLLECTION, expected),
    ]


# ── removing ─────────────────────────────────────────────────


def test_expired_chunks_are_deleted_and_fresh_ones_stay():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0), chunk("aaaa", 1))
    put(store, LEARNING_COLLECTION, chunk("bbbb", 0, expires=FUTURE))
    report = remove_expired(now=NOW, store=store)
    assert report.chunks_deleted == 2
    assert report.documents == 1
    assert list(store.collections[LEARNING_COLLECTION]) == ["bbbb:0000"]


def test_deletes_go_to_the_collection_the_chunks_live_in():
    store = FakeStore()
    put(store, INTERVIEW_COLLECTION, chunk("aaaa", 0))
    put(store, LEARNING_COLLECTION, chunk("bbbb", 0))
    remove_expired(now=NOW, store=store)
    assert sorted(store.deletes) == [
        (INTERVIEW_COLLECTION, ["aaaa:0000"]),
        (LEARNING_COLLECTION, ["bbbb:0000"]),
    ]


def test_removing_with_a_source_leaves_other_sources_alone():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, source="src-a"))
    put(store, LEARNING_COLLECTION, chunk("bbbb", 0, source="src-b"))
    report = remove_expired(now=NOW, source_ids=["src-a"], store=store)
    assert report.chunks_deleted == 1
    assert list(store.collections[LEARNING_COLLECTION]) == ["bbbb:0000"]


def test_nothing_expired_means_no_delete_call():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0, expires=FUTURE))
    report = remove_expired(now=NOW, store=store)
    assert report.docs == []
    assert report.chunks_deleted == 0
    assert store.deletes == []


def test_large_documents_are_deleted_in_batches(monkeypatch):
    monkeypatch.setattr(expiry, "DELETE_BATCH_SIZE", 2)
    store = FakeStore()
    put(store, LEARNING_COLLECTION, *[chunk("aaaa", i) for i in range(5)])
    report = remove_expired(now=NOW, store=store)
    assert [len(ids) for _, ids in store.deletes] == [2, 2, 1]
    assert report.chunks_deleted == 5
    assert store.collections[LEARNING_COLLECTION] == {}


# ── dry run ──────────────────────────────────────────────────


def test_a_dry_run_finds_but_deletes_nothing():
    store = FakeStore()
    put(store, LEARNING_COLLECTION, chunk("aaaa", 0), chunk("aaaa", 1))
    report = remove_expired(now=NOW, dry_run=True, store=store)
    assert report.dry_run is True
    assert report.documents == 1
    assert report.chunks_found == 2
    assert report.chunks_deleted == 0
    assert store.deletes == []
    assert len(store.collections[LEARNING_COLLECTION]) == 2


def test_a_real_run_is_not_marked_as_a_dry_run():
    assert remove_expired(now=NOW, store=FakeStore()).dry_run is False


# ── the report objects ───────────────────────────────────────


def test_report_counts_come_from_its_documents():
    report = ExpiryReport(
        docs=[
            ExpiredDoc("c", "d1", "s", "u1", chunk_ids=["d1:0", "d1:1"]),
            ExpiredDoc("c", "d2", "s", "u2", chunk_ids=["d2:0"]),
        ]
    )
    assert report.documents == 2
    assert report.chunks_found == 3


def test_an_empty_report_has_zero_counts():
    report = ExpiryReport()
    assert (report.documents, report.chunks_found, report.chunks_deleted) == (0, 0, 0)
