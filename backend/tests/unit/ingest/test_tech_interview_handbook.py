"""Tests for the Tech Interview Handbook adapter.

(app/ingest/sources/tech_interview_handbook.py)

The adapter is tested with an in-memory reader, so no GitHub, MCP server or token is
needed. The sample pages are small, made-up pages that have the same kinds of
problems as the real ones: front matter, imports, components, ads and links.
"""

from datetime import datetime, timedelta, timezone

import pytest
import yaml

from app.ingest.licence import require_licence
from app.ingest.sources.base import DEFAULT_EXPIRY, SourceError
from app.ingest.sources.tech_interview_handbook import (
    CONTENT_DIR,
    SITE,
    SKIP_PAGES,
    TechInterviewHandbookAdapter,
    classify_page,
    clean_page,
    is_promotional,
    page_url,
    read_attribution,
    split_front_matter,
)

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)

MIT_LICENCE = """\
MIT License

Copyright (c) 2017-Present Yangshun Tay

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.
"""

APACHE_LICENCE = """\
Apache License
Version 2.0, January 2004

Copyright (c) 2017-Present Yangshun Tay
"""

PROSE = (
    "Practice explaining your thinking out loud while you solve a problem. "
    "Interviewers want to hear how you reason, not only the final answer, so "
    "state your assumptions, test your idea on a small example and then write "
    "the code."
)


def page(title: str | None, body: str) -> str:
    head = f"---\nid: x\ntitle: {title}\n---\n\n" if title else ""
    return head + body


# ── an in-memory reader ──────────────────────────────────────


class MemoryReader:
    """Serves a dict of {path: text}. Paths in `broken` fail with a SourceError."""

    def __init__(self, files: dict[str, str], broken: set[str] | None = None):
        self.files = files
        self.broken = broken or set()
        self.read_paths: list[str] = []

    async def list_files(self, directory: str) -> list[str]:
        return sorted(p for p in self.files if p.rsplit("/", 1)[0] == directory)

    async def read_file(self, path: str) -> str:
        self.read_paths.append(path)
        if path in self.broken:
            raise SourceError(f"{path}: connection reset")
        return self.files[path]


def handbook(pages: dict[str, str], *, licence: str = MIT_LICENCE, **kw):
    files = {"LICENSE": licence}
    files.update({f"{CONTENT_DIR}/{name}": text for name, text in pages.items()})
    return MemoryReader(files, **kw)


def adapter(reader, **kwargs) -> TechInterviewHandbookAdapter:
    return TechInterviewHandbookAdapter(reader, clock=lambda: NOW, **kwargs)


# ── the front matter ─────────────────────────────────────────


def test_front_matter_is_split_from_the_body():
    meta, body = split_front_matter("---\nid: a\ntitle: Resume tips\n---\n\n# Hi\n")
    assert meta == {"id": "a", "title": "Resume tips"}
    assert body.strip() == "# Hi"


def test_a_page_without_front_matter_is_all_body():
    meta, body = split_front_matter("# Hi\n\nText")
    assert meta == {} and body == "# Hi\n\nText"


def test_windows_line_endings_are_handled():
    meta, body = split_front_matter("---\r\ntitle: A\r\n---\r\nText\r\n")
    assert meta == {"title": "A"} and body.strip() == "Text"


def test_empty_front_matter_gives_no_metadata():
    meta, body = split_front_matter("---\n\n---\nText")
    assert meta == {} and body == "Text"


def test_front_matter_that_is_not_a_mapping_is_an_error():
    with pytest.raises(ValueError, match="not a mapping"):
        split_front_matter("---\n- a\n- b\n---\nText")


def test_front_matter_with_broken_yaml_is_an_error():
    with pytest.raises(yaml.YAMLError):
        split_front_matter("---\ntitle: [unclosed\n---\nText")


# ── cleaning: page markup ────────────────────────────────────


def test_import_lines_are_removed():
    text, _ = clean_page("import Tabs from '@theme/Tabs';\n\nHello there.")
    assert text == "Hello there."


def test_self_closing_components_are_removed():
    text, _ = clean_page('Before.\n\n<Banner kind="x" />\n\nAfter.')
    assert text == "Before.\n\nAfter."


def test_html_tags_are_removed_and_their_text_is_kept():
    text, _ = clean_page("Use <strong>big</strong> words.<br/>Next line.")
    assert text == "Use big words.\nNext line."


def test_head_and_figure_blocks_are_removed_with_their_content():
    body = (
        "<head><title>Ignore me</title></head>\n\nReal text.\n\n"
        "<figure><img src='a.png'/><figcaption>Caption</figcaption></figure>"
    )
    text, _ = clean_page(body)
    assert text == "Real text."


def test_images_are_removed():
    text, _ = clean_page("Look: ![a diagram](./diagram.png) here.")
    assert "diagram" not in text and text == "Look:  here."


def test_admonition_markers_are_removed_but_their_text_stays():
    text, _ = clean_page(":::tip\nKeep this tip.\n:::")
    assert text == "Keep this tip."


def test_links_keep_their_text_and_lose_the_address():
    text, _ = clean_page("Read [the guide](https://example.com/guide) first.")
    assert text == "Read the guide first."


def test_a_link_whose_text_has_code_or_brackets_keeps_all_of_its_text():
    text, _ = clean_page(
        "See [`list[int]` docs](https://x.dev) and [a [b]](https://y.dev)."
    )
    assert text == "See `list[int]` docs and a [b]."


def test_many_blank_lines_become_one_blank_line():
    text, _ = clean_page("One.\n\n\n\n\nTwo.")
    assert text == "One.\n\nTwo."


# ── cleaning: code is never changed ──────────────────────────


CODE_BLOCK = (
    "```python\n"
    "import os\n"
    "\n"
    "def f(x: list[int]) -> str:\n"
    "    return '<div>[a](b)</div>'  # aff=1 shareasale.com\n"
    "```"
)


def test_a_code_block_is_kept_exactly():
    text, removed = clean_page(f"Example:\n\n{CODE_BLOCK}\n\nDone.")
    assert CODE_BLOCK in text
    assert removed == 0


def test_a_code_block_with_a_blank_line_is_not_split():
    text, _ = clean_page(f"Intro.\n\n{CODE_BLOCK}")
    assert "import os\n\ndef f" in text


def test_inline_code_is_kept_exactly():
    text, _ = clean_page("Call `<Foo />` or `[a](b)` and `import x` here.")
    assert text == "Call `<Foo />` or `[a](b)` and `import x` here."


def test_inline_code_that_looks_like_a_link_is_not_touched():
    text, _ = clean_page("Call `handlers[i](event)` and `table[0](x)` for each item.")
    assert text == "Call `handlers[i](event)` and `table[0](x)` for each item."


def test_a_real_link_next_to_inline_code_is_still_cleaned():
    text, _ = clean_page("Use `a[0](x)` and read [the docs](https://x.dev) now.")
    assert text == "Use `a[0](x)` and read the docs now."


# ── cleaning: promotional content ────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Buy it at https://shareasale.com/r.cfm?b=1",
        "https://example.com/?aff=42",
        "Our affiliate partner",
        "Use https://iio.sh/abc",
        "https://example.com/page?ref=xyz",
        "https://example.com/page?x=1&ref=xyz",
        "Get 20% off today",
        "Take 50% discount now",
        "JOIN TODAY and start",
        "One-time fee of $99",
        "Get lifetime access",
    ],
)
def test_sales_wording_is_detected(text):
    assert is_promotional(text)


@pytest.mark.parametrize(
    "text",
    [
        "Practise two problems every day.",
        "Binary search runs in O(log n) time.",
        "Reach 100% test coverage on the helper.",
        "A reference to the earlier chapter.",
    ],
)
def test_normal_sentences_are_not_promotional(text):
    assert not is_promotional(text)


def test_a_sales_paragraph_is_removed_and_counted():
    text, removed = clean_page(f"{PROSE}\n\nGet 30% off at https://x.dev/?aff=1 today.")
    assert text == PROSE
    assert removed == 1


def test_only_the_sales_item_of_a_list_is_removed():
    body = "- Read a book\n- Join today for lifetime access\n- Solve problems"
    text, removed = clean_page(body)
    assert text == "- Read a book\n- Solve problems"
    assert removed == 1


def test_only_the_sales_row_of_a_table_is_removed():
    body = (
        "| Course | Link |\n| --- | --- |\n| Free | [a](https://a.dev) |\n"
        "| Paid | [b](https://shareasale.com/x) |"
    )
    text, removed = clean_page(body)
    assert "Paid" not in text and "Free" in text and "Course" in text
    assert removed == 1


def test_a_list_made_only_of_sales_items_disappears():
    body = f"{PROSE}\n\n- Get 10% off\n- Join today"
    text, removed = clean_page(body)
    assert text == PROSE
    assert removed == 2


def test_a_heading_left_with_no_text_is_removed():
    body = f"# Guide\n\n{PROSE}\n\n## Courses\n\nGet 10% off these courses."
    text, removed = clean_page(body)
    assert text == f"# Guide\n\n{PROSE}"
    assert removed == 1


def test_a_heading_with_text_below_it_is_kept():
    text, removed = clean_page(f"## Tips\n\n{PROSE}")
    assert text.startswith("## Tips") and removed == 0


def test_a_sub_heading_with_text_keeps_its_parent_heading():
    text, _ = clean_page(f"## Tips\n\n### Pace\n\n{PROSE}")
    assert text == f"## Tips\n\n### Pace\n\n{PROSE}"


def test_a_page_of_only_sales_text_becomes_empty():
    text, removed = clean_page("# Courses\n\nJoin today!\n\nGet 20% off.")
    assert text == "" and removed == 2


# ── page type, audience, level, address ──────────────────────


@pytest.mark.parametrize(
    "path, expected",
    [
        (
            "behavioral-interview-questions.md",
            ("behavioral_question", "all", "all"),
        ),
        (
            "behavioral-interview-senior-candidates.md",
            ("concept_note", "all", "senior"),
        ),
        (
            "interview-formats-top-companies.md",
            ("company_pattern", "technical", "all"),
        ),
        ("best-practice-questions.md", ("interview_question", "technical", "all")),
        ("coding-interview-study-plan.md", ("roadmap", "technical", "all")),
        ("behavioral-interview-rubric.md", ("concept_note", "all", "all")),
        ("negotiation.md", ("concept_note", "all", "all")),
        ("resume.md", ("concept_note", "all", "all")),
        ("algorithms/tree.md", ("concept_note", "technical", "all")),
        ("coding-interview-prep.md", ("concept_note", "technical", "all")),
    ],
)
def test_pages_are_classified_from_their_file_name(path, expected):
    assert classify_page(f"{CONTENT_DIR}/{path}") == expected


def test_page_url_drops_the_folder_and_the_extension():
    assert page_url(f"{CONTENT_DIR}/resume.md") == f"{SITE}/resume/"


def test_page_url_keeps_a_sub_folder():
    assert page_url(f"{CONTENT_DIR}/algorithms/tree.md") == f"{SITE}/algorithms/tree/"


# ── the licence ──────────────────────────────────────────────


def test_the_credit_line_is_built_from_the_licence_file():
    assert read_attribution(MIT_LICENCE) == (
        "Tech Interview Handbook, Copyright (c) 2017-Present Yangshun Tay, MIT licence"
    )


def test_leading_blank_lines_in_the_licence_are_fine():
    assert read_attribution("\n\n" + MIT_LICENCE).endswith("MIT licence")


def test_a_licence_that_is_not_mit_is_refused():
    with pytest.raises(SourceError, match="not the MIT licence"):
        read_attribution(APACHE_LICENCE)


def test_an_mit_heading_without_the_grant_is_refused():
    text = "MIT License\n\nCopyright (c) 2017 A\n\nAll rights reserved."
    with pytest.raises(SourceError, match="not the MIT licence"):
        read_attribution(text)


def test_a_licence_without_a_copyright_line_is_refused():
    text = "MIT License\n\nPermission is hereby granted, free of charge."
    with pytest.raises(SourceError, match="no copyright line"):
        read_attribution(text)


async def test_a_changed_licence_stops_everything_before_any_page_is_read():
    reader = handbook({"resume.md": page("Resume", PROSE)}, licence=APACHE_LICENCE)
    with pytest.raises(SourceError, match="not the MIT licence"):
        await adapter(reader).fetch()
    assert reader.read_paths == ["LICENSE"]


async def test_a_licence_that_cannot_be_read_stops_everything():
    reader = handbook({"resume.md": page("Resume", PROSE)}, broken={"LICENSE"})
    with pytest.raises(SourceError, match="connection reset"):
        await adapter(reader).fetch()


# ── fetching: documents ──────────────────────────────────────


async def test_a_page_becomes_a_full_text_document():
    reader = handbook({"resume.md": page("Resume tips", PROSE)})
    batch = await adapter(reader).fetch()
    assert batch.source_id == "tech-interview-handbook"
    assert batch.skipped == []
    (doc,) = batch.docs
    assert doc.source_id == "tech-interview-handbook"
    assert doc.url == f"{SITE}/resume/"
    assert doc.title == "Resume tips"
    assert doc.domain == "software-engineering"
    assert doc.ingest_mode == "full_text"
    assert doc.licence == "MIT"
    assert doc.attribution.endswith("MIT licence")
    assert "Yangshun Tay" in doc.attribution
    assert (doc.content_type, doc.role_family, doc.level) == (
        "concept_note",
        "all",
        "all",
    )


async def test_the_text_starts_with_the_title_and_holds_the_cleaned_body():
    body = f"import X from 'y';\n\nRead [this](https://a.dev) first. {PROSE}"
    reader = handbook({"resume.md": page("Resume tips", body)})
    (doc,) = (await adapter(reader).fetch()).docs
    assert doc.text.startswith("Resume tips\n\n")
    assert "import X" not in doc.text and "https://a.dev" not in doc.text
    assert "Read this first." in doc.text


async def test_dates_come_from_the_clock_and_the_expiry():
    reader = handbook({"resume.md": page("Resume", PROSE)})
    (doc,) = (await adapter(reader).fetch()).docs
    assert doc.fetched_at == NOW and doc.expires_at == NOW + DEFAULT_EXPIRY

    (short,) = (await adapter(reader, expiry=timedelta(days=7)).fetch()).docs
    assert short.expires_at == NOW + timedelta(days=7)


async def test_the_title_falls_back_to_the_file_name():
    reader = handbook({"self-introduction.md": page(None, PROSE)})
    (doc,) = (await adapter(reader).fetch()).docs
    assert doc.title == "Self Introduction"


async def test_pages_in_the_algorithms_folder_are_read():
    reader = handbook({"resume.md": page("Resume", PROSE)})
    reader.files[f"{CONTENT_DIR}/algorithms/tree.md"] = page("Tree", PROSE)
    batch = await adapter(reader).fetch()
    urls = sorted(d.url for d in batch.docs)
    assert urls == [f"{SITE}/algorithms/tree/", f"{SITE}/resume/"]


async def test_each_page_gets_its_own_stable_document_id():
    pages = {"resume.md": page("Resume", PROSE), "negotiation.md": page("Pay", PROSE)}
    first = (await adapter(handbook(pages)).fetch()).docs
    second = (await adapter(handbook(pages)).fetch()).docs
    assert len({d.doc_id for d in first}) == 2
    assert sorted(d.doc_id for d in first) == sorted(d.doc_id for d in second)


async def test_documents_go_to_the_collection_for_their_type():
    pages = {
        "behavioral-interview-questions.md": page("Behavioural", PROSE),
        "coding-interview-study-plan.md": page("Study plan", PROSE),
    }
    batch = await adapter(handbook(pages)).fetch()
    by_title = {d.title: d.collection for d in batch.docs}
    assert by_title == {
        "Behavioural": "interview_knowledge",
        "Study plan": "learning_knowledge",
    }


async def test_every_document_passes_the_licence_gate():
    pages = {"resume.md": page("Resume", PROSE), "negotiation.md": page("Pay", PROSE)}
    for doc in (await adapter(handbook(pages)).fetch()).docs:
        assert require_licence(doc).licence == "MIT"


async def test_promotional_text_is_removed_from_the_stored_document():
    body = f"{PROSE}\n\nGet 40% off at https://shareasale.com/r.cfm?b=1\n\n{PROSE}"
    (doc,) = (await adapter(handbook({"resume.md": page("Resume", body)})).fetch()).docs
    assert "shareasale" not in doc.text and "40% off" not in doc.text


# ── fetching: pages that are left out ────────────────────────


@pytest.mark.parametrize("name", sorted(SKIP_PAGES))
async def test_pages_on_the_skip_list_are_not_read(name):
    reader = handbook({name: page("X", PROSE), "resume.md": page("Resume", PROSE)})
    batch = await adapter(reader).fetch()
    skipped = {s.path: s.reason for s in batch.skipped}
    assert skipped[f"{CONTENT_DIR}/{name}"] == SKIP_PAGES[name]
    assert f"{CONTENT_DIR}/{name}" not in reader.read_paths
    assert [d.title for d in batch.docs] == ["Resume"]


async def test_template_files_starting_with_an_underscore_are_skipped():
    reader = handbook(
        {"__template__.md": page("T", PROSE), "resume.md": page("Resume", PROSE)}
    )
    batch = await adapter(reader).fetch()
    assert "template" in batch.skipped[0].reason
    assert len(batch.docs) == 1


async def test_files_that_are_not_markdown_are_skipped():
    reader = handbook({"data.json": "{}", "resume.md": page("Resume", PROSE)})
    batch = await adapter(reader).fetch()
    assert batch.skipped[0].reason == "not a markdown page"
    assert len(batch.docs) == 1


async def test_a_page_that_is_too_short_after_cleaning_is_skipped():
    reader = handbook(
        {
            "short.md": page("Short", "Too short."),
            "ads.md": page("Ads", "Join today!\n\nGet 20% off."),
            "resume.md": page("Resume", PROSE),
        }
    )
    batch = await adapter(reader).fetch()
    reasons = {s.path.rsplit("/", 1)[-1]: s.reason for s in batch.skipped}
    assert "characters left after cleaning" in reasons["short.md"]
    assert "0 characters" in reasons["ads.md"]
    assert [d.title for d in batch.docs] == ["Resume"]


async def test_a_page_with_broken_front_matter_is_skipped_with_the_reason():
    broken = "---\ntitle: [unclosed\n---\n" + PROSE
    reader = handbook({"bad.md": broken, "resume.md": page("Resume", PROSE)})
    batch = await adapter(reader).fetch()
    assert "front matter could not be read" in batch.skipped[0].reason
    assert len(batch.docs) == 1


async def test_a_title_the_schema_rejects_gives_a_skipped_page_not_a_crash():
    reader = handbook(
        {
            "long.md": page("T" * 400, PROSE),
            "resume.md": page("Resume", PROSE),
        }
    )
    batch = await adapter(reader).fetch()
    assert "not a valid document" in batch.skipped[0].reason
    assert "title" in batch.skipped[0].reason
    assert len(batch.docs) == 1


async def test_a_page_that_cannot_be_read_is_skipped_and_the_rest_continue():
    bad = f"{CONTENT_DIR}/negotiation.md"
    reader = handbook(
        {"negotiation.md": page("Pay", PROSE), "resume.md": page("Resume", PROSE)},
        broken={bad},
    )
    batch = await adapter(reader).fetch()
    assert [d.title for d in batch.docs] == ["Resume"]
    assert batch.skipped[0].path == bad
    assert "could not be read" in batch.skipped[0].reason
    assert "connection reset" in batch.skipped[0].reason


# ── fetching: nothing usable ─────────────────────────────────


async def test_a_handbook_with_no_pages_is_an_error():
    with pytest.raises(SourceError, match="no page"):
        await adapter(handbook({})).fetch()


async def test_when_every_read_fails_the_first_error_is_in_the_message():
    reader = handbook(
        {"a.md": page("A", PROSE), "b.md": page("B", PROSE)},
        broken={f"{CONTENT_DIR}/a.md", f"{CONTENT_DIR}/b.md"},
    )
    with pytest.raises(SourceError) as caught:
        await adapter(reader).fetch()
    assert "a.md: connection reset" in str(caught.value)


async def test_when_every_page_is_skipped_the_message_says_so():
    reader = handbook({"mock-interviews.md": page("M", PROSE)})
    with pytest.raises(SourceError, match="all pages skipped"):
        await adapter(reader).fetch()
