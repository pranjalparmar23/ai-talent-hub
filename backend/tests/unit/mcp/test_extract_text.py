"""Tests for text extraction in the MCP gateway (app/mcp/gateway.py::_extract_text).

The GitHub MCP server sends file contents as an embedded resource, not as plain text.
The gateway must keep that text, and must still ignore images and binary data.

The first group tests the function with hand-made results. The second group runs a
real MCP server (in memory, like tests/unit/mcp/test_gateway.py) that answers the way
the GitHub server does, and reads a file through the gateway and GatewayGitHubReader.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import (
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)

from app.ingest.sources.base import SourceError
from app.ingest.sources.readers import GatewayGitHubReader
from app.mcp.cache import TTLCache
from app.mcp.config import ServerConfig
from app.mcp.gateway import MCPGateway, ToolCallError, _extract_text
from tests.unit.mcp.fake_server import FakeServerKit


def text(value: str) -> TextContent:
    return TextContent(type="text", text=value)


def resource(value: str, name: str = "README.md") -> EmbeddedResource:
    return EmbeddedResource(
        type="resource",
        resource=TextResourceContents(
            uri=f"repo://owner/repo/contents/{name}",
            mimeType="text/markdown",
            text=value,
        ),
    )


def blob(name: str = "logo.png") -> EmbeddedResource:
    return EmbeddedResource(
        type="resource",
        resource=BlobResourceContents(
            uri=f"repo://owner/repo/contents/{name}",
            mimeType="image/png",
            blob="aGVsbG8=",
        ),
    )


def image() -> ImageContent:
    return ImageContent(type="image", data="aGVsbG8=", mimeType="image/png")


def result(*content, **kwargs) -> CallToolResult:
    return CallToolResult(content=list(content), **kwargs)


# ── _extract_text ────────────────────────────────────────────


def test_plain_text_is_returned_as_before():
    assert _extract_text(result(text("hello"))) == "hello"


def test_text_inside_an_embedded_resource_is_returned():
    assert _extract_text(result(resource("# Title\n\nBody"))) == "# Title\n\nBody"


def test_a_status_line_and_a_resource_are_joined_in_order():
    status = text("successfully downloaded text file (SHA: 1a2b3c)")
    extracted = _extract_text(result(status, resource("# Title")))
    assert extracted == "successfully downloaded text file (SHA: 1a2b3c)\n# Title"


def test_several_items_keep_their_order():
    extracted = _extract_text(result(text("one"), resource("two"), text("three")))
    assert extracted == "one\ntwo\nthree"


def test_binary_resources_are_ignored():
    assert _extract_text(result(text("note"), blob())) == "note"


def test_images_are_ignored():
    assert _extract_text(result(text("note"), image())) == "note"


def test_a_result_with_only_binary_content_gives_empty_text():
    assert _extract_text(result(blob(), image())) == ""


def test_an_empty_resource_adds_nothing():
    assert _extract_text(result(text("note"), resource(""))) == "note"


def test_surrounding_blank_space_is_trimmed():
    assert _extract_text(result(resource("\n\n  body  \n\n"))) == "body"


def test_structured_content_is_the_fallback_when_there_is_no_text():
    extracted = _extract_text(result(structuredContent={"a": 1}))
    assert json.loads(extracted) == {"a": 1}


def test_structured_content_is_not_used_when_text_exists():
    extracted = _extract_text(
        result(resource("from resource"), structuredContent={"a": 1})
    )
    assert extracted == "from resource"


def test_an_error_result_raises_with_the_text_of_a_resource():
    with pytest.raises(ToolCallError, match="file not found"):
        _extract_text(result(resource("file not found"), isError=True))


def test_an_error_result_with_no_text_still_raises():
    with pytest.raises(ToolCallError, match="tool reported an error"):
        _extract_text(result(isError=True))


# ── through the real gateway ─────────────────────────────────

README = "# Handbook\n\n" + "A line of the page.\n" * 20
STATUS = "successfully downloaded text file (SHA: 1a2b3c)"
FOLDER = [
    {"type": "file", "path": "contents/resume.md"},
    {"type": "dir", "path": "contents/algorithms"},
    {"type": "file", "path": "contents/negotiation.md"},
]


def build_github_like_server() -> FastMCP:
    """Answers get_file_contents the way the GitHub MCP server does."""
    server = FastMCP("fake-github")

    @server.tool()
    def get_file_contents(owner: str, repo: str, path: str):
        if path == "contents":
            return [text(json.dumps(FOLDER))]
        if path == "missing.md":
            raise ValueError("path not found")
        return [text(STATUS), resource(README, path)]

    return server


def github_gateway(**config) -> MCPGateway:
    cfg = ServerConfig(
        name="github",
        transport="http",
        url="https://github-mcp.example.com/mcp",
        allowed_tools=frozenset({"get_file_contents"}),
        timeout_seconds=2.0,
        max_retries=0,
        cache_ttl_seconds=0,
        max_result_chars=config.pop("max_result_chars", 100_000),
        allowed_url_domains=frozenset({"example.com"}),
    )
    kit = FakeServerKit()
    kit.server = build_github_like_server()
    return MCPGateway(
        {"github": cfg},
        session_factory=kit.factory,
        cache=TTLCache(),
        url_resolver=lambda host: ["93.184.216.34"],
        environ={},
    )


async def test_the_gateway_returns_the_text_of_an_embedded_resource():
    gateway = github_gateway()
    args = {"owner": "o", "repo": "r", "path": "README.md"}
    got = await gateway.call_tool("github", "get_file_contents", args)
    assert got.ok
    assert got.text.startswith(STATUS)
    assert "A line of the page." in got.text


async def test_the_reader_reads_a_file_through_the_gateway():
    reader = GatewayGitHubReader("o", "r", gateway=github_gateway())
    assert await reader.read_file("README.md") == README.strip()


async def test_the_reader_lists_a_folder_through_the_gateway():
    reader = GatewayGitHubReader("o", "r", gateway=github_gateway())
    assert await reader.list_files("contents") == [
        "contents/negotiation.md",
        "contents/resume.md",
    ]


async def test_the_reader_refuses_a_file_the_gateway_cut_short():
    reader = GatewayGitHubReader("o", "r", gateway=github_gateway(max_result_chars=100))
    with pytest.raises(SourceError, match="max_result_chars"):
        await reader.read_file("README.md")


async def test_the_reader_reports_a_server_side_failure():
    reader = GatewayGitHubReader("o", "r", gateway=github_gateway())
    with pytest.raises(SourceError, match="README|missing.md"):
        await reader.read_file("missing.md")
