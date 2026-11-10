"""Unit tests for the knowledge document schema. No network, no ChromaDB."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.ingest import schema as s

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
    return s.KnowledgeDoc(**data)


def hashed(doc, **kw):
    params = dict(embedding_model="all-MiniLM-L6-v2", chunk_size=500, chunk_overlap=50)
    params.update(kw)
    return doc.model_copy(
        update={"content_hash": s.compute_content_hash(doc.text, **params)}
    )


# ── a valid document ─────────────────────────────────────────


def test_valid_doc_gets_defaults_and_cleaned_values():
    doc = make_doc(
        title="  Behavioral   interview\n guide ", source_id=" Tech-Interview-Handbook "
    )
    assert doc.title == "Behavioral interview guide"
    assert doc.source_id == "tech-interview-handbook"
    assert (doc.role_family, doc.level, doc.ingest_mode, doc.language) == (
        "all",
        "all",
        "full_text",
        "en",
    )
    assert doc.content_hash == ""


def test_url_fragment_is_dropped():
    assert make_doc(url="https://x.test/a#section-2").url == "https://x.test/a"


def test_naive_times_are_taken_as_utc_and_aware_times_converted():
    naive = make_doc(
        fetched_at=datetime(2026, 11, 10, 6, 0), expires_at="2027-01-01T00:00:00"
    )
    assert naive.fetched_at.tzinfo is timezone.utc
    ist = timezone(timedelta(hours=5, minutes=30))
    aware = make_doc(fetched_at=datetime(2026, 11, 10, 11, 30, tzinfo=ist))
    assert aware.fetched_at == NOW


def test_extra_fields_are_refused():
    with pytest.raises(ValidationError):
        make_doc(published_by="someone")


# ── rejected input, with a message naming the field ──────────


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", "Has Spaces"),
        ("source_id", ""),
        ("domain", "Software Engineering"),
        ("url", "ftp://x.test/a"),
        ("url", "not a url"),
        ("url", ""),
        ("title", "   "),
        ("title", "x" * 301),
        ("content_type", "podcast"),
        ("role_family", "everyone"),
        ("level", "principal"),
        ("ingest_mode", "copy_everything"),
        ("licence", "  "),
        ("attribution", ""),
        ("text", "   \n "),
    ],
)
def test_bad_values_are_rejected_and_name_the_field(field, value):
    with pytest.raises(ValidationError) as err:
        make_doc(**{field: value})
    assert field in str(err.value)


@pytest.mark.parametrize("language", ["hi", "fr", "", "english"])
def test_non_english_is_refused(language):
    with pytest.raises(ValidationError) as err:
        make_doc(language=language)
    assert "English only" in str(err.value)


def test_expiry_must_be_after_fetch():
    with pytest.raises(ValidationError, match="expires_at must be after fetched_at"):
        make_doc(expires_at=NOW)


def test_link_only_text_is_a_short_description():
    ok = make_doc(ingest_mode="link_only", licence="proprietary", text="x" * 500)
    assert ok.ingest_mode == "link_only"
    with pytest.raises(ValidationError, match="short description"):
        make_doc(ingest_mode="link_only", licence="proprietary", text="x" * 501)
    # full text sources may be long
    assert len(make_doc(text="x" * 5000).text) == 5000


# ── collections ──────────────────────────────────────────────


@pytest.mark.parametrize("ctype", s.INTERVIEW_CONTENT_TYPES)
def test_interview_content_goes_to_interview_knowledge(ctype):
    assert make_doc(content_type=ctype).collection == "interview_knowledge"


@pytest.mark.parametrize("ctype", s.LEARNING_CONTENT_TYPES)
def test_learning_content_goes_to_learning_knowledge(ctype):
    assert make_doc(content_type=ctype).collection == "learning_knowledge"


def test_every_content_type_has_exactly_one_collection():
    all_types = s.INTERVIEW_CONTENT_TYPES + s.LEARNING_CONTENT_TYPES
    assert len(all_types) == len(set(all_types)) == len(s.COLLECTION_BY_CONTENT_TYPE)


def test_collection_for_unknown_type_explains_itself():
    with pytest.raises(ValueError, match="known:"):
        s.collection_for("podcast")


# ── ids ──────────────────────────────────────────────────────


def test_doc_id_is_stable_short_and_depends_on_source_and_url():
    a = make_doc()
    assert a.doc_id == make_doc().doc_id
    assert len(a.doc_id) == 16
    assert a.doc_id != make_doc(url="https://x.test/other").doc_id
    assert a.doc_id != make_doc(source_id="roadmap-sh").doc_id


def test_doc_id_ignores_a_url_fragment():
    assert (
        make_doc(url="https://x.test/a#one").doc_id
        == make_doc(url="https://x.test/a#two").doc_id
    )


def test_chunk_id_is_deterministic_and_sortable():
    assert s.make_chunk_id("abc", 3) == "abc:0003"
    assert s.make_chunk_id("abc", 12) == "abc:0012"
    with pytest.raises(ValueError):
        s.make_chunk_id("abc", -1)


# ── content hash ─────────────────────────────────────────────

BASE = dict(embedding_model="all-MiniLM-L6-v2", chunk_size=500, chunk_overlap=50)


def test_hash_ignores_layout_only_changes():
    a = s.compute_content_hash("Use the  STAR\nmethod.", **BASE)
    b = s.compute_content_hash("  Use the STAR method.  ", **BASE)
    assert a == b


def test_hash_ignores_unicode_form():
    composed = "caf\u00e9"  # one character (precomposed)
    decomposed = "cafe\u0301"  # e followed by a combining accent
    assert composed != decomposed  # guards against the escapes being pasted away
    assert s.compute_content_hash(composed, **BASE) == s.compute_content_hash(
        decomposed, **BASE
    )


@pytest.mark.parametrize(
    "change",
    [
        dict(embedding_model="other-model"),
        dict(chunk_size=800),
        dict(chunk_overlap=100),
    ],
)
def test_hash_changes_with_model_or_chunk_settings(change):
    assert s.compute_content_hash("same text", **BASE) != s.compute_content_hash(
        "same text", **{**BASE, **change}
    )


def test_hash_changes_with_the_text():
    assert s.compute_content_hash("one", **BASE) != s.compute_content_hash(
        "two", **BASE
    )


# ── chunk metadata ───────────────────────────────────────────


def test_chunk_metadata_is_flat_and_chroma_safe():
    doc = hashed(make_doc())
    meta = s.chunk_metadata(doc, 2, embedding_model="all-MiniLM-L6-v2")
    for key, value in meta.items():
        assert isinstance(value, (str, int, float, bool)), key
        assert value is not None and not isinstance(value, (list, dict)), key
    assert meta["chunk_index"] == 2
    assert meta["schema_version"] == 2
    assert meta["doc_id"] == doc.doc_id
    assert meta["language"] == "en"
    assert "text" not in meta


def test_chunk_metadata_dates_are_unix_seconds():
    doc = hashed(make_doc())
    meta = s.chunk_metadata(doc, 0, embedding_model="m")
    assert meta["fetched_at_ts"] == int(NOW.timestamp())
    assert meta["expires_at_ts"] - meta["fetched_at_ts"] == 90 * 24 * 3600


def test_chunk_metadata_needs_a_hash():
    with pytest.raises(ValueError, match="content_hash"):
        s.chunk_metadata(make_doc(), 0, embedding_model="m")


def test_citation_has_what_the_app_shows():
    meta = s.chunk_metadata(hashed(make_doc()), 0, embedding_model="m")
    assert s.citation_from_metadata(meta) == {
        "title": "Behavioral interview guide",
        "url": "https://www.techinterviewhandbook.org/behavioral-interview/",
        "licence": "MIT",
        "attribution": "Tech Interview Handbook, MIT licence",
    }


def test_citation_from_old_or_partial_metadata_does_not_raise():
    assert s.citation_from_metadata({"topic": "Docker"}) == {
        "title": "",
        "url": "",
        "licence": "",
        "attribution": "",
    }
