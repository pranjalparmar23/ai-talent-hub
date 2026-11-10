"""Licence gate: decides whether a document may be stored (Phase 7, task 7).

The rule is "no licence, no ingest" (docs/architecture/knowledge-layer.md):

- full_text sources are stored only when their licence is on the allowed list.
- link_only sources store just our own short description and the link, so the
  source's licence does not limit us, but a licence must still be recorded.

To allow a licence, add it to ALLOWED_FULL_TEXT. To forbid one, remove it.
"""

from dataclasses import dataclass

from app.ingest.schema import KnowledgeDoc

# Permissive licences: the text may be copied and shown if we give credit.
ALLOWED_FULL_TEXT: tuple[str, ...] = (
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "0BSD",
    "Unlicense",
    "CC0-1.0",
    "CC-BY-3.0",
    "CC-BY-4.0",
)

# Share-alike: copying the text may force the same licence on what we build
# with it. Refused until a person decides (knowledge-layer.md, open items).
NEEDS_DECISION: tuple[str, ...] = ("CC-BY-SA-3.0", "CC-BY-SA-4.0")

# Words that mean "we do not know the licence". They count as not recorded.
NOT_RECORDED: tuple[str, ...] = (
    "",
    "unknown",
    "none",
    "n/a",
    "na",
    "unspecified",
    "unlicensed",
    "tbd",
)

_ALLOWED_BY_LOWER = {name.lower(): name for name in ALLOWED_FULL_TEXT}
_DECISION_BY_LOWER = {name.lower(): name for name in NEEDS_DECISION}


@dataclass(frozen=True)
class LicenceDecision:
    allowed: bool
    licence: str  # the licence written the standard way when we know it
    reason: str  # plain words, safe to show in a log or an error


class LicenceRefused(ValueError):
    """Raised when a document must not be stored because of its licence."""


def check_licence(doc: KnowledgeDoc) -> LicenceDecision:
    """Say whether this document may be stored, and why."""
    raw = doc.licence.strip()
    key = raw.lower()

    if key in NOT_RECORDED:
        return LicenceDecision(False, raw, "no licence is recorded for this source")

    if doc.ingest_mode == "link_only":
        canonical = _ALLOWED_BY_LOWER.get(key) or _DECISION_BY_LOWER.get(key) or raw
        return LicenceDecision(
            True, canonical, "link only: stores our own description and the link"
        )

    if key in _ALLOWED_BY_LOWER:
        canonical = _ALLOWED_BY_LOWER[key]
        return LicenceDecision(
            True, canonical, f"{canonical} allows copying with credit"
        )

    if key in _DECISION_BY_LOWER:
        canonical = _DECISION_BY_LOWER[key]
        return LicenceDecision(
            False,
            canonical,
            f"{canonical} is share-alike and needs a human decision before "
            "its text is stored",
        )

    return LicenceDecision(
        False,
        raw,
        f"licence '{raw}' is not on the allowed list for storing full text",
    )


def require_licence(doc: KnowledgeDoc) -> KnowledgeDoc:
    """Return the document with its licence written the standard way, or refuse it."""
    decision = check_licence(doc)
    if not decision.allowed:
        raise LicenceRefused(f"{doc.url}: {decision.reason}")
    if decision.licence == doc.licence:
        return doc
    return doc.model_copy(update={"licence": decision.licence})
