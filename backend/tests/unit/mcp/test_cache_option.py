"""Tests for the registry option `cache: false` (config and gateway).

A server with `cache: false` is one whose terms do not allow keeping its output (for
example Brave search). The gateway must call it every time and never keep a result,
not even an old copy to fall back on.
"""

import pytest

from app.mcp import gateway as gw
from app.mcp.cache import TTLCache, make_key
from app.mcp.config import RegistryError, load_registry, parse_registry
from app.mcp.gateway import MCPGateway
from tests.unit.mcp.fake_server import FakeServerKit
from tests.unit.mcp.test_gateway import Clock, make_cfg, make_gateway, no_sleep

# ── the option in the registry ───────────────────────────────


def _raw(**over):
    base = {
        "transport": "stdio",
        "command": ["fake-server"],
        "allowed_tools": ["echo"],
    }
    base.update(over)
    return {"servers": {"s": base}}


def test_cache_is_on_by_default():
    assert parse_registry(_raw(), environ={})["s"].cache is True


def test_cache_can_be_switched_off():
    assert parse_registry(_raw(cache=False), environ={})["s"].cache is False


def test_cache_can_be_stated_as_on():
    assert parse_registry(_raw(cache=True), environ={})["s"].cache is True


@pytest.mark.parametrize("bad", ["false", "true", "no", 0, 1, None, []])
def test_cache_must_be_a_real_true_or_false(bad):
    # A quoted "false" is a non-empty string, which Python treats as true. That would
    # quietly cache a server that must not be cached, so it is refused instead.
    with pytest.raises(RegistryError, match="cache must be true or false"):
        parse_registry(_raw(cache=bad), environ={})


def test_the_error_names_the_server():
    with pytest.raises(RegistryError, match="server 's'"):
        parse_registry(_raw(cache="false"), environ={})


def test_the_option_is_read_from_a_yaml_file(tmp_path):
    path = tmp_path / "servers.yaml"
    path.write_text(
        "servers:\n"
        "  a:\n"
        "    transport: stdio\n"
        "    command: [x]\n"
        "    allowed_tools: [t]\n"
        "    cache: false\n"
        "  b:\n"
        "    transport: stdio\n"
        "    command: [x]\n"
        "    allowed_tools: [t]\n",
        encoding="utf-8",
    )
    registry = load_registry(path)
    assert registry["a"].cache is False
    assert registry["b"].cache is True


def test_a_quoted_false_in_a_yaml_file_is_refused(tmp_path):
    path = tmp_path / "servers.yaml"
    path.write_text(
        "servers:\n"
        "  a:\n"
        "    transport: stdio\n"
        "    command: [x]\n"
        "    allowed_tools: [t]\n"
        '    cache: "false"\n',
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="cache must be true or false"):
        load_registry(path)


# ── the gateway honours it ───────────────────────────────────


@pytest.fixture
def kit():
    return FakeServerKit()


@pytest.fixture
def clock():
    return Clock()


def nocache_gateway(kit, clock, **cfg_over):
    return make_gateway(kit, clock, make_cfg(cache=False, **cfg_over))


async def test_every_call_goes_to_the_server(kit, clock):
    g = nocache_gateway(kit, clock)
    first = await g.call_tool("fake", "echo", {"text": "hi"})
    second = await g.call_tool("fake", "echo", {"text": "hi"})
    assert first.source == "live" and second.source == "live"
    assert second.text == "echo:hi"
    assert kit.sessions_opened == 2


async def test_a_result_is_never_stored(kit, clock):
    g = nocache_gateway(kit, clock)
    await g.call_tool("fake", "echo", {"text": "hi"})
    assert len(g.cache) == 0


async def test_a_cached_server_does_store_its_result(kit, clock):
    # The contrast: the default keeps working as before.
    g = make_gateway(kit, clock)
    await g.call_tool("fake", "echo", {"text": "hi"})
    assert len(g.cache) == 1


async def test_no_old_copy_is_served_when_the_server_fails(kit, clock):
    g = nocache_gateway(kit, clock)
    assert (await g.call_tool("fake", "echo", {"text": "hi"})).ok
    clock.t += 120  # a cached server would now answer from its stale copy
    kit.always_down = True
    result = await g.call_tool("fake", "echo", {"text": "hi"})
    assert not result.ok
    assert result.source == "unavailable"
    assert result.text == ""
    assert result.error_code == gw.UNAVAILABLE


async def test_a_cached_server_does_serve_an_old_copy_in_the_same_situation(kit, clock):
    g = make_gateway(kit, clock)
    await g.call_tool("fake", "echo", {"text": "hi"})
    clock.t += 120
    kit.always_down = True
    result = await g.call_tool("fake", "echo", {"text": "hi"})
    assert result.source == "stale_cache"


async def test_no_old_copy_is_served_while_the_circuit_is_open(kit, clock):
    cfg = make_cfg(cache=False, max_retries=0)
    g = make_gateway(kit, clock, cfg, failure_threshold=2, cooldown_seconds=30)
    assert (await g.call_tool("fake", "echo", {"text": "hi"})).ok
    kit.always_down = True
    await g.call_tool("fake", "echo", {"text": "hi"})
    await g.call_tool("fake", "echo", {"text": "hi"})
    skipped = await g.call_tool("fake", "echo", {"text": "hi"})
    assert not skipped.ok
    assert skipped.error_code == gw.CIRCUIT_OPEN
    assert skipped.text == ""


async def test_no_old_copy_is_served_when_the_rate_limit_is_hit(kit, clock):
    g = nocache_gateway(kit, clock, rate_limit_per_minute=1)
    assert (await g.call_tool("fake", "echo", {"text": "hi"})).ok
    limited = await g.call_tool("fake", "echo", {"text": "hi"})
    assert not limited.ok
    assert limited.error_code == gw.RATE_LIMITED
    assert limited.text == ""


async def test_the_other_protections_still_apply(kit, clock):
    # No cache does not mean no safety: retries and the timeout still work.
    g = nocache_gateway(kit, clock)
    kit.always_down = True
    result = await g.call_tool("fake", "echo", {"text": "hi"})
    assert not result.ok
    assert kit.sessions_opened == 3  # first try + 2 retries


async def test_the_allowlist_still_applies(kit, clock):
    g = nocache_gateway(kit, clock, allowed_tools=frozenset({"echo"}))
    result = await g.call_tool("fake", "boom", {})
    assert result.error_code == gw.TOOL_NOT_ALLOWED
    assert kit.sessions_opened == 0


async def test_one_server_without_cache_does_not_change_another(kit, clock):
    live_only = make_cfg(name="live_only", cache=False)
    cached = make_cfg(name="cached")
    g = MCPGateway(
        {"live_only": live_only, "cached": cached},
        session_factory=kit.factory,
        cache=TTLCache(clock=clock),
        url_resolver=lambda host: ["93.184.216.34"],
        environ={},
        clock=clock,
        sleep=no_sleep,
    )
    await g.call_tool("live_only", "echo", {"text": "hi"})
    await g.call_tool("live_only", "echo", {"text": "hi"})
    assert kit.sessions_opened == 2  # no cache: both calls reached the server

    await g.call_tool("cached", "echo", {"text": "hi"})
    again = await g.call_tool("cached", "echo", {"text": "hi"})
    assert again.source == "cache"
    assert kit.sessions_opened == 3  # cached: the second call did not
    assert len(g.cache) == 1  # only the cached server left anything behind


def _seed(g, text="seeded"):
    """Put an entry in the cache by hand, as if it had been stored earlier."""
    key = make_key("fake", "echo", {"text": "hi"})
    g.cache.put(key, (text, False), ttl=60, stale_ttl=3600)


async def test_an_entry_already_in_the_cache_is_not_used_as_a_fresh_answer(kit, clock):
    g = nocache_gateway(kit, clock)
    _seed(g)
    result = await g.call_tool("fake", "echo", {"text": "hi"})
    assert result.source == "live"
    assert result.text == "echo:hi"
    assert kit.sessions_opened == 1


async def test_an_entry_already_in_the_cache_is_not_used_as_an_old_copy(kit, clock):
    g = nocache_gateway(kit, clock)
    _seed(g)
    kit.always_down = True
    result = await g.call_tool("fake", "echo", {"text": "hi"})
    assert not result.ok
    assert result.text == ""
