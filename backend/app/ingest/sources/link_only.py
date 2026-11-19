"""Adapter for link-only sources (Phase 7, tasks 6 and 12).

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

Addresses are tidied with `normalise_url()` before anything else, so the same page
written two ways is one page (task 12). The stored address is the tidied one. A page
that is already listed, in this file or by another source in the same run, is
skipped with the reason.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from pydantic import ValidationError

from app.ingest.schema import KnowledgeDoc
from app.ingest.sources.base import DEFAULT_EXPIRY, Skipped, SourceBatch, SourceError

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
ROADMAP_SH_FILE = DATA_DIR / "roadmap_sh.yaml"

_REQUIRED_TOP = ("source_id", "licence", "attribution", "content_type", "entries")
_REQUIRED_ENTRY = ("url", "title", "domain", "role_family", "summary")

# Query parameters that only say where a visitor came from. They never change which
# page is shown, so they are removed. Anything else (v=, list=, page=, ...) stays.
_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref",
        "ref_src",
        "si",  # YouTube share links
        "feature",  # YouTube share links
    }
)
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalise_url(url: str) -> str:
    """Write an address in one standard way, so one page has one address.

    - `http` becomes `https` (unless it names a port other than 80)
    - the host is lower-case, without a trailing dot or a default port
    - the fragment (`#section`) is removed
    - tracking parameters (`utm_*`, `fbclid`, `gclid`, `ref`, ...) are removed and the
      rest are sorted
    - a trailing slash is removed, except for the site root (`https://host/`)

    The path keeps its case (paths are case-sensitive), `www.` is kept (a site may not
    answer without it), and nothing is looked up on the network.

    Anything that is not a usable http(s) address with a host is returned stripped but
    otherwise unchanged. Checking it is the job of the document schema.
    """
    text = url.strip()
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:  # a bad port or a broken bracketed host
        return text
    if parts.scheme.lower() not in ("http", "https") or not host:
        return text

    scheme = parts.scheme.lower()
    if scheme == "http" and port in (None, 80):
        # Upgrade to https. An http address on another port is a different service,
        # so it keeps its scheme.
        scheme, port = "https", None
    if port == _DEFAULT_PORTS[scheme]:
        port = None

    netloc = f"[{host}]" if ":" in host else host  # an IPv6 address needs brackets
    if (
        parts.username is not None
    ):  # keep credentials as written: the guards reject them
        login = parts.username + (f":{parts.password}" if parts.password else "")
        netloc = f"{login}@{netloc}"
    if port is not None:
        netloc = f"{netloc}:{port}"

    path = parts.path.rstrip("/") or "/"
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    query = urlencode(sorted(kept))
    return urlunsplit((scheme, netloc, path, query, ""))


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
    """Reads one YAML link list.

    `seen_urls` lets several adapters in the same run share one set of addresses. The
    first adapter to list a page keeps it, and later ones skip it. The run passes the
    same set to every link-only adapter it builds.
    """

    def __init__(
        self,
        path: Path = ROADMAP_SH_FILE,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        expiry: timedelta = DEFAULT_EXPIRY,
        seen_urls: set[str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.clock = clock
        self.expiry = expiry
        self.seen_urls: set[str] = seen_urls if seen_urls is not None else set()
        self._claimed: set[str] = set()  # the addresses this adapter put in seen_urls
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
            if not isinstance(entry["url"], str):
                batch.skipped.append(Skipped(label, "url is not text"))
                continue

            url = normalise_url(entry["url"])
            if url in seen:
                batch.skipped.append(Skipped(label, "duplicate address"))
                continue
            if url in self.seen_urls and url not in self._claimed:
                batch.skipped.append(
                    Skipped(label, "duplicate address: listed by another source")
                )
                continue
            seen.add(url)

            title = str(entry["title"]).strip()
            try:
                doc = KnowledgeDoc(
                    source_id=self.source_id,
                    url=url,
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
            self.seen_urls.add(url)
            self._claimed.add(url)

        if not batch.docs:
            raise SourceError(f"{self.path.name}: no valid entries")
        logger.info(
            "%s: %d documents, %d entries skipped",
            self.source_id,
            len(batch.docs),
            len(batch.skipped),
        )
        return batch
