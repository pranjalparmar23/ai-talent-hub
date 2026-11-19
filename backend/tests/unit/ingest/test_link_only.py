"""Tests for the link-only adapter (app/ingest/sources/link_only.py).

Most tests write a small YAML list into a temporary folder. The last group checks the
real roadmap.sh list that is shipped with the code. Address tidying (normalise_url) and
the shared "already listed" set are tested in their own groups.
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
    normalise_url,
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


# ── tidying an address ───────────────────────────────────────


@pytest.mark.parametrize(
    ("written", "tidy"),
    [
        # already tidy: left alone
        ("https://roadmap.sh/backend", "https://roadmap.sh/backend"),
        # http becomes https, host is lower-case, trailing slash goes
        ("http://RoadMap.sh/backend/", "https://roadmap.sh/backend"),
        # fragments go
        ("https://roadmap.sh/backend#top", "https://roadmap.sh/backend"),
        # the root keeps one slash, with or without it written
        ("https://example.com", "https://example.com/"),
        ("https://example.com/", "https://example.com/"),
        # trailing dot on the host, spaces around the address
        ("https://example.com./a", "https://example.com/a"),
        ("  https://example.com/a  ", "https://example.com/a"),
        # default ports go, other ports stay
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "https://example.com/a"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        # http on another port is another service: keep its scheme
        ("http://example.com:8080/a", "http://example.com:8080/a"),
        # the path keeps its case, and www. is kept
        ("https://WWW.Example.com/Docs/Intro", "https://www.example.com/Docs/Intro"),
        # tracking parameters go, the others stay and are sorted
        (
            "https://example.com/a?utm_source=x&b=2&a=1&fbclid=z&gclid=q",
            "https://example.com/a?a=1&b=2",
        ),
        ("https://example.com/a?UTM_Medium=x&ref=home", "https://example.com/a"),
        # YouTube: the video and playlist stay, the share parameters go
        (
            "https://www.youtube.com/watch?v=abc&si=xyz&feature=share&list=PL1",
            "https://www.youtube.com/watch?list=PL1&v=abc",
        ),
        # IPv6 hosts keep their brackets
        ("https://[::1]:8080/a", "https://[::1]:8080/a"),
    ],
)
def test_normalise_url(written, tidy):
    assert normalise_url(written) == tidy


@pytest.mark.parametrize(
    "value",
    [
        "not-a-url",
        "ftp://example.com/a",
        "file:///etc/passwd",
        "https://",
        "https://example.com:99999/a",  # a port that does not exist
        "",
    ],
)
def test_something_that_is_not_an_http_address_is_returned_unchanged(value):
    assert normalise_url(value) == value


def test_credentials_are_kept_so_the_guards_can_reject_them():
    assert normalise_url("https://user:pw@example.com/a") == (
        "https://user:pw@example.com/a"
    )


def test_tidying_twice_gives_the_same_answer():
    messy = "http://WWW.Example.com:80/A/?b=2&utm_x=1&a=1#frag"
    once = normalise_url(messy)
    assert normalise_url(once) == once


def test_the_same_page_written_two_ways_has_one_address():
    a = "http://example.com/guide/?utm_source=mail&page=2"
    b = "https://EXAMPLE.com/guide?page=2#intro"
    assert normalise_url(a) == normalise_url(b)


# ── addresses in a list are tidied ───────────────────────────


async def test_the_stored_address_is_the_tidy_one(tmp_path):
    path = link_file(tmp_path, entry(url="http://Example.com/backend/?utm_source=x"))
    (doc,) = (await adapter(path).fetch()).docs
    assert doc.url == "https://example.com/backend"


async def test_the_same_page_written_two_ways_is_skipped_as_a_duplicate(tmp_path):
    again = entry(url="http://EXAMPLE.com/backend/#top?x", title="Backend again")
    batch = await adapter(link_file(tmp_path, entry(), again)).fetch()
    assert [d.title for d in batch.docs] == ["Backend roadmap"]
    assert batch.skipped[0].path == "http://EXAMPLE.com/backend/#top?x"
    assert batch.skipped[0].reason == "duplicate address"


async def test_an_address_that_is_not_text_is_skipped(tmp_path):
    batch = await adapter(link_file(tmp_path, entry(url=12345), OTHER)).fetch()
    assert [d.url for d in batch.docs] == ["https://example.com/other"]
    assert batch.skipped[0].reason == "url is not text"


# ── one run, several lists ───────────────────────────────────


async def test_a_page_listed_by_an_earlier_source_is_skipped(tmp_path):
    shared: set[str] = set()
    first = adapter(
        link_file(tmp_path, entry(), source_id="first-links"), seen_urls=shared
    )
    second_path = write(
        tmp_path,
        {
            **TOP,
            "source_id": "second-links",
            "entries": [
                entry(url="http://example.com/backend/", title="Backend (second)"),
                OTHER,
            ],
        },
        name="second.yaml",
    )
    second = adapter(second_path, seen_urls=shared)

    first_batch = await first.fetch()
    second_batch = await second.fetch()

    assert [d.url for d in first_batch.docs] == ["https://example.com/backend"]
    assert [d.url for d in second_batch.docs] == ["https://example.com/other"]
    assert len(second_batch.skipped) == 1
    assert "listed by another source" in second_batch.skipped[0].reason
    assert shared == {"https://example.com/backend", "https://example.com/other"}


async def test_without_a_shared_set_each_list_stands_alone(tmp_path):
    one = await adapter(link_file(tmp_path, entry())).fetch()
    two = await adapter(link_file(tmp_path, entry())).fetch()
    assert len(one.docs) == len(two.docs) == 1
    assert two.skipped == []


async def test_fetching_the_same_adapter_twice_gives_the_same_result(tmp_path):
    shared: set[str] = set()
    one = adapter(link_file(tmp_path, entry(), OTHER), seen_urls=shared)
    first = await one.fetch()
    second = await one.fetch()
    assert [d.url for d in second.docs] == [d.url for d in first.docs]
    assert second.skipped == []


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
    # A private loop, closed after use. Do NOT use asyncio.run() here: it resets the
    # "current" event loop to None, and tests/conftest.py shares one session-wide loop
    # between all async tests, so every async test after this fixture would fail.
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


def test_roadmap_addresses_are_already_tidy(roadmap_batch):
    # A document id is a hash of source_id and address. If tidying changed one of
    # these, its id would change and the next ingest would store it twice.
    raw = yaml.safe_load(ROADMAP_SH_FILE.read_text(encoding="utf-8"))["entries"]
    assert [normalise_url(e["url"]) for e in raw] == [e["url"] for e in raw]
    assert [d.url for d in roadmap_batch.docs] == [e["url"] for e in raw]


def test_roadmap_documents_go_to_the_learning_collection(roadmap_batch):
    assert {d.collection for d in roadmap_batch.docs} == {"learning_knowledge"}
    assert {d.content_type for d in roadmap_batch.docs} <= set(LEARNING_CONTENT_TYPES)


def test_roadmap_documents_pass_the_licence_gate(roadmap_batch):
    assert all(check_licence(d).allowed for d in roadmap_batch.docs)


def test_the_roadmap_list_covers_more_than_software(roadmap_batch):
    assert {"technical", "non_technical"} <= {d.role_family for d in roadmap_batch.docs}
