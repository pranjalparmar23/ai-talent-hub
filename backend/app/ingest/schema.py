"""Knowledge document schema for the Phase 7 ingestion pipeline.

One `KnowledgeDoc` is one source page or file after fetching and cleaning. The
pipeline splits it into chunks and stores each chunk with flat metadata built by
`chunk_metadata()`, so a search result can show its source, licence and
attribution without a second lookup.

Design and field meanings: docs/architecture/knowledge-layer.md.

This module is pure Python (pydantic plus the standard library). It does not
import the embedding model or ChromaDB, so it is fast to import and easy to test.
The embedding model name and chunk settings are passed in by the pipeline.
"""

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

SCHEMA_VERSION = 2
LANGUAGE = "en"  # all-MiniLM-L6-v2 is English only (knowledge-layer.md, decision 3)

INTERVIEW_COLLECTION = "interview_knowledge"
LEARNING_COLLECTION = "learning_knowledge"

INTERVIEW_CONTENT_TYPES = (
    "interview_question",
    "behavioral_question",
    "experience",
    "company_pattern",
    "concept_note",
)
LEARNING_CONTENT_TYPES = ("course", "article", "video", "roadmap", "docs")

# Which collection each content type is stored in.
COLLECTION_BY_CONTENT_TYPE: dict[str, str] = {
    **{t: INTERVIEW_COLLECTION for t in INTERVIEW_CONTENT_TYPES},
    **{t: LEARNING_COLLECTION for t in LEARNING_CONTENT_TYPES},
}

ROLE_FAMILIES = ("technical", "non_technical", "all")
LEVELS = ("entry", "mid", "senior", "all")
INGEST_MODES = ("full_text", "link_only")

# A starting set only: `domain` accepts any lowercase slug so Phase 9 can add roles.
DOMAINS_STARTER = (
    "software-engineering",
    "data",
    "product",
    "design",
    "marketing",
    "finance",
    "hr",
    "sales",
    "operations",
    "general",
)

LINK_ONLY_TEXT_MAX_CHARS = 500  # our own short description, never the source's text
TITLE_MAX_CHARS = 300

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_URL = re.compile(r"^https?://[^\s/?#]+\S*$")


def _one_of(value: str, allowed: tuple[str, ...], field: str) -> str:
    cleaned = (value or "").strip().lower()
    if cleaned not in allowed:
        raise ValueError(f"{field} must be one of {', '.join(allowed)} (got {value!r})")
    return cleaned


def _slug(value: str, field: str) -> str:
    cleaned = (value or "").strip().lower()
    if not _SLUG.match(cleaned):
        raise ValueError(
            f"{field} must be lowercase letters, digits and hyphens, "
            f"1 to 63 characters, starting with a letter or digit (got {value!r})"
        )
    return cleaned


def _utc(value: datetime) -> datetime:
    """Naive times are taken as UTC; aware times are converted to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def collection_for(content_type: str) -> str:
    """The collection a content type is stored in. Raises ValueError if unknown."""
    try:
        return COLLECTION_BY_CONTENT_TYPE[content_type]
    except KeyError:
        known = ", ".join(COLLECTION_BY_CONTENT_TYPE)
        raise ValueError(f"unknown content_type {content_type!r} (known: {known})")


class KnowledgeDoc(BaseModel):
    """One cleaned source document, ready for chunking and embedding."""

    model_config = ConfigDict(extra="forbid")

    source_id: str  # an approved source from docs/mcp/vetting-log.md
    url: str
    title: str
    domain: str
    content_type: str
    licence: str  # SPDX id such as "MIT", or "proprietary" for link-only sources
    attribution: str  # the exact credit line shown to the user
    fetched_at: datetime
    expires_at: datetime
    text: str
    role_family: str = "all"
    level: str = "all"
    ingest_mode: str = "full_text"
    language: str = LANGUAGE
    content_hash: str = ""  # set by the pipeline, see compute_content_hash()

    # ── field checks (each message names the field, for pipeline logs) ───────

    @field_validator("source_id")
    @classmethod
    def _check_source_id(cls, v: str) -> str:
        return _slug(v, "source_id")

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, v: str) -> str:
        return _slug(v, "domain")

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        cleaned = (v or "").strip().split("#", 1)[0]
        if not _URL.match(cleaned):
            raise ValueError(f"url must start with http:// or https:// (got {v!r})")
        return cleaned

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str) -> str:
        cleaned = " ".join((v or "").split())
        if not cleaned:
            raise ValueError("title must not be empty")
        if len(cleaned) > TITLE_MAX_CHARS:
            raise ValueError(f"title is longer than {TITLE_MAX_CHARS} characters")
        return cleaned

    @field_validator("content_type")
    @classmethod
    def _check_content_type(cls, v: str) -> str:
        cleaned = (v or "").strip().lower()
        collection_for(cleaned)
        return cleaned

    @field_validator("role_family")
    @classmethod
    def _check_role_family(cls, v: str) -> str:
        return _one_of(v, ROLE_FAMILIES, "role_family")

    @field_validator("level")
    @classmethod
    def _check_level(cls, v: str) -> str:
        return _one_of(v, LEVELS, "level")

    @field_validator("ingest_mode")
    @classmethod
    def _check_ingest_mode(cls, v: str) -> str:
        return _one_of(v, INGEST_MODES, "ingest_mode")

    @field_validator("licence", "attribution")
    @classmethod
    def _check_required_text(cls, v: str, info) -> str:
        cleaned = " ".join((v or "").split())
        if not cleaned:
            raise ValueError(f"{info.field_name} must not be empty")
        if len(cleaned) > 300:
            raise ValueError(f"{info.field_name} is longer than 300 characters")
        return cleaned

    @field_validator("language")
    @classmethod
    def _check_language(cls, v: str) -> str:
        cleaned = (v or "").strip().lower()
        if cleaned != LANGUAGE:
            raise ValueError(
                f"language must be {LANGUAGE!r}: the embedding model is English only "
                f"(got {v!r})"
            )
        return cleaned

    @field_validator("fetched_at", "expires_at")
    @classmethod
    def _check_time(cls, v: datetime) -> datetime:
        return _utc(v)

    @field_validator("text")
    @classmethod
    def _check_text(cls, v: str) -> str:
        cleaned = (v or "").strip()
        if not cleaned:
            raise ValueError("text must not be empty")
        return cleaned

    @model_validator(mode="after")
    def _check_whole(self) -> "KnowledgeDoc":
        if self.expires_at <= self.fetched_at:
            raise ValueError("expires_at must be after fetched_at")
        if (
            self.ingest_mode == "link_only"
            and len(self.text) > LINK_ONLY_TEXT_MAX_CHARS
        ):
            raise ValueError(
                f"for link_only sources, text is our own short description "
                f"(at most {LINK_ONLY_TEXT_MAX_CHARS} characters), not the source's text"
            )
        return self

    # ── derived values ───────────────────────────────────────────────────────

    @property
    def doc_id(self) -> str:
        return make_doc_id(self.source_id, self.url)

    @property
    def collection(self) -> str:
        return collection_for(self.content_type)


# ── ids, hashes and metadata ────────────────────────────────────────────────


def make_doc_id(source_id: str, url: str) -> str:
    """First 16 hex characters of SHA-256 over `source_id|url`."""
    return hashlib.sha256(f"{source_id}|{url}".encode("utf-8")).hexdigest()[:16]


def make_chunk_id(doc_id: str, chunk_index: int) -> str:
    """Deterministic chunk id, so loading the same content twice changes nothing."""
    if chunk_index < 0:
        raise ValueError("chunk_index must not be negative")
    return f"{doc_id}:{chunk_index:04d}"


def normalise_text(text: str) -> str:
    """Unicode NFC and single spaces, so layout-only changes do not change the hash."""
    return " ".join(unicodedata.normalize("NFC", text or "").split())


def compute_content_hash(
    text: str, *, embedding_model: str, chunk_size: int, chunk_overlap: int
) -> str:
    """Fingerprint of the text AND how it is embedded.

    Changing the text, the embedding model or the chunk settings changes the hash,
    which makes the pipeline re-embed the document.
    """
    payload = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "text": normalise_text(text),
            "embedding_model": embedding_model,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def chunk_metadata(
    doc: KnowledgeDoc, chunk_index: int, *, embedding_model: str
) -> dict[str, Any]:
    """Flat metadata stored with one chunk in ChromaDB.

    ChromaDB accepts only text, whole numbers, decimals and true/false, so there
    are no lists, no None, and the dates are also stored as Unix seconds (it can
    compare numbers but not date text).
    """
    if not doc.content_hash:
        raise ValueError("content_hash is empty: compute it before building metadata")
    return {
        "doc_id": doc.doc_id,
        "source_id": doc.source_id,
        "url": doc.url,
        "title": doc.title,
        "domain": doc.domain,
        "role_family": doc.role_family,
        "level": doc.level,
        "content_type": doc.content_type,
        "ingest_mode": doc.ingest_mode,
        "licence": doc.licence,
        "attribution": doc.attribution,
        "language": doc.language,
        "content_hash": doc.content_hash,
        "chunk_index": chunk_index,
        "schema_version": SCHEMA_VERSION,
        "embedding_model": embedding_model,
        "fetched_at_ts": int(doc.fetched_at.timestamp()),
        "expires_at_ts": int(doc.expires_at.timestamp()),
    }


def citation_from_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
    """What the app shows for a retrieved chunk: where it came from and its credit."""
    return {
        "title": str(metadata.get("title", "")),
        "url": str(metadata.get("url", "")),
        "licence": str(metadata.get("licence", "")),
        "attribution": str(metadata.get("attribution", "")),
    }
