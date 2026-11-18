"""Adapter for the Tech Interview Handbook (Phase 7, task 6).

Source: https://github.com/yangshun/tech-interview-handbook (MIT licence).
Vetting verdict: approve-ingest, with these conditions (docs/mcp/vetting-log.md):
read the LICENSE file at ingest time, store source, URL, licence and attribution
with every document, skip promotional content, run the link-health check later.

What this adapter does for each page of the handbook:
1. skips files that are not real pages (templates, old copies, pages that only
   promote paid products);
2. turns the MDX page into plain text: front matter, imports, ads, HTML and
   images are removed, links keep their text;
3. removes paragraphs, list items and table rows that carry affiliate links or
   sales wording;
4. builds a KnowledgeDoc with the right type, level and credit line.

Anything left out is returned in SourceBatch.skipped with a reason, so it can be
reviewed. Page URLs are built from the file path and are checked later by the
link-health task.
"""

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml
from pydantic import ValidationError

from app.ingest.schema import KnowledgeDoc
from app.ingest.sources.base import (
    DEFAULT_EXPIRY,
    Skipped,
    SourceBatch,
    SourceError,
    SourceReader,
)

logger = logging.getLogger(__name__)

SOURCE_ID = "tech-interview-handbook"
OWNER = "yangshun"
REPO = "tech-interview-handbook"
SITE = "https://www.techinterviewhandbook.org"

LICENSE_PATH = "LICENSE"
CONTENT_DIR = "apps/website/contents"
CONTENT_DIRS = (CONTENT_DIR, f"{CONTENT_DIR}/algorithms")
DOMAIN = "software-engineering"
MIN_BODY_CHARS = 200  # a page shorter than this after cleaning is not worth storing

# Whole pages we never ingest.
SKIP_PAGES = {
    "resume-old.md": "old copy of a page that was replaced",
    "best-coding-interview-courses.md": "promotes paid third-party courses",
    "mock-interviews.md": "referral links to paid platforms",
}

# Wording that marks a paragraph, list item or table row as a sales message.
PROMO_URL_MARKERS = ("shareasale.com", "aff=", "affiliate", "iio.sh/", "?ref=", "&ref=")
_PROMO_PHRASE = re.compile(
    r"\b\d{1,3}% (?:discount|off)\b|\bjoin today\b|\blifetime access\b|\bone-time fee\b",
    re.IGNORECASE,
)

# ── Page type, who it is for, and how senior ─────────────────
# (content_type, role_family, level). Anything not listed is a technical concept note.
_PAGE_RULES: dict[str, tuple[str, str, str]] = {
    "behavioral-interview-questions": ("behavioral_question", "all", "all"),
    "behavioral-interview-senior-candidates": ("concept_note", "all", "senior"),
    "interview-formats-top-companies": ("company_pattern", "technical", "all"),
    "best-practice-questions": ("interview_question", "technical", "all"),
    "coding-interview-study-plan": ("roadmap", "technical", "all"),
}
_ALL_ROLES = (
    "career-growth",
    "choosing-between-companies",
    "engineering-levels",
    "final-questions",
    "landscape",
    "negotiation",
    "negotiation-rules",
    "resume",
    "self-introduction",
    "understanding-compensation",
)


def classify_page(path: str) -> tuple[str, str, str]:
    """(content_type, role_family, level) for a page, from its file path."""
    stem = path.rsplit("/", 1)[-1].removesuffix(".md")
    if stem in _PAGE_RULES:
        return _PAGE_RULES[stem]
    if stem.startswith("behavioral") or stem in _ALL_ROLES:
        return ("concept_note", "all", "all")
    return ("concept_note", "technical", "all")


def page_url(path: str) -> str:
    """Public address of a page: algorithms/tree.md -> .../algorithms/tree/"""
    relative = path.removeprefix(CONTENT_DIR + "/").removesuffix(".md")
    return f"{SITE}/{relative}/"


# ── The licence ──────────────────────────────────────────────

_COPYRIGHT = re.compile(r"^Copyright \(c\) (.+)$", re.IGNORECASE | re.MULTILINE)


def read_attribution(licence_text: str) -> str:
    """Check the LICENSE file is the MIT licence and build the credit line from it."""
    head = licence_text.lstrip().lower()
    if not head.startswith("mit license") or "permission is hereby granted" not in head:
        raise SourceError(
            f"{OWNER}/{REPO}/{LICENSE_PATH} is not the MIT licence any more. "
            "Review the licence before ingesting."
        )
    match = _COPYRIGHT.search(licence_text)
    if not match:
        raise SourceError(f"{OWNER}/{REPO}/{LICENSE_PATH} has no copyright line")
    return (
        f"Tech Interview Handbook, Copyright (c) {match.group(1).strip()}, MIT licence"
    )


# ── Cleaning a page ──────────────────────────────────────────

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_CODE = re.compile(r"(```.*?```|`[^`\n]+`)", re.DOTALL)
_HEAD_BLOCK = re.compile(r"<head>.*?</head>", re.DOTALL | re.IGNORECASE)
_FIGURE = re.compile(r"<figure>.*?</figure>", re.DOTALL | re.IGNORECASE)
_IMPORT = re.compile(r"^import .*$", re.MULTILINE)
_COMPONENT = re.compile(r"<[A-Z][A-Za-z0-9]*\b[^<>]*/>")
_LINE_BREAK = re.compile(r"<br\s*/?>|</br>", re.IGNORECASE)
_HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")
_ADMONITION = re.compile(r"^:::.*$", re.MULTILINE)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_FENCED_CODE = re.compile(r"(```.*?```)", re.DOTALL)
# [text](url), where the text may hold `code` and one level of [brackets]
_LINK = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])+)\]\([^)]*\)")
_MANY_BLANK_LINES = re.compile(r"\n{3,}")


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """The page's header block (id, title, ...) and the rest of the page."""
    text = text.replace("\r\n", "\n")
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError("front matter is not a mapping")
    return meta, text[match.end() :]


def _outside(pattern: re.Pattern[str], text: str, change: Callable[[str], str]) -> str:
    """Apply `change` to the parts of `text` that `pattern` does not match."""
    parts = pattern.split(text)
    return "".join(p if i % 2 else change(p) for i, p in enumerate(parts))


def _outside_code(text: str, change: Callable[[str], str]) -> str:
    """Change the prose only; code blocks and `inline code` stay as written."""
    return _outside(_CODE, text, change)


def _strip_mdx(prose: str) -> str:
    prose = _HEAD_BLOCK.sub("", prose)
    prose = _FIGURE.sub("", prose)
    prose = _IMPORT.sub("", prose)
    prose = _COMPONENT.sub("", prose)
    prose = _LINE_BREAK.sub("\n", prose)
    prose = _HTML_TAG.sub("", prose)
    prose = _ADMONITION.sub("", prose)
    return _IMAGE.sub("", prose)


def _blocks(text: str) -> list[str]:
    """Paragraph-sized blocks (split on blank lines, but never inside a code fence)."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def is_promotional(text: str) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in PROMO_URL_MARKERS) or bool(
        _PROMO_PHRASE.search(text)
    )


_LIST_START = re.compile(r"^\s*(?:[|\-*+]|\d+\.)")


def _drop_promotional(blocks: list[str]) -> tuple[list[str], int]:
    """Remove sales paragraphs, and sales items inside lists and tables."""
    kept: list[str] = []
    removed = 0
    for block in blocks:
        if "```" in block:
            kept.append(block)
        elif _LIST_START.match(block):
            lines = block.split("\n")
            good = [ln for ln in lines if not is_promotional(ln)]
            removed += len(lines) - len(good)
            if good:
                kept.append("\n".join(good))
        elif is_promotional(block):
            removed += 1
        else:
            kept.append(block)
    return kept, removed


def _heading_level(block: str | None) -> int:
    if not block or "\n" in block:
        return 0
    match = re.match(r"(#{1,6})\s", block)
    return len(match.group(1)) if match else 0


def _drop_empty_headings(blocks: list[str]) -> list[str]:
    """A heading whose section lost all its text is removed too."""
    while True:
        out = []
        for i, block in enumerate(blocks):
            level = _heading_level(block)
            if level:
                nxt = blocks[i + 1] if i + 1 < len(blocks) else None
                next_level = _heading_level(nxt)
                if nxt is None or (next_level and next_level <= level):
                    continue
            out.append(block)
        if len(out) == len(blocks):
            return out
        blocks = out


def clean_page(body: str) -> tuple[str, int]:
    """Plain text for one page, and how many promotional items were removed."""
    body = _outside_code(body, _strip_mdx)
    blocks, removed = _drop_promotional(_blocks(body))
    blocks = [_outside(_FENCED_CODE, b, lambda p: _LINK.sub(r"\1", p)) for b in blocks]
    blocks = _drop_empty_headings(blocks)
    text = _MANY_BLANK_LINES.sub("\n\n", "\n\n".join(blocks)).strip()
    return text, removed


# ── The adapter ──────────────────────────────────────────────


class TechInterviewHandbookAdapter:
    source_id = SOURCE_ID

    def __init__(
        self,
        reader: SourceReader | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        expiry: timedelta = DEFAULT_EXPIRY,
    ) -> None:
        if reader is None:
            from app.ingest.sources.readers import GatewayGitHubReader

            reader = GatewayGitHubReader(OWNER, REPO)
        self.reader = reader
        self.clock = clock
        self.expiry = expiry

    async def fetch(self) -> SourceBatch:
        attribution = read_attribution(await self.reader.read_file(LICENSE_PATH))
        fetched_at = self.clock()
        batch = SourceBatch(source_id=SOURCE_ID)
        first_error = ""

        for directory in CONTENT_DIRS:
            for path in await self.reader.list_files(directory):
                name = path.rsplit("/", 1)[-1]
                reason = self._skip_reason(name)
                if reason:
                    batch.skipped.append(Skipped(path, reason))
                    continue
                try:
                    raw = await self.reader.read_file(path)
                except SourceError as err:
                    first_error = first_error or str(err)
                    batch.skipped.append(Skipped(path, f"could not be read: {err}"))
                    continue
                doc, why = self._build_doc(path, raw, attribution, fetched_at)
                if doc:
                    batch.docs.append(doc)
                else:
                    batch.skipped.append(Skipped(path, why))

        if not batch.docs:
            raise SourceError(
                "no page of the Tech Interview Handbook could be turned into a "
                f"document. First error: {first_error or 'none (all pages skipped)'}"
            )
        logger.info(
            "tech-interview-handbook: %d documents, %d pages skipped",
            len(batch.docs),
            len(batch.skipped),
        )
        return batch

    @staticmethod
    def _skip_reason(name: str) -> str:
        if name in SKIP_PAGES:
            return SKIP_PAGES[name]
        if name.startswith("_"):
            return "template or component file, not a page"
        if not name.endswith(".md"):
            return "not a markdown page"
        return ""

    def _build_doc(
        self, path: str, raw: str, attribution: str, fetched_at: datetime
    ) -> tuple[KnowledgeDoc | None, str]:
        try:
            meta, body = split_front_matter(raw)
        except (yaml.YAMLError, ValueError) as err:
            return None, f"front matter could not be read: {err}"

        text, removed = clean_page(body)
        if len(text) < MIN_BODY_CHARS:
            return None, f"only {len(text)} characters left after cleaning"
        if removed:
            logger.info("%s: removed %d promotional item(s)", path, removed)

        stem = path.rsplit("/", 1)[-1].removesuffix(".md")
        title = str(meta.get("title") or stem.replace("-", " ").title())
        content_type, role_family, level = classify_page(path)
        try:
            doc = KnowledgeDoc(
                source_id=SOURCE_ID,
                url=page_url(path),
                title=title,
                domain=DOMAIN,
                content_type=content_type,
                role_family=role_family,
                level=level,
                licence="MIT",
                attribution=attribution,
                fetched_at=fetched_at,
                expires_at=fetched_at + self.expiry,
                text=f"{title}\n\n{text}",
            )
        except ValidationError as err:
            first = err.errors()[0]
            return None, f"not a valid document: {first['loc']}: {first['msg']}"
        return doc, ""
