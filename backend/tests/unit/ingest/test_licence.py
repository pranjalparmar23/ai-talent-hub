"""Unit tests for the licence gate. No network, no ChromaDB."""

from datetime import datetime, timedelta, timezone

import pytest

from app.ingest import licence as lic
from app.ingest.schema import KnowledgeDoc

NOW = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)


def make_doc(**overrides):
    data = dict(
        source_id="tech-interview-handbook",
        url="https://www.techinterviewhandbook.org/behavioral-interview/",
        title="Behavioral interview guide",
        domain="software-engineering",
        content_type="experience",
        licence="MIT",
        attribution="Tech Interview Handbook, MIT licence",
        fetched_at=NOW,
        expires_at=NOW + timedelta(days=90),
        text="Use the STAR method to structure your answers.",
    )
    data.update(overrides)
    return KnowledgeDoc(**data)


# ── full text ────────────────────────────────────────────────


@pytest.mark.parametrize("name", lic.ALLOWED_FULL_TEXT)
def test_every_allowed_licence_is_accepted_for_full_text(name):
    decision = lic.check_licence(make_doc(licence=name))
    assert decision.allowed is True
    assert decision.licence == name


@pytest.mark.parametrize("written", ["mit", "MIT", " Mit ", "cc-by-4.0", "APACHE-2.0"])
def test_licence_spelling_is_matched_without_case(written):
    decision = lic.check_licence(make_doc(licence=written))
    assert decision.allowed is True
    assert decision.licence in lic.ALLOWED_FULL_TEXT


@pytest.mark.parametrize("name", lic.NEEDS_DECISION)
def test_share_alike_is_refused_and_says_a_person_must_decide(name):
    decision = lic.check_licence(make_doc(licence=name))
    assert decision.allowed is False
    assert "human decision" in decision.reason


@pytest.mark.parametrize(
    "name", ["GPL-3.0-only", "CC-BY-NC-4.0", "CC-BY-ND-4.0", "proprietary", "WTFPL"]
)
def test_other_licences_are_refused_for_full_text(name):
    decision = lic.check_licence(make_doc(licence=name))
    assert decision.allowed is False
    assert name in decision.reason


# an empty licence is already stopped by the schema, so test the real words
@pytest.mark.parametrize("word", [w for w in lic.NOT_RECORDED if w])
def test_unknown_licence_words_count_as_not_recorded(word):
    for mode in ("full_text", "link_only"):
        decision = lic.check_licence(
            make_doc(licence=word, ingest_mode=mode, text="short")
        )
        assert decision.allowed is False
        assert "no licence is recorded" in decision.reason


def test_the_two_lists_do_not_overlap():
    assert not set(lic.ALLOWED_FULL_TEXT) & set(lic.NEEDS_DECISION)


# ── link only ────────────────────────────────────────────────


def test_link_only_accepts_any_recorded_licence():
    doc = make_doc(ingest_mode="link_only", licence="proprietary", text="Roadmap")
    decision = lic.check_licence(doc)
    assert decision.allowed is True
    assert decision.licence == "proprietary"


def test_link_only_accepts_share_alike_because_no_source_text_is_copied():
    doc = make_doc(ingest_mode="link_only", licence="cc-by-sa-4.0", text="Primer")
    decision = lic.check_licence(doc)
    assert decision.allowed is True
    assert decision.licence == "CC-BY-SA-4.0"


# ── require_licence ──────────────────────────────────────────


def test_require_returns_the_same_document_when_nothing_changes():
    doc = make_doc()
    assert lic.require_licence(doc) is doc


def test_require_writes_the_licence_the_standard_way():
    doc = lic.require_licence(make_doc(licence="mit"))
    assert doc.licence == "MIT"


def test_require_refuses_with_the_url_and_the_reason():
    with pytest.raises(lic.LicenceRefused) as err:
        lic.require_licence(make_doc(licence="GPL-3.0-only"))
    assert "behavioral-interview" in str(err.value)
    assert "GPL-3.0-only" in str(err.value)


def test_a_refusal_is_a_value_error():
    assert issubclass(lic.LicenceRefused, ValueError)
