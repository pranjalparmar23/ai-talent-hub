import pytest

from app.mcp import gateway as gw
from app.mcp.cache import TTLCache
from app.mcp.config import ServerConfig
from app.mcp.gateway import MCPGateway
from app.mcp.security import UNTRUSTED_CLOSE, UNTRUSTED_OPEN
from tests.unit.mcp.fake_server import HOSTILE_TEXT, FakeServerKit

TOOLS = frozenset({"echo", "big", "boom", "slow", "hostile", "control_chars", "fetch"})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_cfg(**over) -> ServerConfig:
    base = dict(
        name="fake",
        transport="http",
        url="https://fake.example.com/mcp",
        allowed_tools=TOOLS,
        timeout_seconds=1.0,
        max_retries=2,
        cache_ttl_seconds=60,
        stale_ttl_seconds=3600,
        max_result_chars=1000,
        allowed_url_domains=frozenset({"example.com"}),
    )
    base.update(over)
    return ServerConfig(**base)


@pytest.fixture
def kit():
    return FakeServerKit()


@pytest.fixture
def clock():
    return Clock()


async def no_sleep(_seconds):
    return None


def make_gateway(kit, clock, cfg=None, **kwargs):
    cfg = cfg or make_cfg()
    return MCPGateway(
        {cfg.name: cfg},
        session_factory=kit.factory,
        cache=TTLCache(clock=clock),
        url_resolver=lambda host: ["93.184.216.34"],
        environ=kwargs.pop("environ", {}),
        clock=clock,
        sleep=no_sleep,
        **kwargs,
    )


# ── happy path and cache ─────────────────────────────────────────────────────


async def test_live_call_returns_text(kit, clock):
    result = await make_gateway(kit, clock).call_tool("fake", "echo", {"text": "hi"})
    assert result.ok and result.source == "live"
    assert result.text == "echo:hi"
    assert result.error_code is None


async def test_second_identical_call_is_served_from_cache(kit, clock):
    g = make_gateway(kit, clock)
    await g.call_tool("fake", "echo", {"text": "hi"})
    again = await g.call_tool("fake", "echo", {"text": "hi"})
    assert again.source == "cache" and again.text == "echo:hi"
    assert kit.sessions_opened == 1


async def test_different_arguments_are_not_shared(kit, clock):
    g = make_gateway(kit, clock)
    await g.call_tool("fake", "echo", {"text": "a"})
    other = await g.call_tool("fake", "echo", {"text": "b"})
    assert other.source == "live" and other.text == "echo:b"


# ── policy: registry, allowlist, urls ────────────────────────────────────────


async def test_unknown_server_is_a_result_not_an_exception(kit, clock):
    result = await make_gateway(kit, clock).call_tool("nope", "echo", {})
    assert not result.ok and result.error_code == gw.UNKNOWN_SERVER
    assert kit.sessions_opened == 0


async def test_tool_not_in_allowlist_is_blocked_before_any_connection(kit, clock):
    cfg = make_cfg(allowed_tools=frozenset({"echo"}))
    result = await make_gateway(kit, clock, cfg).call_tool("fake", "boom", {})
    assert not result.ok and result.error_code == gw.TOOL_NOT_ALLOWED
    assert kit.sessions_opened == 0


async def test_disabled_server_is_never_called(kit, clock):
    cfg = make_cfg(enabled=False)
    result = await make_gateway(kit, clock, cfg).call_tool(
        "fake", "echo", {"text": "x"}
    )
    assert result.error_code == gw.SERVER_DISABLED
    assert kit.sessions_opened == 0


async def test_url_argument_on_allowlisted_public_host_is_allowed(kit, clock):
    result = await make_gateway(kit, clock).call_tool(
        "fake", "fetch", {"url": "https://docs.example.com/a"}
    )
    assert result.ok and result.text == "fetched:https://docs.example.com/a"


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "https://evil.com/x",
    ],
)
async def test_dangerous_url_arguments_are_blocked(kit, clock, url):
    result = await make_gateway(kit, clock).call_tool("fake", "fetch", {"url": url})
    assert not result.ok and result.error_code == gw.INVALID_ARGUMENTS
    assert kit.sessions_opened == 0


async def test_allowlisted_name_resolving_to_private_ip_is_blocked(kit, clock):
    g = make_gateway(kit, clock)
    g._resolver = lambda host: ["10.0.0.7"]
    result = await g.call_tool("fake", "fetch", {"url": "https://docs.example.com/a"})
    assert result.error_code == gw.INVALID_ARGUMENTS


# ── result handling ──────────────────────────────────────────────────────────


async def test_oversize_result_is_truncated(kit, clock):
    result = await make_gateway(kit, clock).call_tool("fake", "big", {})
    assert result.ok and result.truncated
    assert len(result.text) == 1000


async def test_control_characters_are_stripped(kit, clock):
    result = await make_gateway(kit, clock).call_tool("fake", "control_chars", {})
    assert result.text == "oktext"


async def test_hostile_output_is_wrapped_and_cannot_close_the_block(kit, clock):
    result = await make_gateway(kit, clock).call_tool("fake", "hostile", {})
    assert result.ok
    assert result.text == HOSTILE_TEXT  # raw text is kept for ingestion / scanning
    prompt = result.as_prompt_context()
    assert prompt.startswith(UNTRUSTED_OPEN)
    assert prompt.count(UNTRUSTED_CLOSE) == 1  # the attacker's closing tag is defanged
    assert prompt.rstrip().endswith(UNTRUSTED_CLOSE)


async def test_failed_result_gives_empty_prompt_context(kit, clock):
    result = await make_gateway(kit, clock).call_tool("nope", "echo", {})
    assert result.as_prompt_context() == ""


# ── failures and degradation ─────────────────────────────────────────────────


async def test_tool_error_is_reported_without_retry_and_does_not_trip_breaker(
    kit, clock
):
    g = make_gateway(kit, clock)
    for _ in range(5):
        result = await g.call_tool("fake", "boom", {})
        assert not result.ok and result.error_code == gw.TOOL_ERROR
    assert kit.sessions_opened == 5  # one session per call: no retries
    assert g._circuit("fake").failures == 0


async def test_connection_failures_are_retried_then_succeed(kit, clock):
    kit.fail_next = 2
    result = await make_gateway(kit, clock).call_tool("fake", "echo", {"text": "hi"})
    assert result.ok and result.source == "live"
    assert kit.sessions_opened == 3


async def test_server_down_returns_failure_not_exception(kit, clock):
    kit.always_down = True
    result = await make_gateway(kit, clock).call_tool("fake", "echo", {"text": "hi"})
    assert not result.ok and result.error_code == gw.UNAVAILABLE
    assert result.source == "unavailable"
    assert kit.sessions_opened == 3  # first try + 2 retries


async def test_timeout_is_reported_as_timeout(kit, clock):
    cfg = make_cfg(timeout_seconds=0.2, max_retries=0)
    result = await make_gateway(kit, clock, cfg).call_tool("fake", "slow", {})
    assert not result.ok and result.error_code == gw.TIMEOUT


async def test_stale_cache_is_served_when_server_fails(kit, clock):
    g = make_gateway(kit, clock)
    await g.call_tool("fake", "echo", {"text": "hi"})
    clock.t += 120  # past the 60 s fresh window, inside the 1 h stale window
    kit.always_down = True
    result = await g.call_tool("fake", "echo", {"text": "hi"})
    assert result.ok and result.source == "stale_cache"
    assert result.text == "echo:hi"
    assert result.meta["degraded_reason"] == gw.UNAVAILABLE


async def test_stale_cache_expires(kit, clock):
    g = make_gateway(kit, clock)
    await g.call_tool("fake", "echo", {"text": "hi"})
    clock.t += 7200  # beyond the stale window
    kit.always_down = True
    result = await g.call_tool("fake", "echo", {"text": "hi"})
    assert not result.ok


async def test_circuit_opens_after_repeated_failures_and_skips_the_server(kit, clock):
    cfg = make_cfg(max_retries=0)
    g = make_gateway(kit, clock, cfg, failure_threshold=3, cooldown_seconds=30)
    kit.always_down = True
    for i in range(3):
        await g.call_tool("fake", "echo", {"text": str(i)})
    opened = kit.sessions_opened
    skipped = await g.call_tool("fake", "echo", {"text": "next"})
    assert skipped.error_code == gw.CIRCUIT_OPEN
    assert kit.sessions_opened == opened  # no connection attempt while open

    clock.t += 31  # cooldown over: one real attempt again
    kit.always_down = False
    recovered = await g.call_tool("fake", "echo", {"text": "next"})
    assert recovered.ok and recovered.source == "live"
    assert g._circuit("fake").failures == 0


async def test_rate_limit_blocks_excess_calls_in_a_minute(kit, clock):
    cfg = make_cfg(rate_limit_per_minute=2)
    g = make_gateway(kit, clock, cfg)
    assert (await g.call_tool("fake", "echo", {"text": "1"})).ok
    assert (await g.call_tool("fake", "echo", {"text": "2"})).ok
    limited = await g.call_tool("fake", "echo", {"text": "3"})
    assert limited.error_code == gw.RATE_LIMITED
    clock.t += 61
    assert (await g.call_tool("fake", "echo", {"text": "3"})).ok


async def test_missing_secret_is_not_configured_and_not_retried(clock):
    cfg = make_cfg(api_key_env="FAKE_MCP_KEY")
    calls = {"n": 0}

    async def sleep_counter(_):
        calls["n"] += 1

    g = MCPGateway(
        {"fake": cfg},
        environ={},  # FAKE_MCP_KEY not set
        cache=TTLCache(clock=clock),
        clock=clock,
        sleep=sleep_counter,
    )
    result = await g.call_tool("fake", "echo", {"text": "x"})
    assert result.error_code == gw.NOT_CONFIGURED
    assert "FAKE_MCP_KEY" in result.error
    assert calls["n"] == 0  # no backoff sleeps: it was not retried


async def test_secret_values_never_appear_in_errors(kit, clock):
    cfg = make_cfg(api_key_env="FAKE_MCP_KEY", max_retries=0)
    secret = "super-secret-key-9999"
    g = make_gateway(kit, clock, cfg, environ={"FAKE_MCP_KEY": secret})

    @gw.asynccontextmanager
    async def leaky(_cfg):
        raise ConnectionError(f"failed with header Authorization: Bearer {secret}")
        yield  # pragma: no cover

    g._session_factory = leaky
    result = await g.call_tool("fake", "echo", {"text": "x"})
    assert not result.ok
    assert secret not in (result.error or "")
    assert "***" in result.error


# ── health ───────────────────────────────────────────────────────────────────


async def test_health_reports_up_with_missing_allowed_tools(kit, clock):
    cfg = make_cfg(allowed_tools=frozenset({"echo", "tool_that_vanished"}))
    report = await make_gateway(kit, clock, cfg).health()
    entry = report["fake"]
    assert entry["status"] == "up"
    assert entry["tool_count"] >= 1
    assert entry["missing_allowed_tools"] == ["tool_that_vanished"]


async def test_health_reports_down_and_disabled(kit, clock):
    up_cfg = make_cfg(name="a")
    off_cfg = make_cfg(name="b", enabled=False)
    kit.always_down = True
    g = MCPGateway(
        {"a": up_cfg, "b": off_cfg},
        session_factory=kit.factory,
        cache=TTLCache(clock=clock),
        environ={},
        clock=clock,
        sleep=no_sleep,
    )
    report = await g.health()
    assert report["a"]["status"] == "down"
    assert report["b"] == {"status": "disabled"}


# ── stdio guard ──────────────────────────────────────────────────────────────


async def test_stdio_transport_is_refused_in_production():
    cfg = ServerConfig(
        name="local",
        transport="stdio",
        allowed_tools=frozenset({"x"}),
        command=("python", "-c", "pass"),
        max_retries=0,
    )
    g = MCPGateway({"local": cfg}, environ={"APP_ENV": "production"}, sleep=no_sleep)
    result = await g.call_tool("local", "x", {})
    assert result.error_code == gw.NOT_CONFIGURED
    assert "production" in result.error
