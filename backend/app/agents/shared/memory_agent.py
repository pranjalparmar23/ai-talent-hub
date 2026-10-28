"""MemoryAgent — Redis-backed chat history for one interview session.

Each message is a JSON string in a Redis list at `interview:history:<session_id>`
with a rolling TTL. Uses the shared async client from app.database.redis (the
Redis singleton) so nothing blocks the event loop.

Postgres (InterviewSession.history) is the durable copy; Redis is the fast cache
that also lets a conversation survive between HTTP requests cheaply.

Message shape (unchanged from the original scaffold):
    {"role": "human" | "ai", "content": str}
"""

import json
import logging

logger = logging.getLogger(__name__)

KEY_TEMPLATE = "interview:history:{session_id}"
TTL_SECONDS = 24 * 60 * 60  # sessions idle for a day fall out of the cache


class MemoryAgent:
    def __init__(self, session_id: str, client=None):
        self.session_id = session_id
        self.key = KEY_TEMPLATE.format(session_id=session_id)
        self._client = client  # injectable for tests; defaults to the singleton

    @property
    def client(self):
        if self._client is None:
            from app.database.redis import redis_client

            self._client = redis_client
        return self._client

    async def add_user_message(self, message: str) -> None:
        await self._append("human", message)

    async def add_ai_message(self, message: str) -> None:
        await self._append("ai", message)

    async def get_history(self) -> list[dict]:
        raw_items = await self.client.lrange(self.key, 0, -1)
        history: list[dict] = []
        for raw in raw_items:
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning(f"MemoryAgent: skipping malformed entry in {self.key}")
                continue
            if isinstance(msg, dict) and "role" in msg and "content" in msg:
                history.append({"role": msg["role"], "content": msg["content"]})
        return history

    async def count_user_messages(self) -> int:
        return sum(1 for m in await self.get_history() if m["role"] == "human")

    async def load(self, history: list[dict]) -> None:
        """Replace the cached history wholesale (used to re-seed from Postgres
        after the Redis key expired or fell out of sync)."""
        await self.client.delete(self.key)
        if history:
            await self.client.rpush(
                self.key,
                *[
                    json.dumps({"role": m["role"], "content": m["content"]})
                    for m in history
                ],
            )
            await self.client.expire(self.key, TTL_SECONDS)

    async def clear(self) -> None:
        await self.client.delete(self.key)

    async def _append(self, role: str, content: str) -> None:
        await self.client.rpush(
            self.key, json.dumps({"role": role, "content": content})
        )
        await self.client.expire(self.key, TTL_SECONDS)
