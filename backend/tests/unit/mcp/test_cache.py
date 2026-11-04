from app.mcp.cache import TTLCache, make_key


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_make_key_is_stable_and_hides_argument_values():
    k1 = make_key("s", "t", {"b": 2, "a": "secret resume text"})
    k2 = make_key("s", "t", {"a": "secret resume text", "b": 2})
    assert k1 == k2
    assert "secret" not in k1
    assert make_key("s", "t", {"a": 1}) != make_key("s", "t", {"a": 2})
    assert make_key("s", "t", None) == make_key("s", "t", {})


def test_fresh_stale_and_expired():
    clock = Clock()
    cache = TTLCache(clock=clock)
    cache.put("k", "v", ttl=10, stale_ttl=100)
    assert cache.get("k") == ("v", False)
    clock.t = 50
    assert cache.get("k") is None  # past ttl, stale not allowed
    assert cache.get("k", allow_stale=True) == ("v", True)
    clock.t = 101
    assert cache.get("k", allow_stale=True) is None
    assert len(cache) == 0  # removed once beyond the stale window


def test_lru_eviction():
    cache = TTLCache(max_entries=2, clock=Clock())
    cache.put("a", 1, 10, 10)
    cache.put("b", 2, 10, 10)
    cache.get("a")  # a is now most recently used
    cache.put("c", 3, 10, 10)
    assert cache.get("b") is None
    assert cache.get("a") == (1, False)
    assert cache.get("c") == (3, False)
