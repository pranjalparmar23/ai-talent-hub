"""Adapter for link-only sources (Phase 7, task 6).

Some sources may be linked to but not copied: their licence is restrictive or
unknown (roadmap.sh, system-design-primer, freeCodeCamp: docs/mcp/vetting-log.md).
For these we store ONLY a link, a title and a short description that we wrote
ourselves. Nothing from the source's own pages is fetched or stored.

The list lives in a small YAML file next to the code, for example
`data/roadmap_sh.yaml`:

    source_id: roadmap-sh
    licence: proprietary
    attribution: "roadmap.sh by Kamran Ahmed. Link only: no content is copied."
    content_type: roadmap
    entries:
      - url: https://roadmap.sh/backend
        title: "Backend Developer roadmap"
        domain: software-engineering
        role_family: technical
        level: all            # optional, default "all"
        summary: "One plain sentence, in our own words."

An entry that is not a valid document is skipped with the reason, and the rest
still load.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.ingest.schema import KnowledgeDoc
from app.ingest.sources.base import DEFAULT_EXPIRY, Skipped, SourceBatch, SourceError

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
ROADMAP_SH_FILE = DATA_DIR / "roadmap_sh.yaml"

_REQUIRED_TOP = ("source_id", "licence", "attribution", "content_type", "entries")
_REQUIRED_ENTRY = ("url", "title", "domain", "role_family", "summary")


def load_link_list(path: Path) -> dict[str, Any]:
    """Read and check the top level of a link list. Raises SourceError."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as err:
        raise SourceError(f"cannot read link list {path.name}: {err}") from err
    if not isinstance(data, dict):
        raise SourceError(f"{path.name}: expected a mapping at the top level")
    missing = [k for k in _REQUIRED_TOP if not data.get(k)]
    if missing:
        raise SourceError(f"{path.name}: missing {', '.join(missing)}")
    if not isinstance(data["entries"], list):
        raise SourceError(f"{path.name}: 'entries' must be a list")
    return data


class LinkOnlyAdapter:
    def __init__(
        self,
        path: Path = ROADMAP_SH_FILE,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        expiry: timedelta = DEFAULT_EXPIRY,
    ) -> None:
        self.path = Path(path)
        self.clock = clock
        self.expiry = expiry
        self._data = load_link_list(self.path)
        self.source_id = str(self._data["source_id"])

    async def fetch(self) -> SourceBatch:
        data = self._data
        fetched_at = self.clock()
        batch = SourceBatch(source_id=self.source_id)
        seen: set[str] = set()

        for position, entry in enumerate(data["entries"], start=1):
            if not isinstance(entry, dict):
                batch.skipped.append(Skipped(f"entry {position}", "not a mapping"))
                continue
            label = entry.get("url") or f"entry {position}"
            missing = [k for k in _REQUIRED_ENTRY if not entry.get(k)]
            if missing:
                batch.skipped.append(Skipped(label, f"missing {', '.join(missing)}"))
                continue
            if entry["url"] in seen:
                batch.skipped.append(Skipped(label, "duplicate address"))
                continue
            seen.add(entry["url"])

            title = str(entry["title"]).strip()
            try:
                doc = KnowledgeDoc(
                    source_id=self.source_id,
                    url=entry["url"],
                    title=title,
                    domain=entry["domain"],
                    content_type=entry.get("content_type", data["content_type"]),
                    role_family=entry["role_family"],
                    level=entry.get("level", "all"),
                    ingest_mode="link_only",
                    licence=data["licence"],
                    attribution=data["attribution"],
                    fetched_at=fetched_at,
                    expires_at=fetched_at + self.expiry,
                    text=f"{title}. {str(entry['summary']).strip()}",
                )
            except ValidationError as err:
                first = err.errors()[0]
                batch.skipped.append(
                    Skipped(
                        label, f"not a valid document: {first['loc']}: {first['msg']}"
                    )
                )
                continue
            batch.docs.append(doc)

        if not batch.docs:
            raise SourceError(f"{self.path.name}: no valid entries")
        logger.info(
            "%s: %d documents, %d entries skipped",
            self.source_id,
            len(batch.docs),
            len(batch.skipped),
        )
        return batch
