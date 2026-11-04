"""Small in-process TTL cache for MCP results.

Two lifetimes per entry:
- fresh  (`ttl`)       — served without calling the server.
- stale  (`stale_ttl`) — served only when the server is failing, so the app keeps
                         working with slightly old reference data.

In-memory is enough for Phase 6. A Redis-backed version (shared between workers)
can replace this class later without changing the gateway, because the gateway
only uses get / put.
"""

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


def make_key(server: str, tool: str, arguments: dict | None) -> str:
    """Stable key. Arguments are hashed so long or private values never sit in the key."""
    canonical = json.dumps(arguments or {}, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"{server}:{tool}:{digest}"


@dataclass
class CacheEntry:
    value: object
    stored_at: float
    ttl: float
    stale_ttl: float


class TTLCache:
    def __init__(
        self, max_entries: int = 512, clock: Callable[[], float] = time.monotonic
    ):
        self._data: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max = max_entries
        self._clock = clock

    def put(self, key: str, value: object, ttl: float, stale_ttl: float) -> None:
        self._data[key] = CacheEntry(value, self._clock(), ttl, max(stale_ttl, ttl))
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)  # drop the least recently used

    def get(self, key: str, allow_stale: bool = False) -> tuple[object, bool] | None:
        """Return (value, is_stale) or None. Expired entries are removed."""
        entry = self._data.get(key)
        if entry is None:
            return None
        age = self._clock() - entry.stored_at
        if age <= entry.ttl:
            self._data.move_to_end(key)
            return entry.value, False
        if allow_stale and age <= entry.stale_ttl:
            return entry.value, True
        if age > entry.stale_ttl:
            del self._data[key]
        return None

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
