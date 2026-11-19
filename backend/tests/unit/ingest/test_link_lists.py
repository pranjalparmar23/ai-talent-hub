"""Checks on the link lists that ship with the code (app/ingest/sources/data/*.yaml).

The adapter and the ingest run have their own tests, with small lists made for the
purpose. This file looks at the REAL lists, so a mistake in a YAML file (a bad address,
a repeated page, a missing credit line, a list nobody registered) fails the test suite
before it reaches the knowledge collections.

Every `*.yaml` file in the data folder is a link list and is checked, so a new list is
covered as soon as it is added. No test here uses the network: whether an address still
answers is the job of the link check (python -m app.ingest.run).
"""

import asyncio
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
import yaml

from app.ingest import run as run_module
from app.ingest.licence import check_licence
from app.ingest.schema import (
    LEARNING_COLLECTION,
    LEARNING_CONTENT_TYPES,
    LINK_ONLY_TEXT_MAX_CHARS,
)
from app.ingest.sources.link_only import DATA_DIR, LinkOnlyAdapter, normalise_url

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc)

LIST_FILES = sorted(DATA_DIR.glob("*.yaml"))
LIST_IDS = [path.stem for path in LIST_FILES]

# A summary is one plain sentence about the page. This is generous, but it catches a
# pasted paragraph.
SUMMARY_MAX_CHARS = 300


def raw_entries(path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["entries"]


def top(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def batches():
    """{file stem: batch}, loaded the way one ingest run does it: in file order, with
    one set of addresses shared by all the lists."""
    shared: set[str] = set()
    adapters = {
        path.stem: LinkOnlyAdapter(path, clock=lambda: NOW, seen_urls=shared)
        for path in LIST_FILES
    }

    async def fetch_all():
        return {stem: await adapter.fetch() for stem, adapter in adapters.items()}

    # A private loop, closed after use. Do NOT use asyncio.run() here: it resets the
    # "current" event loop to None, and tests/conftest.py shares one session-wide loop
    # between all async tests, so every async test after this fixture would fail.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fetch_all())
    finally:
        loop.close()


# ── the files ────────────────────────────────────────────────


def test_the_lists_are_inside_the_backend_package():
    # The Docker image copies only backend/, so the data must live there.
    assert LIST_FILES, "no link lists found"
    assert "app" in DATA_DIR.parts


def test_the_expected_lists_are_there():
    assert {"roadmap_sh", "official_docs", "freecodecamp"} <= set(LIST_IDS)


# ── each list on its own ─────────────────────────────────────


@pytest.mark.parametrize("stem", LIST_IDS)
def test_every_entry_is_a_valid_document(batches, stem):
    batch = batches[stem]
    assert batch.skipped == [], f"{stem}: {[(s.path, s.reason) for s in batch.skipped]}"
    assert len(batch.docs) == len(raw_entries(LIST_FILES[LIST_IDS.index(stem)]))


@pytest.mark.parametrize("stem", LIST_IDS)
def test_every_address_is_written_in_tidy_form(stem):
    # A document id is a hash of source_id and address. An address that tidying would
    # change gets a different id once someone tidies it, and is then stored twice.
    path = LIST_FILES[LIST_IDS.index(stem)]
    untidy = [
        e["url"] for e in raw_entries(path) if normalise_url(e["url"]) != e["url"]
    ]
    assert untidy == []


@pytest.mark.parametrize("stem", LIST_IDS)
def test_addresses_are_plain_https_pages_on_public_looking_hosts(stem):
    path = LIST_FILES[LIST_IDS.index(stem)]
    for entry in raw_entries(path):
        parts = urlsplit(entry["url"])
        host = parts.hostname or ""
        assert parts.scheme == "https", entry["url"]
        assert "." in host and host != "localhost", entry["url"]
        assert not host.replace(".", "").isdigit(), f"IP address: {entry['url']}"
        assert parts.username is None, entry["url"]
        assert parts.fragment == "", entry["url"]


@pytest.mark.parametrize("stem", LIST_IDS)
def test_titles_are_not_repeated(stem):
    path = LIST_FILES[LIST_IDS.index(stem)]
    titles = [e["title"].strip().lower() for e in raw_entries(path)]
    assert len(titles) == len(set(titles))


@pytest.mark.parametrize("stem", LIST_IDS)
def test_each_summary_is_one_short_sentence_we_wrote(stem):
    path = LIST_FILES[LIST_IDS.index(stem)]
    for entry in raw_entries(path):
        summary = entry["summary"].strip()
        assert "\n" not in summary, entry["url"]
        assert len(summary) <= SUMMARY_MAX_CHARS, entry["url"]
        assert summary.endswith("."), f"no full stop: {entry['url']}"


@pytest.mark.parametrize("stem", LIST_IDS)
def test_documents_hold_only_a_link_and_our_own_text(batches, stem):
    for doc in batches[stem].docs:
        assert doc.ingest_mode == "link_only"
        assert len(doc.text) <= LINK_ONLY_TEXT_MAX_CHARS
        assert doc.collection == LEARNING_COLLECTION
        assert doc.content_type in LEARNING_CONTENT_TYPES
        assert check_licence(doc).allowed


@pytest.mark.parametrize("stem", LIST_IDS)
def test_each_list_says_it_is_link_only_and_names_its_owner(stem):
    path = LIST_FILES[LIST_IDS.index(stem)]
    attribution = top(path)["attribution"].lower()
    assert "link only" in attribution
    assert "no content is copied" in attribution
    assert (
        len(attribution) > len("link only: no content is copied.") + 5
    )  # names someone


@pytest.mark.parametrize("stem", LIST_IDS)
def test_each_list_has_a_source_id_that_the_run_command_knows(stem):
    path = LIST_FILES[LIST_IDS.index(stem)]
    source_id = top(path)["source_id"]
    spec = run_module.SOURCES.get(source_id)
    assert spec is not None, f"{source_id} is not registered in app/ingest/run.py"
    assert spec.link_only, f"{source_id} is registered but not as a link source"
    assert spec.build(None).path == path, f"{source_id} reads a different file"


# ── all the lists together ───────────────────────────────────


def test_every_registered_link_source_has_a_list():
    registered = {n for n, s in run_module.SOURCES.items() if s.link_only}
    in_files = {top(path)["source_id"] for path in LIST_FILES}
    assert registered == in_files


def test_source_ids_are_not_shared_between_lists():
    ids = [top(path)["source_id"] for path in LIST_FILES]
    assert len(ids) == len(set(ids))


def test_no_page_is_named_in_two_lists(batches):
    # The lists were loaded with one shared set, so a page named twice was skipped
    # with this reason.
    repeated = [
        (stem, s.path)
        for stem, batch in batches.items()
        for s in batch.skipped
        if "another source" in s.reason
    ]
    assert repeated == []


def test_document_ids_are_unique_across_all_the_lists(batches):
    ids = [d.doc_id for batch in batches.values() for d in batch.docs]
    assert len(ids) == len(set(ids))


# ── what the lists cover ─────────────────────────────────────

MINIMUM_ENTRIES = {"roadmap_sh": 30, "official_docs": 30, "freecodecamp": 15}


@pytest.mark.parametrize(("stem", "minimum"), sorted(MINIMUM_ENTRIES.items()))
def test_a_list_does_not_quietly_lose_most_of_its_entries(batches, stem, minimum):
    assert len(batches[stem].docs) >= minimum


def test_together_the_lists_cover_more_than_one_kind_of_page_and_role(batches):
    docs = [d for batch in batches.values() for d in batch.docs]
    assert {"roadmap", "docs", "course", "video", "article"} <= {
        d.content_type for d in docs
    }
    assert {"technical", "non_technical"} <= {d.role_family for d in docs}
    assert {"software-engineering", "data"} <= {d.domain for d in docs}
