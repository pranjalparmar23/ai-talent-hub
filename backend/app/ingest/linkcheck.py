"""Link-health check (Phase 7, task 12).

A learning plan with a dead link loses the reader's trust (risk R7). Before a link is
stored in `learning_knowledge`, this module asks the site whether the page is still
there.

What it does for each address:
  1. Check it with the gateway's URL guard (app/mcp/security.py): http(s) only, no
     credentials, the host must be one of the hosts in the list being checked, and
     every IP it resolves to must be public.
  2. Send a HEAD request. Some sites refuse HEAD (400, 403, 405, 501), and some answer
     it with 404 even though the page is there (support.google.com does). So when HEAD
     does not give a 2xx or a redirect, it sends a GET for the first byte only, reads
     nothing else, and the GET answer is the one that counts.
  3. Follow up to 3 redirects by hand. Each step goes through the same URL guard, so a
     redirect cannot lead to a private address or to a site that is not in the list.

What the answer means:

  | Result  | When                                            | What ingest does |
  |---------|-------------------------------------------------|------------------|
  | ok      | the final answer is 2xx                         | keeps the link   |
  | broken  | the final answer is 404 or 410 (HEAD and GET)   | drops the link   |
  | unknown | anything else: timeout, 403, 429, 5xx, a        | keeps the link   |
  |         | redirect to another site, no network, ...       |                  |

A link is dropped only when the site clearly says the page is gone. A site that blocks
bots, is down for a moment, or cannot be reached from this machine is "unknown", so a
bad day on the network never deletes good links.

What it does not do:
  - It does not notice a "soft 404" (a normal page that says "not found").
  - It does not check that the page still says what our description says.
  - The URL guard resolves the name once and the HTTP client resolves it again, so a
    host that changes its answer in between (DNS rebinding) is not caught. The
    addresses come from our own reviewed YAML files, never from a user, so this is a
    small risk. The guard still stops private addresses in the normal case.

It sends only the address of a page to that page's own site. No user data, no cookies.
It never raises: every problem becomes an "unknown" result with a plain-words reason.
"""

import asyncio
import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urljoin, urlparse

import httpx

from app.mcp.security import (
    Resolver,
    UrlNotAllowedError,
    default_resolver,
    validate_url,
)

logger = logging.getLogger(__name__)

USER_AGENT = (
    "AITalentHub-LinkCheck/1.0 (+https://github.com/pranjalparmar23/ai-talent-hub)"
)
DEFAULT_TIMEOUT = 10.0  # seconds, for one request
DEFAULT_CONCURRENCY = 5  # requests in flight at once, across all hosts
DEFAULT_MAX_REDIRECTS = 3
DEFAULT_HOST_DELAY = 0.25  # seconds between two requests to the same host

_REDIRECTS = {301, 302, 303, 307, 308}
_GONE = {404, 410}
# When HEAD says one of these, ask again with a one-byte GET before believing it. A
# 404 or 410 from HEAD alone is not enough to drop a link: some sites do not serve HEAD.
_HEAD_REFUSED = {400, 403, 405, 501}
_HEAD_UNSURE = _HEAD_REFUSED | _GONE


class LinkStatus(str, Enum):
    OK = "ok"
    BROKEN = "broken"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LinkResult:
    url: str
    status: LinkStatus
    reason: str = ""  # plain words, empty when ok
    http_status: int | None = None  # the last status code seen, if any
    final_url: str = ""  # where redirects ended, only when different from `url`

    @property
    def keep(self) -> bool:
        """False only when the site clearly says the page is gone."""
        return self.status is not LinkStatus.BROKEN


def count_by_status(results: Iterable[LinkResult]) -> dict[str, int]:
    """How many results of each status, with all three keys always present."""
    counts = Counter(r.status.value for r in results)
    return {s.value: counts.get(s.value, 0) for s in LinkStatus}


async def check_links(
    urls: Iterable[str],
    *,
    client: httpx.AsyncClient | None = None,
    resolver: Resolver = default_resolver,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    host_delay: float = DEFAULT_HOST_DELAY,
) -> dict[str, LinkResult]:
    """Check every distinct address once. The result is keyed by the address given,
    in the order first seen.

    `client` and `resolver` can be replaced in tests, so no network is needed.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    unique = list(dict.fromkeys(u for u in urls if isinstance(u, str)))
    if not unique:
        return {}

    allowed = _allowed_hosts(unique)
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
        )

    slots = asyncio.Semaphore(concurrency)
    host_locks: dict[str, asyncio.Lock] = {}

    async def check_one(url: str) -> LinkResult:
        host = (urlparse(url).hostname or "").lower()
        lock = host_locks.setdefault(host, asyncio.Lock())
        # Take the host lock first: tasks that only wait for a busy host must not
        # use up the request slots that other hosts could use.
        async with lock:
            async with slots:
                try:
                    result = await _probe(client, url, allowed, resolver, max_redirects)
                except Exception as err:  # never raise: see the module docstring
                    logger.warning("link check failed unexpectedly: %s", err)
                    result = LinkResult(
                        url,
                        LinkStatus.UNKNOWN,
                        f"unexpected error: {type(err).__name__}",
                    )
            if host_delay:
                await asyncio.sleep(host_delay)
        return result

    try:
        results = await asyncio.gather(*(check_one(u) for u in unique))
    finally:
        if owns_client:
            await client.aclose()

    counts = count_by_status(results)
    logger.info(
        "link check: %d links, %d ok, %d broken, %d unknown",
        len(results),
        counts["ok"],
        counts["broken"],
        counts["unknown"],
    )
    return dict(zip(unique, results))


# ── One address ────────────────────────────────────────────────────────────


async def _probe(
    client: httpx.AsyncClient,
    url: str,
    allowed: frozenset[str],
    resolver: Resolver,
    max_redirects: int,
) -> LinkResult:
    current = url
    code: int | None = None

    for _ in range(max_redirects + 1):
        try:
            await validate_url(current, allowed, resolver)
        except UrlNotAllowedError as err:
            label = "address" if current == url else "redirect"
            return LinkResult(
                url,
                LinkStatus.UNKNOWN,
                f"{label} not checked: {err}",
                http_status=code,
                final_url=_changed(url, current),
            )

        try:
            response_code, location = await _ask(client, current)
        except httpx.TimeoutException:
            return LinkResult(url, LinkStatus.UNKNOWN, "timed out", code)
        except httpx.HTTPError as err:
            reason = f"request failed ({type(err).__name__})"
            return LinkResult(url, LinkStatus.UNKNOWN, reason, code)
        code = response_code

        if code in _REDIRECTS:
            if not location:
                return LinkResult(
                    url, LinkStatus.UNKNOWN, "redirect with no address", code
                )
            current = urljoin(current, location)
            continue

        final = _changed(url, current)
        if 200 <= code < 300:
            return LinkResult(url, LinkStatus.OK, "", code, final)
        if code in _GONE:
            return LinkResult(url, LinkStatus.BROKEN, f"HTTP {code}", code, final)
        return LinkResult(url, LinkStatus.UNKNOWN, f"HTTP {code}", code, final)

    return LinkResult(url, LinkStatus.UNKNOWN, "too many redirects", code)


async def _ask(client: httpx.AsyncClient, url: str) -> tuple[int, str]:
    """One request. Returns (status code, Location header). HEAD first, then a
    one-byte GET if HEAD was refused or said the page is gone. The body is never read.
    """
    response = await client.head(url)
    if response.status_code in _HEAD_UNSURE:
        async with client.stream("GET", url, headers={"Range": "bytes=0-0"}) as got:
            return got.status_code, got.headers.get("location", "")
    return response.status_code, response.headers.get("location", "")


# ── Helpers ────────────────────────────────────────────────────────────────


def _allowed_hosts(urls: Iterable[str]) -> frozenset[str]:
    """The hosts a redirect may lead to: the hosts in the list. A `www.` host also
    allows its bare name, because sites often redirect between the two."""
    hosts: set[str] = set()
    for url in urls:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        if not host:
            continue
        hosts.add(host)
        if host.startswith("www."):
            hosts.add(host[4:])
    return frozenset(hosts)


def _changed(original: str, current: str) -> str:
    return current if current != original else ""
