"""Tests for the link-only adapter (app/ingest/sources/link_only.py).

Most tests write a small YAML list into a temporary folder. The last group checks the
real roadmap.sh list that is shipped with the code.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from app.ingest.licence import check_licence
from app.ingest.schema import LEARNING_CONTENT_TYPES, LINK_ONLY_TEXT_MAX_CHARS
from app.ingest.sources.base import DEFAULT_EXPIRY, SourceError
from app.ingest.sources.link_only import (
    ROADMAP_SH_FILE,
    LinkOnlyAdapter,
    load_link_list,
)

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)

TOP = {
    "source_id": "example-links",
    "licence": "proprietary",
    "attribution": "Example Site. Link only: no content is copied.",
    "content_type": "roadmap",
}


def entry(**over) -> dict:
    """A valid entry. Pass field=None to remove a field."""
    base = {
        "url": "https://example.com/backend",
        "title": "Backend roadmap",
        "domain": "software-engineering",
        "role_family": "technical",
        "level": "senior",
        "summary": "A learning path for backend work.",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def write(tmp_path: Path, data, name: str = "links.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def link_file(tmp_path: Path, *entries, **top) -> Path:
    return write(tmp_path, {**TOP, **top, "entries": list(entries)})


def adapter(path: Path, **kwargs) -> LinkOnlyAdapter:
    return LinkOnlyAdapter(path, clock=lambda: NOW, **kwargs)


# ── a valid list ─────────────────────────────────────────────


async def test_a_valid_entry_becomes_a_link_only_document(tmp_path):
    batch = await adapter(link_file(tmp_path, entry())).fetch()
    assert batch.source_id == "example-links"
    assert batch.skipped == []
    (doc,) = batch.docs
    assert doc.source_id == "example-links"
    assert doc.url == "https://example.com/backend"
    assert doc.title == "Backend roadmap"
    assert doc.domain == "software-engineering"
    assert doc.role_family == "technical"
    assert doc.level == "senior"
    assert doc.content_type == "roadmap"
    assert doc.ingest_mode == "link_only"
    assert doc.licence == "proprietary"
    assert doc.attribution == "Example Site. Link only: no content is copied."


async def test_the_stored_text_is_the_title_and_our_summary(tmp_path):
    (doc,) = (await adapter(link_file(tmp_path, entry())).fetch()).docs
    assert doc.text == "Backend roadmap. A learning path for backend work."


async def test_dates_come_from_the_clock_and_the_expiry(tmp_path):
    (doc,) = (await adapter(link_file(tmp_path, entry())).fetch()).docs
    assert doc.fetched_at == NOW
    assert doc.expires_at == NOW + DEFAULT_EXPIRY


async def test_expiry_can_be_changed(tmp_path):
    path = link_file(tmp_path, entry())
    (doc,) = (await adapter(path, expiry=timedelta(days=30)).fetch()).docs
    assert doc.expires_at == NOW + timedelta(days=30)


async def test_level_defaults_to_all(tmp_path):
    (doc,) = (await adapter(link_file(tmp_path, entry(level=None))).fetch()).docs
    assert doc.level == "all"


async def test_an_entry_can_set_its_own_content_type(tmp_path):
    path = link_file(tmp_path, entry(content_type="article"))
    (doc,) = (await adapter(path).fetch()).docs
    assert doc.content_type == "article"


async def test_spaces_around_title_and_summary_are_removed(tmp_path):
    path = link_file(tmp_path, entry(title="  Backend roadmap ", summary=" A path. "))
    (doc,) = (await adapter(path).fetch()).docs
    assert doc.title == "Backend roadmap"
    assert doc.text == "Backend roadmap. A path."


async def test_documents_keep_the_order_of_the_list(tmp_path):
    path = link_file(
        tmp_path, entry(), entry(url="https://example.com/frontend", title="Frontend")
    )
    batch = await adapter(path).fetch()
    assert [d.url for d in batch.docs] == [
        "https://example.com/backend",
        "https://example.com/frontend",
    ]


async def test_link_only_documents_pass_the_licence_gate(tmp_path):
    (doc,) = (await adapter(link_file(tmp_path, entry())).fetch()).docs
    assert check_licence(doc).allowed


# ── entries that are left out ────────────────────────────────

OTHER = entry(url="https://example.com/other", title="Other roadmap")


@pytest.mark.parametrize("field", ["url", "title", "domain", "role_family", "summary"])
async def test_an_entry_missing_a_required_field_is_skipped(tmp_path, field):
    path = link_file(tmp_path, entry(**{field: None}), OTHER)
    batch = await adapter(path).fetch()
    assert [d.url for d in batch.docs] == ["https://example.com/other"]
    assert len(batch.skipped) == 1
    assert f"missing {field}" in batch.skipped[0].reason


async def test_an_entry_that_is_not_a_mapping_is_skipped(tmp_path):
    batch = await adapter(link_file(tmp_path, "just a string", OTHER)).fetch()
    assert len(batch.docs) == 1
    assert batch.skipped[0].path == "entry 1"
    assert "not a mapping" in batch.skipped[0].reason


async def test_a_repeated_address_is_skipped(tmp_path):
    again = entry(title="Backend roadmap again")
    batch = await adapter(link_file(tmp_path, entry(), again)).fetch()
    assert [d.title for d in batch.docs] == ["Backend roadmap"]
    assert batch.skipped[0].path == "https://example.com/backend"
    assert "duplicate" in batch.skipped[0].reason


async def test_an_invalid_value_is_skipped_with_the_field_named(tmp_path):
    batch = await adapter(
        link_file(tmp_path, entry(role_family="wizard"), OTHER)
    ).fetch()
    assert len(batch.docs) == 1
    assert "not a valid document" in batch.skipped[0].reason
    assert "role_family" in batch.skipped[0].reason


async def test_a_bad_address_is_skipped(tmp_path):
    batch = await adapter(link_file(tmp_path, entry(url="not-a-url"), OTHER)).fetch()
    assert len(batch.docs) == 1
    assert "url" in batch.skipped[0].reason


async def test_a_summary_that_is_too_long_is_skipped(tmp_path):
    too_long = "word " * (LINK_ONLY_TEXT_MAX_CHARS // 5 + 10)
    batch = await adapter(link_file(tmp_path, entry(summary=too_long), OTHER)).fetch()
    assert len(batch.docs) == 1
    assert "not a valid document" in batch.skipped[0].reason


async def test_a_content_type_the_schema_does_not_know_is_skipped(tmp_path):
    batch = await adapter(
        link_file(tmp_path, entry(content_type="podcast"), OTHER)
    ).fetch()
    assert len(batch.docs) == 1
    assert "content_type" in batch.skipped[0].reason


async def test_a_list_with_no_valid_entry_is_an_error(tmp_path):
    path = link_file(tmp_path, entry(role_family="wizard"))
    with pytest.raises(SourceError, match="no valid entries"):
        await adapter(path).fetch()


async def test_a_list_with_no_entries_is_an_error(tmp_path):
    path = link_file(tmp_path)
    with pytest.raises(SourceError, match="entries"):
        adapter(path)


# ── the file itself ──────────────────────────────────────────


def test_a_missing_file_is_a_source_error(tmp_path):
    with pytest.raises(SourceError, match="cannot read"):
        load_link_list(tmp_path / "nope.yaml")


def test_broken_yaml_is_a_source_error(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("source_id: [unclosed\n", encoding="utf-8")
    with pytest.raises(SourceError, match="cannot read"):
        load_link_list(path)


def test_a_list_at_the_top_level_is_a_source_error(tmp_path):
    with pytest.raises(SourceError, match="mapping"):
        load_link_list(write(tmp_path, ["a", "b"]))


@pytest.mark.parametrize(
    "key", ["source_id", "licence", "attribution", "content_type", "entries"]
)
def test_each_top_level_key_is_required(tmp_path, key):
    data = {**TOP, "entries": [entry()]}
    del data[key]
    with pytest.raises(SourceError, match=f"missing .*{key}"):
        load_link_list(write(tmp_path, data))


def test_entries_must_be_a_list(tmp_path):
    data = {**TOP, "entries": "not-a-list"}
    with pytest.raises(SourceError, match="must be a list"):
        load_link_list(write(tmp_path, data))


def test_the_adapter_reports_the_source_id_from_the_file(tmp_path):
    assert adapter(link_file(tmp_path, entry())).source_id == "example-links"


# ── the real roadmap.sh list ─────────────────────────────────


@pytest.fixture(scope="module")
def roadmap_batch():
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(LinkOnlyAdapter(clock=lambda: NOW).fetch())
    finally:
        loop.close()


def test_the_roadmap_file_is_inside_the_backend_package():
    # The Docker image copies only backend/, so the data must live there.
    assert ROADMAP_SH_FILE.is_file()
    assert "app" in ROADMAP_SH_FILE.parts


def test_every_roadmap_entry_is_valid(roadmap_batch):
    assert roadmap_batch.source_id == "roadmap-sh"
    assert roadmap_batch.skipped == []
    assert len(roadmap_batch.docs) >= 30


def test_roadmap_documents_hold_only_a_link_and_our_own_summary(roadmap_batch):
    for doc in roadmap_batch.docs:
        assert doc.ingest_mode == "link_only"
        assert doc.licence == "proprietary"
        assert "no content is copied" in doc.attribution
        assert len(doc.text) <= LINK_ONLY_TEXT_MAX_CHARS
        assert doc.url.startswith("https://roadmap.sh/")


def test_roadmap_addresses_are_unique(roadmap_batch):
    urls = [d.url for d in roadmap_batch.docs]
    assert len(urls) == len(set(urls))


def test_roadmap_documents_go_to_the_learning_collection(roadmap_batch):
    assert {d.collection for d in roadmap_batch.docs} == {"learning_knowledge"}
    assert {d.content_type for d in roadmap_batch.docs} <= set(LEARNING_CONTENT_TYPES)


def test_roadmap_documents_pass_the_licence_gate(roadmap_batch):
    assert all(check_licence(d).allowed for d in roadmap_batch.docs)


def test_the_roadmap_list_covers_more_than_software(roadmap_batch):
    assert {"technical", "non_technical"} <= {d.role_family for d in roadmap_batch.docs}
