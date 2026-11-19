"""Tests for the link-health check (app/ingest/linkcheck.py).

No network is used. Requests go to an httpx MockTransport, and names are resolved by a
fake resolver, so every answer a site can give is made up here.
"""

import asyncio

import httpx
import pytest

from app.ingest import linkcheck
from app.ingest.linkcheck import (
    LinkResult,
    LinkStatus,
    check_links,
    count_by_status,
)

HOST = "https://docs.example"


def public(host: str) -> list[str]:
    return ["8.8.8.8"]


def by_path(table: dict[str, int | Exception], seen: list | None = None):
    """A handler that answers by URL path. A value can be a status code or an
    exception to raise. `seen` collects (method, path, headers) of every request."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((request.method, request.url.path, request.headers))
        answer = table.get(request.url.path, 200)
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer)

    return handler


async def run(handler, urls, **options) -> dict[str, LinkResult]:
    options.setdefault("host_delay", 0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await check_links(urls, client=client, resolver=public, **options)


# ── Status of one link ─────────────────────────────────────────────────────


async def test_200_is_ok():
    url = f"{HOST}/ok"
    result = (await run(by_path({"/ok": 200}), [url]))[url]
    assert result.status is LinkStatus.OK
    assert result.reason == ""
    assert result.http_status == 200
    assert result.final_url == ""
    assert result.keep is True


@pytest.mark.parametrize("code", [404, 410])
async def test_gone_pages_are_broken(code):
    url = f"{HOST}/gone"
    result = (await run(by_path({"/gone": code}), [url]))[url]
    assert result.status is LinkStatus.BROKEN
    assert result.reason == f"HTTP {code}"
    assert result.http_status == code
    assert result.keep is False


@pytest.mark.parametrize("code", [401, 403, 429, 500, 502, 503])
async def test_other_errors_are_unknown_and_kept(code):
    url = f"{HOST}/page"
    result = (await run(by_path({"/page": code}), [url]))[url]
    assert result.status is LinkStatus.UNKNOWN
    assert result.reason == f"HTTP {code}"
    assert result.keep is True


async def test_timeout_is_unknown():
    url = f"{HOST}/slow"
    handler = by_path({"/slow": httpx.ReadTimeout("slow")})
    result = (await run(handler, [url]))[url]
    assert result.status is LinkStatus.UNKNOWN
    assert result.reason == "timed out"
    assert result.http_status is None
    assert result.keep is True


async def test_connection_failure_is_unknown_and_names_the_error_type():
    url = f"{HOST}/down"
    handler = by_path({"/down": httpx.ConnectError("refused")})
    result = (await run(handler, [url]))[url]
    assert result.status is LinkStatus.UNKNOWN
    assert result.reason == "request failed (ConnectError)"
    assert result.keep is True


async def test_unexpected_error_is_unknown_and_never_raised():
    url = f"{HOST}/odd"
    handler = by_path({"/odd": RuntimeError("bug")})
    result = (await run(handler, [url]))[url]
    assert result.status is LinkStatus.UNKNOWN
    assert result.reason == "unexpected error: RuntimeError"


# ── HEAD, then a one-byte GET ──────────────────────────────────────────────


async def test_head_is_tried_first_and_no_get_is_sent_when_it_works():
    seen: list = []
    url = f"{HOST}/ok"
    await run(by_path({"/ok": 200}, seen), [url])
    assert [(m, p) for m, p, _ in seen] == [("HEAD", "/ok")]


@pytest.mark.parametrize("head_code", [400, 403, 405, 501])
async def test_refused_head_falls_back_to_a_one_byte_get(head_code):
    seen: list = []
    url = f"{HOST}/page"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.headers.get("range")))
        return httpx.Response(head_code if request.method == "HEAD" else 200)

    result = (await run(handler, [url]))[url]
    assert result.status is LinkStatus.OK
    assert seen == [("HEAD", None), ("GET", "bytes=0-0")]


async def test_get_answer_wins_over_a_refused_head():
    # HEAD says 403 (bot block), GET shows the page is gone.
    url = f"{HOST}/page"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403 if request.method == "HEAD" else 404)

    result = (await run(handler, [url]))[url]
    assert result.status is LinkStatus.BROKEN
    assert result.http_status == 404


async def test_head_404_does_not_trigger_a_get():
    seen: list = []
    url = f"{HOST}/gone"
    await run(by_path({"/gone": 404}, seen), [url])
    assert [m for m, _, _ in seen] == ["HEAD"]


# ── Redirects ──────────────────────────────────────────────────────────────


def redirecting(table: dict[str, tuple[int, str]], final: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        step = table.get(request.url.path)
        if step is None:
            return httpx.Response(final)
        code, location = step
        headers = {"location": location} if location else {}
        return httpx.Response(code, headers=headers)

    return handler


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
async def test_redirect_is_followed_and_final_url_is_reported(code):
    url = f"{HOST}/old"
    handler = redirecting({"/old": (code, "/new")})
    result = (await run(handler, [url]))[url]
    assert result.status is LinkStatus.OK
    assert result.final_url == f"{HOST}/new"


async def test_redirect_to_a_broken_page_is_broken():
    url = f"{HOST}/old"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://docs.example/new"})
        return httpx.Response(404)

    result = (await run(handler, [url]))[url]
    assert result.status is LinkStatus.BROKEN
    assert result.final_url == f"{HOST}/new"


async def test_relative_and_absolute_locations_both_work():
    handler = redirecting(
        {
            "/a": (302, "b"),  # relative to /a
            "/b": (302, f"{HOST}/c"),  # absolute
        }
    )
    result = (await run(handler, [f"{HOST}/a"]))[f"{HOST}/a"]
    assert result.status is LinkStatus.OK
    assert result.final_url == f"{HOST}/c"


async def test_three_redirects_are_allowed():
    handler = redirecting({"/1": (301, "/2"), "/2": (301, "/3"), "/3": (301, "/4")})
    result = (await run(handler, [f"{HOST}/1"]))[f"{HOST}/1"]
    assert result.status is LinkStatus.OK
    assert result.final_url == f"{HOST}/4"


async def test_too_many_redirects_is_unknown():
    handler = redirecting({"/loop": (302, "/loop")})
    result = (await run(handler, [f"{HOST}/loop"]))[f"{HOST}/loop"]
    assert result.status is LinkStatus.UNKNOWN
    assert result.reason == "too many redirects"
    assert result.http_status == 302
    assert result.keep is True


async def test_max_redirects_can_be_changed():
    handler = redirecting({"/1": (301, "/2")})
    result = (await run(handler, [f"{HOST}/1"], max_redirects=0))[f"{HOST}/1"]
    assert result.status is LinkStatus.UNKNOWN
    assert result.reason == "too many redirects"


async def test_redirect_without_a_location_is_unknown():
    handler = redirecting({"/odd": (302, "")})
    result = (await run(handler, [f"{HOST}/odd"]))[f"{HOST}/odd"]
    assert result.status is LinkStatus.UNKNOWN
    assert result.reason == "redirect with no address"


async def test_www_and_bare_host_may_redirect_to_each_other():
    url = "https://www.example.org/start"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.example.org":
            return httpx.Response(
                301, headers={"location": "https://example.org/start"}
            )
        return httpx.Response(200)

    result = (await run(handler, [url]))[url]
    assert result.status is LinkStatus.OK
    assert result.final_url == "https://example.org/start"


# ── The URL guard ──────────────────────────────────────────────────────────


async def test_redirect_to_another_site_is_not_followed():
    url = f"{HOST}/out"
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://other.example/x"})

    result = (await run(handler, [url]))[url]
    assert result.status is LinkStatus.UNKNOWN
    assert result.reason.startswith("redirect not checked:")
    assert result.final_url == "https://other.example/x"
    assert result.keep is True
    assert seen == ["docs.example"]  # the other site was never contacted


async def test_redirect_to_a_private_address_is_not_followed():
    url = f"{HOST}/inner"

    def resolver(host: str) -> list[str]:
        return ["10.0.0.5"] if host == "internal.example" else ["8.8.8.8"]

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://internal.example/a"})

    # internal.example is in the list too, so only the IP check can stop it.
    urls = [url, "https://internal.example/b"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await check_links(
            urls, client=client, resolver=resolver, host_delay=0
        )

    assert results[url].status is LinkStatus.UNKNOWN
    assert results[url].reason.startswith("redirect not checked:")
    assert "internal.example" not in seen


async def test_address_resolving_to_a_private_ip_is_not_requested():
    url = f"{HOST}/page"
    seen: list = []

    def resolver(host: str) -> list[str]:
        return ["192.168.1.10"]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await check_links(
            [url], client=client, resolver=resolver, host_delay=0
        )

    assert results[url].status is LinkStatus.UNKNOWN
    assert results[url].reason.startswith("address not checked:")
    assert seen == []


@pytest.mark.parametrize(
    "url",
    ["ftp://docs.example/x", "https://user:pw@docs.example/x", "file:///etc/passwd"],
)
async def test_unsafe_addresses_are_not_requested(url):
    seen: list = []
    results = await run(by_path({}, seen), [url])
    assert results[url].status is LinkStatus.UNKNOWN
    assert results[url].reason.startswith("address not checked:")
    assert seen == []


# ── The list as a whole ────────────────────────────────────────────────────


async def test_each_distinct_address_is_checked_once_in_first_seen_order():
    seen: list = []
    urls = [f"{HOST}/b", f"{HOST}/a", f"{HOST}/b"]
    results = await run(by_path({}, seen), urls)
    assert list(results) == [f"{HOST}/b", f"{HOST}/a"]
    assert sorted(p for _, p, _ in seen) == ["/a", "/b"]


async def test_mixed_results_are_matched_to_their_own_address():
    table = {"/ok": 200, "/gone": 404, "/blocked": 403}
    urls = [f"{HOST}{p}" for p in table]
    results = await run(by_path(table), urls)
    assert results[f"{HOST}/ok"].status is LinkStatus.OK
    assert results[f"{HOST}/gone"].status is LinkStatus.BROKEN
    assert results[f"{HOST}/blocked"].status is LinkStatus.UNKNOWN


async def test_empty_list_returns_nothing_and_makes_no_client():
    assert await check_links([]) == {}


async def test_non_string_entries_are_ignored():
    results = await run(by_path({}), [None, 5, f"{HOST}/ok"])  # type: ignore[list-item]
    assert list(results) == [f"{HOST}/ok"]


async def test_concurrency_below_one_is_refused():
    with pytest.raises(ValueError):
        await check_links([f"{HOST}/ok"], concurrency=0)


async def test_every_request_carries_the_user_agent():
    seen: list = []
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(by_path({}, seen)),
        headers={"User-Agent": linkcheck.USER_AGENT},
    )
    async with client:
        await check_links([f"{HOST}/ok"], client=client, resolver=public, host_delay=0)
    assert seen[0][2]["user-agent"].startswith("AITalentHub-LinkCheck/")


async def test_the_function_closes_a_client_it_made_but_not_one_it_was_given(
    monkeypatch,
):
    made: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    def factory(**options):
        options.pop("timeout", None)
        client = real(transport=httpx.MockTransport(by_path({})), **options)
        made.append(client)
        return client

    monkeypatch.setattr(linkcheck.httpx, "AsyncClient", factory)
    await check_links([f"{HOST}/ok"], resolver=public, host_delay=0)
    assert made[0].is_closed

    given = real(transport=httpx.MockTransport(by_path({})))
    await check_links([f"{HOST}/ok"], client=given, resolver=public, host_delay=0)
    assert not given.is_closed
    await given.aclose()


# ── Limits ─────────────────────────────────────────────────────────────────


class Meter:
    """Counts how many requests are in flight, in total and per host."""

    def __init__(self):
        self.total = 0
        self.peak_total = 0
        self.now: dict[str, int] = {}
        self.peak_host: dict[str, int] = {}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.total += 1
        self.now[host] = self.now.get(host, 0) + 1
        self.peak_total = max(self.peak_total, self.total)
        self.peak_host[host] = max(self.peak_host.get(host, 0), self.now[host])
        await asyncio.sleep(0.01)
        self.total -= 1
        self.now[host] -= 1
        return httpx.Response(200)


async def test_requests_in_flight_never_exceed_the_concurrency_limit():
    meter = Meter()
    urls = [f"https://site{i}.example/page" for i in range(12)]
    results = await run(meter.handler, urls, concurrency=3)
    assert all(r.status is LinkStatus.OK for r in results.values())
    assert meter.peak_total <= 3
    assert meter.peak_total > 1  # and it does work in parallel


async def test_one_host_gets_one_request_at_a_time():
    meter = Meter()
    urls = [f"{HOST}/p{i}" for i in range(6)]
    await run(meter.handler, urls, concurrency=5)
    assert meter.peak_host["docs.example"] == 1


async def test_a_busy_host_does_not_block_other_hosts():
    meter = Meter()
    urls = [f"{HOST}/p{i}" for i in range(4)] + [
        "https://other.example/a",
        "https://third.example/a",
    ]
    await run(meter.handler, urls, concurrency=3)
    assert meter.peak_total >= 2


async def test_the_pause_between_requests_to_one_host_is_applied(monkeypatch):
    pauses: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float):
        pauses.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(linkcheck.asyncio, "sleep", fake_sleep)
    urls = [f"{HOST}/a", f"{HOST}/b"]
    await run(by_path({}), urls, host_delay=0.25)
    assert pauses == [0.25, 0.25]


# ── Small helpers ──────────────────────────────────────────────────────────


def test_count_by_status_always_has_all_three_keys():
    results = [
        LinkResult("a", LinkStatus.OK),
        LinkResult("b", LinkStatus.OK),
        LinkResult("c", LinkStatus.BROKEN, "HTTP 404", 404),
    ]
    assert count_by_status(results) == {"ok": 2, "broken": 1, "unknown": 0}
    assert count_by_status([]) == {"ok": 0, "broken": 0, "unknown": 0}


def test_only_broken_results_are_not_kept():
    assert LinkResult("a", LinkStatus.OK).keep is True
    assert LinkResult("a", LinkStatus.UNKNOWN, "timed out").keep is True
    assert LinkResult("a", LinkStatus.BROKEN, "HTTP 404").keep is False
