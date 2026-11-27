"""Unit tests for ClipCache, using the fake Redis and fake clock of the quota tests.

The fake Redis answers with text, like the app's shared client (it is created with
decode_responses=True), which is why clips are stored as base64.
"""

import inspect
import logging
import re

import pytest

from app.services import voice_cache as vc
from app.services.voice_cache import ClipCache
from app.services.wav_audio import WavClip
from tests.unit.services.test_voice_quota_service import FakeClock, FakeRedis
from tests.unit.services.test_wav_audio import tone

MODEL = "canopylabs/orpheus-v1-english"
VOICE = "daniel"
TEXT = "Tell me about a time you had to debug a production issue under pressure."
AUDIO = WavClip(1, 24000, tone(0.5)).to_bytes()


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.delenv("VOICE_TTS_CACHE_SECONDS", raising=False)


@pytest.fixture
def redis():
    return FakeRedis(FakeClock())


@pytest.fixture
def cache(redis):
    store = ClipCache()
    store.client = redis
    return store


class TestKey:
    def test_it_is_a_prefix_and_a_sha256(self):
        key = ClipCache.key(MODEL, VOICE, TEXT)

        assert re.fullmatch(r"voice:tts:clip:[0-9a-f]{64}", key)

    def test_the_same_clip_has_the_same_key(self):
        assert ClipCache.key(MODEL, VOICE, TEXT) == ClipCache.key(MODEL, VOICE, TEXT)

    @pytest.mark.parametrize(
        "other",
        [
            (MODEL + "-v2", VOICE, TEXT),
            (MODEL, "hannah", TEXT),
            (MODEL, VOICE, TEXT + " "),
            (MODEL, VOICE, TEXT.lower()),
        ],
    )
    def test_a_different_model_voice_or_text_has_a_different_key(self, other):
        assert ClipCache.key(*other) != ClipCache.key(MODEL, VOICE, TEXT)

    def test_fields_cannot_run_into_each_other(self):
        assert ClipCache.key("a", "bc", "d") != ClipCache.key("ab", "c", "d")
        assert ClipCache.key("a", "b", "cd") != ClipCache.key("a", "bc", "d")

    def test_the_key_does_not_contain_the_text(self):
        key = ClipCache.key(MODEL, VOICE, "SECRET-QUESTION-TEXT")

        assert "SECRET" not in key

    def test_text_that_is_not_ascii_works(self):
        assert ClipCache.key(MODEL, VOICE, "Café — naïve 日本")


class TestGetAndSet:
    async def test_a_stored_clip_comes_back_unchanged(self, cache):
        key = ClipCache.key(MODEL, VOICE, TEXT)

        assert await cache.set(key, AUDIO) is True

        assert await cache.get(key) == AUDIO

    async def test_a_clip_that_was_never_stored_is_a_miss(self, cache):
        assert await cache.get(ClipCache.key(MODEL, VOICE, TEXT)) is None

    async def test_every_byte_value_survives(self, cache):
        audio = b"RIFF" + bytes(range(256)) * 4
        await cache.set("voice:tts:clip:x", audio)

        assert await cache.get("voice:tts:clip:x") == audio

    async def test_clips_are_stored_as_text(self, cache, redis):
        await cache.set("voice:tts:clip:x", AUDIO)

        assert isinstance(redis.values["voice:tts:clip:x"], str)

    async def test_a_client_that_returns_bytes_works_too(self, cache, redis):
        await cache.set("voice:tts:clip:x", AUDIO)
        stored = redis.values["voice:tts:clip:x"]

        async def get_bytes(key):
            return stored.encode("ascii")

        redis.get = get_bytes

        assert await cache.get("voice:tts:clip:x") == AUDIO

    async def test_a_clip_expires(self, cache, redis):
        await cache.set("voice:tts:clip:x", AUDIO)

        assert await redis.ttl("voice:tts:clip:x") == 3 * 24 * 3600

        redis.clock.advance(3 * 24 * 3600 + 1)

        assert await cache.get("voice:tts:clip:x") is None

    async def test_the_lifetime_can_be_changed(self, redis, monkeypatch):
        monkeypatch.setenv("VOICE_TTS_CACHE_SECONDS", "600")
        store = ClipCache()
        store.client = redis

        await store.set("voice:tts:clip:x", AUDIO)

        assert await redis.ttl("voice:tts:clip:x") == 600

    @pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5"])
    def test_a_bad_lifetime_is_ignored(self, monkeypatch, caplog, value):
        monkeypatch.setenv("VOICE_TTS_CACHE_SECONDS", value)

        with caplog.at_level(logging.WARNING):
            store = ClipCache()

        assert store.ttl_seconds == vc.DEFAULT_TTL_SECONDS
        assert "VOICE_TTS_CACHE_SECONDS" in caplog.text

    async def test_a_new_clip_replaces_an_old_one(self, cache):
        other = WavClip(1, 24000, tone(0.25)).to_bytes()
        await cache.set("voice:tts:clip:x", AUDIO)

        await cache.set("voice:tts:clip:x", other)

        assert await cache.get("voice:tts:clip:x") == other


class TestWhatIsNotStored:
    async def test_empty_audio_is_not_stored(self, cache, redis):
        assert await cache.set("voice:tts:clip:x", b"") is False
        assert redis.values == {}

    @pytest.mark.parametrize("value", [None, "text", 12, bytearray(b"RIFF")])
    async def test_something_that_is_not_bytes_is_not_stored(self, cache, redis, value):
        assert await cache.set("voice:tts:clip:x", value) is False
        assert redis.values == {}

    async def test_a_clip_over_the_size_limit_is_not_stored(self, cache, redis):
        cache.max_bytes = 100

        assert await cache.set("voice:tts:clip:x", b"RIFF" + b"\x00" * 100) is False
        assert redis.values == {}

    async def test_a_clip_exactly_at_the_size_limit_is_stored(self, cache):
        cache.max_bytes = 104

        assert await cache.set("voice:tts:clip:x", b"RIFF" + b"\x00" * 100) is True

    def test_the_default_limit_fits_a_minute_of_speech(self):
        one_minute = 60 * 24000 * 2 + 44

        assert vc.DEFAULT_MAX_BYTES >= one_minute


class TestDamagedEntries:
    @pytest.mark.parametrize(
        "stored",
        ["not base64 !!!", "UklGR", "%%%%", "UklG\nRg==", "é"],
    )
    async def test_a_damaged_entry_is_a_miss(self, cache, redis, caplog, stored):
        redis.values["voice:tts:clip:x"] = stored

        with caplog.at_level(logging.WARNING):
            assert await cache.get("voice:tts:clip:x") is None

        assert "Voice cache" in caplog.text

    async def test_something_that_is_not_audio_is_a_miss(self, cache, redis):
        import base64

        redis.values["voice:tts:clip:x"] = base64.b64encode(b"<html>oops").decode()

        assert await cache.get("voice:tts:clip:x") is None

    async def test_bytes_that_are_not_text_are_a_miss(self, cache, redis):
        async def get_bytes(key):
            return b"\xff\xfe\x00"

        redis.get = get_bytes

        assert await cache.get("voice:tts:clip:x") is None


class TestRedisFailures:
    async def test_a_read_failure_is_a_miss_and_does_not_raise(
        self, cache, redis, caplog
    ):
        await cache.set("voice:tts:clip:x", AUDIO)
        redis.down = True

        with caplog.at_level(logging.WARNING):
            assert await cache.get("voice:tts:clip:x") is None

        assert "ConnectionError" in caplog.text

    async def test_a_write_failure_is_reported_and_does_not_raise(
        self, cache, redis, caplog
    ):
        redis.down = True

        with caplog.at_level(logging.WARNING):
            assert await cache.set("voice:tts:clip:x", AUDIO) is False

        assert "ConnectionError" in caplog.text

    async def test_it_works_again_when_redis_comes_back(self, cache, redis):
        redis.down = True
        await cache.set("voice:tts:clip:x", AUDIO)
        redis.down = False

        assert await cache.set("voice:tts:clip:x", AUDIO) is True
        assert await cache.get("voice:tts:clip:x") == AUDIO


class TestPrivacyAndShape:
    async def test_nothing_spoken_is_logged(self, cache, redis, caplog):
        key = ClipCache.key(MODEL, VOICE, "ZEBRA-CONFIDENTIAL-QUESTION")
        redis.down = True

        with caplog.at_level(logging.DEBUG):
            await cache.set(key, AUDIO)
            await cache.get(key)

        assert "ZEBRA" not in caplog.text
        assert key not in caplog.text

    def test_constructor_takes_no_arguments(self):
        assert list(inspect.signature(ClipCache.__init__).parameters) == ["self"]

    def test_the_default_lifetime_is_three_days(self):
        assert ClipCache().ttl_seconds == 3 * 24 * 3600

    def test_the_client_is_only_looked_up_on_first_use(self):
        store = ClipCache()
        assert store._client is None  # created on first use, not at import
