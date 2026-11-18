"""The shared shape of a source adapter (Phase 7, task 6).

An adapter knows ONE source: where its pages are, which ones to skip, how to clean
them, and which licence and credit line apply. It returns KnowledgeDoc records. It
never writes to ChromaDB: the pipeline (app/ingest/pipeline.py) does that.

Adapters read files through a SourceReader, so the same adapter works with the
GitHub MCP server in production and with an in-memory fake in tests.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

from app.ingest.schema import KnowledgeDoc

# How long ingested content is trusted before it must be fetched again.
# From docs/architecture/knowledge-layer.md: about 90 days for static repositories.
DEFAULT_EXPIRY = timedelta(days=90)


class SourceError(Exception):
    """The whole source cannot be used right now. The message is plain words.

    Examples: the server is down, or the licence file is not what we expected.
    A single bad page is not an error: it goes into SourceBatch.skipped.
    """


class SourceReader(Protocol):
    """Reads files from a repository, one folder at a time."""

    async def list_files(self, directory: str) -> list[str]:
        """Paths of the files directly inside `directory` (no sub-folders)."""
        ...

    async def read_file(self, path: str) -> str:
        """The full text of one file. Raises SourceError if it is not read in full."""
        ...


@dataclass(frozen=True)
class Skipped:
    path: str
    reason: str


@dataclass
class SourceBatch:
    """What one adapter run produced: the records, and what was left out and why."""

    source_id: str
    docs: list[KnowledgeDoc] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)


class SourceAdapter(Protocol):
    source_id: str

    async def fetch(self) -> SourceBatch: ...
