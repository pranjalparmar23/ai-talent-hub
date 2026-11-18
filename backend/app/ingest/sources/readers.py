"""Readers: fetch repository files for source adapters (Phase 7, task 6).

GatewayGitHubReader reads through the MCPGateway and the GitHub MCP server, so it
gets the gateway's allowlist, rate limit, circuit breaker and logging.

For ingestion a wrong answer is worse than no answer, so this reader refuses:
- a result the gateway cut short (a half page must never be stored as a whole one),
- old cached data served because the server was failing (it would get today's date),
- a reply with no file text in it.
"""

import json
import re
from typing import Any

from app.ingest.sources.base import SourceError

GITHUB_SERVER = "github"  # the name in mcp_servers.yaml
GET_FILE_TOOL = "get_file_contents"

# The GitHub MCP server puts a one-line status in front of the file text.
_STATUS_LINE = re.compile(r"^successfully downloaded text file[^\n]*\n?", re.IGNORECASE)


class GatewayGitHubReader:
    """Reads files and folders of one public GitHub repository."""

    def __init__(
        self,
        owner: str,
        repo: str,
        *,
        ref: str | None = None,
        gateway: Any = None,
        server: str = GITHUB_SERVER,
    ) -> None:
        if gateway is None:
            from app.mcp.gateway import get_gateway

            gateway = get_gateway()
        self.owner = owner
        self.repo = repo
        self.ref = ref  # a branch, tag or commit; None means the default branch
        self.gateway = gateway
        self.server = server

    async def list_files(self, directory: str) -> list[str]:
        text = await self._fetch(directory)
        try:
            entries = json.loads(text)
        except json.JSONDecodeError as err:
            raise SourceError(
                f"{self._where(directory)}: the folder listing is not JSON ({err})"
            ) from err
        if not isinstance(entries, list):
            raise SourceError(f"{self._where(directory)}: this is not a folder")
        return sorted(
            e["path"]
            for e in entries
            if isinstance(e, dict) and e.get("type") == "file" and e.get("path")
        )

    async def read_file(self, path: str) -> str:
        text = _STATUS_LINE.sub("", await self._fetch(path), count=1).strip()
        if not text:
            raise SourceError(f"{self._where(path)}: the server returned no file text")
        return text

    async def _fetch(self, path: str) -> str:
        arguments = {"owner": self.owner, "repo": self.repo, "path": path.strip("/")}
        if self.ref:
            arguments["ref"] = self.ref
        result = await self.gateway.call_tool(self.server, GET_FILE_TOOL, arguments)
        where = self._where(path)
        if not result.ok:
            raise SourceError(f"{where}: {result.error_code}: {result.error}")
        if result.source == "stale_cache":
            raise SourceError(f"{where}: only old cached data was available")
        if result.truncated:
            raise SourceError(
                f"{where}: longer than the gateway limit; raise max_result_chars "
                f"for '{self.server}' in mcp_servers.yaml"
            )
        return result.text

    def _where(self, path: str) -> str:
        return f"{self.owner}/{self.repo}/{path.strip('/')}"
