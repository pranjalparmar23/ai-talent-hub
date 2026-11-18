"""Tests for GatewayGitHubReader (app/ingest/sources/readers.py).

The reader is tested with a fake gateway that returns scripted ToolResult objects,
so no MCP server, network or token is needed. The test in
tests/unit/mcp/test_extract_text.py also runs the reader through the real gateway.
"""

import json

import pytest

from app.ingest.sources.base import SourceError
from app.ingest.sources.readers import GatewayGitHubReader
from app.mcp.gateway import ToolResult


class FakeGateway:
    """Returns the scripted results one by one and records every call."""

    def __init__(self, *results: ToolResult):
        self.results = list(results)
        self.calls: list[tuple[str, str, dict]] = []

    async def call_tool(self, server, tool, arguments=None):
        self.calls.append((server, tool, arguments))
        return self.results.pop(0)


def live(text: str, **over) -> ToolResult:
    base = dict(ok=True, server="github", tool="get_file_contents", source="live")
    base.update(over, text=text)
    return ToolResult(**base)


def failed(code: str, message: str) -> ToolResult:
    return ToolResult(
        ok=False,
        server="github",
        tool="get_file_contents",
        error_code=code,
        error=message,
    )


def listing(*entries: dict) -> str:
    return json.dumps(list(entries))


def reader(gateway, **kwargs) -> GatewayGitHubReader:
    return GatewayGitHubReader(
        "yangshun", "tech-interview-handbook", gateway=gateway, **kwargs
    )


# ── what is sent to the server ───────────────────────────────


async def test_read_file_sends_owner_repo_and_path():
    gateway = FakeGateway(live("hello"))
    await reader(gateway).read_file("apps/website/contents/resume.md")
    server, tool, arguments = gateway.calls[0]
    assert (server, tool) == ("github", "get_file_contents")
    assert arguments == {
        "owner": "yangshun",
        "repo": "tech-interview-handbook",
        "path": "apps/website/contents/resume.md",
    }


async def test_leading_and_trailing_slashes_are_removed_from_the_path():
    gateway = FakeGateway(live("hello"))
    await reader(gateway).read_file("/LICENSE/")
    assert gateway.calls[0][2]["path"] == "LICENSE"


async def test_ref_is_sent_only_when_given():
    without = FakeGateway(live("a"))
    await reader(without).read_file("LICENSE")
    assert "ref" not in without.calls[0][2]

    with_ref = FakeGateway(live("a"))
    await reader(with_ref, ref="e1d28e8").read_file("LICENSE")
    assert with_ref.calls[0][2]["ref"] == "e1d28e8"


async def test_a_custom_server_name_is_used():
    gateway = FakeGateway(live("a"))
    await reader(gateway, server="github-mirror").read_file("LICENSE")
    assert gateway.calls[0][0] == "github-mirror"


# ── read_file: the text ──────────────────────────────────────


async def test_status_line_in_front_of_the_file_is_removed():
    text = "successfully downloaded text file (SHA: 1a2b3c)\n# Title\n\nBody"
    assert await reader(FakeGateway(live(text))).read_file("a.md") == "# Title\n\nBody"


async def test_status_line_is_matched_without_regard_to_capitals():
    text = "Successfully Downloaded Text File\nBody"
    assert await reader(FakeGateway(live(text))).read_file("a.md") == "Body"


async def test_text_without_a_status_line_is_returned_as_it_is():
    assert await reader(FakeGateway(live("# Title\n\nBody"))).read_file("a.md") == (
        "# Title\n\nBody"
    )


async def test_the_status_phrase_inside_the_file_is_kept():
    text = "# Notes\n\nsuccessfully downloaded text file is a phrase in this page."
    assert await reader(FakeGateway(live(text))).read_file("a.md") == text


async def test_only_one_status_line_is_removed():
    text = "successfully downloaded text file\nsuccessfully downloaded text file\nBody"
    result = await reader(FakeGateway(live(text))).read_file("a.md")
    assert result == "successfully downloaded text file\nBody"


async def test_a_reply_with_only_a_status_line_is_an_error():
    gateway = FakeGateway(live("successfully downloaded text file (SHA: 1a2b3c)"))
    with pytest.raises(SourceError, match="no file text"):
        await reader(gateway).read_file("empty.md")


async def test_an_empty_reply_is_an_error():
    with pytest.raises(SourceError, match="no file text"):
        await reader(FakeGateway(live("   \n"))).read_file("empty.md")


# ── refusing results we must not store ───────────────────────


async def test_a_failed_call_is_an_error_with_the_code_and_the_path():
    gateway = FakeGateway(failed("unavailable", "connection refused"))
    with pytest.raises(SourceError) as caught:
        await reader(gateway).read_file("LICENSE")
    message = str(caught.value)
    assert "yangshun/tech-interview-handbook/LICENSE" in message
    assert "unavailable" in message and "connection refused" in message


async def test_old_cached_data_is_refused():
    gateway = FakeGateway(live("old text", source="stale_cache"))
    with pytest.raises(SourceError, match="old cached data"):
        await reader(gateway).read_file("LICENSE")


async def test_fresh_cache_is_accepted():
    gateway = FakeGateway(live("cached text", source="cache"))
    assert await reader(gateway).read_file("LICENSE") == "cached text"


async def test_a_cut_short_result_is_refused_and_names_the_setting():
    gateway = FakeGateway(live("half a page", truncated=True))
    with pytest.raises(SourceError) as caught:
        await reader(gateway).read_file("big.md")
    assert "max_result_chars" in str(caught.value)
    assert "mcp_servers.yaml" in str(caught.value)


async def test_a_cut_short_folder_listing_is_refused_too():
    gateway = FakeGateway(live('[{"type": "file", "path": "a.md"', truncated=True))
    with pytest.raises(SourceError, match="max_result_chars"):
        await reader(gateway).list_files("docs")


# ── list_files ───────────────────────────────────────────────


async def test_list_files_returns_sorted_paths_of_files_only():
    gateway = FakeGateway(
        live(
            listing(
                {"type": "file", "path": "docs/b.md"},
                {"type": "dir", "path": "docs/algorithms"},
                {"type": "file", "path": "docs/a.md"},
            )
        )
    )
    assert await reader(gateway).list_files("docs") == ["docs/a.md", "docs/b.md"]


async def test_list_files_sends_the_folder_as_the_path():
    gateway = FakeGateway(live("[]"))
    await reader(gateway).list_files("apps/website/contents/")
    assert gateway.calls[0][2]["path"] == "apps/website/contents"


async def test_list_files_ignores_entries_it_cannot_use():
    gateway = FakeGateway(
        live(
            listing(
                {"type": "file"},
                {"type": "file", "path": ""},
                {"path": "docs/no-type.md"},
                "just a string",
                {"type": "file", "path": "docs/ok.md"},
            )
        )
    )
    assert await reader(gateway).list_files("docs") == ["docs/ok.md"]


async def test_an_empty_folder_gives_an_empty_list():
    assert await reader(FakeGateway(live("[]"))).list_files("docs") == []


async def test_a_listing_that_is_not_json_is_an_error():
    with pytest.raises(SourceError, match="not JSON"):
        await reader(FakeGateway(live("# a markdown file"))).list_files("docs")


async def test_a_single_file_instead_of_a_folder_is_an_error():
    single = json.dumps({"type": "file", "path": "docs/a.md"})
    with pytest.raises(SourceError, match="not a folder"):
        await reader(FakeGateway(live(single))).list_files("docs/a.md")


async def test_list_files_passes_on_gateway_failures():
    gateway = FakeGateway(failed("timeout", "took too long"))
    with pytest.raises(SourceError, match="timeout"):
        await reader(gateway).list_files("docs")
