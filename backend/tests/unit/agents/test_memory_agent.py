"""Unit tests for MemoryAgent using an in-memory fake of the async Redis client."""

import json

import pytest

from app.agents.shared.memory_agent import TTL_SECONDS, MemoryAgent


class FakeRedis:
    """Implements just the async calls MemoryAgent uses."""

    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}

    async def rpush(self, key, *values):
        self.lists.setdefault(key, []).extend(values)
        return len(self.lists[key])

    async def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        return items[start:] if end == -1 else items[start : end + 1]

    async def expire(self, key, seconds):
        self.ttls[key] = seconds

    async def delete(self, key):
        self.lists.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def memory(redis):
    return MemoryAgent("session-1", client=redis)


class TestMemoryAgent:
    @pytest.mark.asyncio
    async def test_empty_history(self, memory):
        assert await memory.get_history() == []

    @pytest.mark.asyncio
    async def test_messages_stored_in_order_with_roles(self, memory):
        await memory.add_ai_message("Tell me about yourself.")
        await memory.add_user_message("I build APIs.")
        await memory.add_ai_message("Why Python?")

        assert await memory.get_history() == [
            {"role": "ai", "content": "Tell me about yourself."},
            {"role": "human", "content": "I build APIs."},
            {"role": "ai", "content": "Why Python?"},
        ]

    @pytest.mark.asyncio
    async def test_ttl_set_on_write(self, memory, redis):
        await memory.add_user_message("hi")
        assert redis.ttls[memory.key] == TTL_SECONDS

    @pytest.mark.asyncio
    async def test_sessions_are_isolated(self, redis):
        a = MemoryAgent("a", client=redis)
        b = MemoryAgent("b", client=redis)
        await a.add_user_message("only in a")
        assert await b.get_history() == []

    @pytest.mark.asyncio
    async def test_count_user_messages(self, memory):
        await memory.add_ai_message("q1")
        await memory.add_user_message("a1")
        await memory.add_ai_message("q2")
        await memory.add_user_message("a2")
        assert await memory.count_user_messages() == 2

    @pytest.mark.asyncio
    async def test_malformed_entries_skipped(self, memory, redis):
        await memory.add_user_message("good")
        redis.lists[memory.key].append("{not json")
        redis.lists[memory.key].append(json.dumps({"role": "ai"}))  # no content
        redis.lists[memory.key].append(json.dumps(["not", "a", "dict"]))

        assert await memory.get_history() == [{"role": "human", "content": "good"}]

    @pytest.mark.asyncio
    async def test_load_replaces_existing_history(self, memory):
        await memory.add_user_message("stale")
        await memory.load(
            [
                {"role": "ai", "content": "fresh q"},
                {"role": "human", "content": "fresh a"},
            ]
        )
        history = await memory.get_history()
        assert [m["content"] for m in history] == ["fresh q", "fresh a"]

    @pytest.mark.asyncio
    async def test_load_empty_clears(self, memory):
        await memory.add_user_message("x")
        await memory.load([])
        assert await memory.get_history() == []

    @pytest.mark.asyncio
    async def test_clear(self, memory):
        await memory.add_user_message("x")
        await memory.clear()
        assert await memory.get_history() == []

    def test_client_defaults_to_redis_singleton(self):
        from app.database.redis import redis_client

        assert MemoryAgent("s").client is redis_client
