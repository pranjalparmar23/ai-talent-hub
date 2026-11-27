"""Unit tests for VoiceQuotaService, using a fake Redis and a fake clock.

No test needs Redis, the network or real time. The fake Redis models what the guard
relies on: INCRBY / DECRBY, EXPIRE, GET, SET with EX, and TTL with real expiry.
"""

import logging
import math
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.services import voice_quota_service as vq
from app.services.voice_quota_service import (
    STT,
    TTS,
    Decision,
    VoiceLimits,
    VoiceQuotaService,
)

START = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc).timestamp()


class FakeClock:
    def __init__(self, now: float = START):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRedis:
    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.values: dict[str, int | str] = {}
        self.expiry: dict[str, float] = {}
        self.down = False
        self.fail_incrby_call: int | None = None  # fail this INCRBY call, once
        self.incrby_calls = 0
        self.calls = 0

    def _enter(self):
        self.calls += 1
        if self.down:
            raise ConnectionError("redis is down")

    def _purge(self, key):
        if key in self.expiry and self.expiry[key] <= self.clock():
            self.values.pop(key, None)
            self.expiry.pop(key, None)

    async def incrby(self, key, amount):
        self._enter()
        self.incrby_calls += 1
        if self.fail_incrby_call == self.incrby_calls:
            raise TimeoutError("one slow call")
        self._purge(key)
        self.values[key] = int(self.values.get(key, 0)) + amount
        return self.values[key]

    async def decrby(self, key, amount):
        self._enter()
        self._purge(key)
        self.values[key] = int(self.values.get(key, 0)) - amount
        return self.values[key]

    async def expire(self, key, seconds):
        self._enter()
        if key in self.values:
            self.expiry[key] = self.clock() + seconds
        return True

    async def get(self, key):
        self._enter()
        self._purge(key)
        value = self.values.get(key)
        return None if value is None else str(value)

    async def set(self, key, value, ex=None):
        self._enter()
        self.values[key] = value
        if ex is not None:
            self.expiry[key] = self.clock() + ex
        return True

    async def ttl(self, key):
        self._enter()
        self._purge(key)
        if key not in self.values:
            return -2
        if key not in self.expiry:
            return -1
        return math.ceil(self.expiry[key] - self.clock())

    def counter(self, fragment: str) -> int:
        """The value of the one counter whose key contains `fragment`."""
        matches = [k for k in self.values if fragment in k]
        assert len(matches) == 1, matches
        return int(self.values[matches[0]])


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def redis(clock):
    return FakeRedis(clock)


@pytest.fixture
def quota(redis, clock):
    service = VoiceQuotaService()
    service.client = redis
    service.clock = clock
    service.limits = VoiceLimits()
    return service


def limit(quota, **changes):
    quota.limits = replace(quota.limits, **changes)


# ── Settings ──────────────────────────────────────────────────────────────────


class TestLimits:
    def test_defaults_are_at_most_half_of_the_groq_free_limits(self):
        limits = VoiceLimits()

        assert limits.stt_org_daily_requests <= 2000 * 0.5
        assert limits.stt_org_daily_seconds <= 28_800 * 0.5
        assert limits.stt_org_hourly_seconds <= 7_200 * 0.5
        assert limits.stt_org_minute_requests <= 20 * 0.5
        assert limits.tts_org_daily_chars <= 3_600 * 0.5
        assert limits.tts_org_daily_requests <= 100 * 0.5
        assert limits.tts_org_minute_chars <= 1_200 * 0.5
        assert limits.tts_org_minute_requests <= 10 * 0.5

    def test_a_user_cannot_have_more_than_the_organisation(self):
        limits = VoiceLimits()

        assert limits.stt_user_daily_requests <= limits.stt_org_daily_requests
        assert limits.stt_user_daily_seconds <= limits.stt_org_daily_seconds
        assert limits.tts_user_daily_chars <= limits.tts_org_daily_chars

    def test_the_longest_recording_fits_every_speech_to_text_cap(self):
        limits = VoiceLimits()

        assert limits.max_recording_seconds == 120
        assert limits.max_recording_seconds <= limits.stt_user_daily_seconds
        assert limits.max_recording_seconds <= limits.stt_org_hourly_seconds
        assert limits.max_recording_seconds >= limits.stt_min_billed_seconds

    def test_the_longest_recording_comes_from_the_environment(self):
        limits = VoiceLimits.from_env({"VOICE_MAX_RECORDING_SECONDS": "60"})

        assert limits.max_recording_seconds == 60

    @pytest.mark.parametrize("value", ["abc", "-1", "1.5"])
    def test_a_bad_longest_recording_is_ignored(self, value):
        limits = VoiceLimits.from_env({"VOICE_MAX_RECORDING_SECONDS": value})

        assert limits.max_recording_seconds == 120

    def test_environment_overrides(self):
        limits = VoiceLimits.from_env(
            {"VOICE_TTS_ORG_DAILY_CHARS": "900", "VOICE_STT_USER_DAILY_REQUESTS": "5"}
        )

        assert limits.tts_org_daily_chars == 900
        assert limits.stt_user_daily_requests == 5
        assert limits.stt_org_daily_requests == VoiceLimits().stt_org_daily_requests

    def test_empty_environment_gives_the_defaults(self):
        assert VoiceLimits.from_env({}) == VoiceLimits()

    @pytest.mark.parametrize("value", ["abc", "-5", "1.5", "  "])
    def test_a_bad_value_is_ignored_and_the_default_stays(self, value, caplog):
        with caplog.at_level(logging.WARNING):
            limits = VoiceLimits.from_env({"VOICE_TTS_ORG_DAILY_CHARS": value})

        assert limits.tts_org_daily_chars == VoiceLimits().tts_org_daily_chars
        assert ("TTS_ORG_DAILY_CHARS" in caplog.text) == bool(value.strip())

    def test_zero_is_a_valid_cap(self):
        assert (
            VoiceLimits.from_env({"VOICE_TTS_ORG_DAILY_CHARS": "0"}).tts_org_daily_chars
            == 0
        )

    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
    def test_voice_can_be_switched_off(self, value):
        assert VoiceLimits.from_env({"VOICE_ENABLED": value}).enabled is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "anything"])
    def test_voice_is_otherwise_on(self, value):
        assert VoiceLimits.from_env({"VOICE_ENABLED": value}).enabled is True

    def test_the_service_reads_the_environment(self, monkeypatch):
        monkeypatch.setenv("VOICE_TTS_USER_DAILY_CHARS", "123")

        assert VoiceQuotaService().limits.tts_user_daily_chars == 123

    def test_constructor_takes_no_arguments(self):
        # Routes use Depends(VoiceQuotaService); an argument would become a query field.
        import inspect

        assert list(inspect.signature(VoiceQuotaService.__init__).parameters) == [
            "self"
        ]


# ── Speech to text ────────────────────────────────────────────────────────────


class TestReserveSpeechToText:
    async def test_first_call_is_allowed_and_counted(self, quota, redis):
        decision = await quota.reserve(STT, "u1", 12.0)

        assert decision.allowed
        assert decision.reason is None and decision.http_status == 200
        assert redis.counter("stt:user:u1:day:20261126:requests") == 1
        assert redis.counter("stt:user:u1:day:20261126:units") == 12
        assert redis.counter("stt:org:day:20261126:requests") == 1
        assert redis.counter("stt:org:day:20261126:units") == 12
        assert redis.counter("stt:org:hour:2026112612:units") == 12
        assert redis.counter("stt:org:minute:202611261200:requests") == 1

    async def test_short_audio_is_counted_at_the_minimum_billing(self, quota, redis):
        await quota.reserve(STT, "u1", 3.2)

        assert redis.counter("stt:user:u1:day:20261126:units") == 10

    async def test_audio_seconds_are_rounded_up(self, quota, redis):
        await quota.reserve(STT, "u1", 25.2)

        assert redis.counter("stt:user:u1:day:20261126:units") == 26

    async def test_missing_duration_is_counted_at_the_minimum(self, quota, redis):
        await quota.reserve(STT, "u1", 0)

        assert redis.counter("stt:user:u1:day:20261126:units") == 10

    async def test_user_daily_request_cap(self, quota, clock):
        limit(quota, stt_user_daily_requests=2)
        await quota.reserve(STT, "u1", 20)
        await quota.reserve(STT, "u1", 20)

        third = await quota.reserve(STT, "u1", 20)

        assert not third.allowed
        assert third.reason == vq.USER_DAILY
        assert third.http_status == 429
        assert third.retry_after == 12 * 3600  # until midnight UTC
        assert third.reservation is None
        assert third.message

    async def test_user_daily_seconds_cap(self, quota):
        limit(quota, stt_user_daily_seconds=60)
        assert (await quota.reserve(STT, "u1", 40)).allowed

        denied = await quota.reserve(STT, "u1", 40)

        assert denied.reason == vq.USER_DAILY

    async def test_another_user_is_not_affected_by_one_users_cap(self, quota):
        limit(quota, stt_user_daily_requests=1)
        await quota.reserve(STT, "u1", 20)

        assert not (await quota.reserve(STT, "u1", 20)).allowed
        assert (await quota.reserve(STT, "u2", 20)).allowed

    async def test_organisation_daily_cap_applies_across_users(self, quota):
        limit(quota, stt_org_daily_requests=2)
        await quota.reserve(STT, "u1", 20)
        await quota.reserve(STT, "u2", 20)

        denied = await quota.reserve(STT, "u3", 20)

        assert denied.reason == vq.ORG_DAILY

    async def test_organisation_hourly_audio_cap(self, quota, clock):
        limit(quota, stt_org_hourly_seconds=50)
        await quota.reserve(STT, "u1", 30)

        denied = await quota.reserve(STT, "u2", 30)

        assert denied.reason == vq.ORG_HOURLY
        assert denied.retry_after == 3600  # noon sharp: a full hour to the next one
        clock.advance(3600)
        assert (await quota.reserve(STT, "u2", 30)).allowed

    async def test_organisation_minute_cap_and_recovery(self, quota, clock):
        limit(quota, stt_org_minute_requests=2)
        await quota.reserve(STT, "u1", 20)
        await quota.reserve(STT, "u2", 20)
        clock.advance(20)

        denied = await quota.reserve(STT, "u3", 20)

        assert denied.reason == vq.ORG_MINUTE
        assert denied.retry_after == 40
        clock.advance(40)
        assert (await quota.reserve(STT, "u3", 20)).allowed

    async def test_a_new_day_starts_with_empty_counters(self, quota, clock):
        limit(quota, stt_user_daily_requests=1)
        await quota.reserve(STT, "u1", 20)
        assert not (await quota.reserve(STT, "u1", 20)).allowed

        clock.advance(12 * 3600)  # midnight UTC

        assert (await quota.reserve(STT, "u1", 20)).allowed

    async def test_a_denied_call_leaves_no_trace(self, quota, redis):
        # The user is within their own caps but the organisation is full: nothing
        # the user's counters took may stay behind.
        limit(quota, stt_org_daily_requests=1)
        await quota.reserve(STT, "u1", 20)
        await quota.reserve(STT, "u2", 30)  # denied

        assert redis.counter("stt:user:u2:day:20261126:requests") == 0
        assert redis.counter("stt:user:u2:day:20261126:units") == 0
        assert redis.counter("stt:org:day:20261126:requests") == 1
        assert redis.counter("stt:org:day:20261126:units") == 20

    async def test_a_cap_of_zero_switches_that_kind_off(self, quota):
        limit(quota, stt_user_daily_requests=0)

        assert (await quota.reserve(STT, "u1", 20)).reason == vq.USER_DAILY

    async def test_every_counter_expires(self, quota, redis):
        await quota.reserve(STT, "u1", 20)

        assert set(redis.expiry) == set(redis.values)
        ttls = {key: redis.expiry[key] - START for key in redis.expiry}
        assert ttls[next(k for k in ttls if ":minute:" in k)] == 60 + 60
        assert ttls[next(k for k in ttls if ":hour:" in k)] == 3600 + 60
        assert (
            ttls[next(k for k in ttls if "user:u1" in k and k.endswith("requests"))]
            == 86400 + 60
        )


# ── Text to speech ────────────────────────────────────────────────────────────


class TestReserveTextToSpeech:
    async def test_counts_characters_and_requests(self, quota, redis):
        decision = await quota.reserve(TTS, "u1", 136)

        assert decision.allowed
        assert redis.counter("tts:user:u1:day:20261126:units") == 136
        assert redis.counter("tts:org:day:20261126:units") == 136
        assert redis.counter("tts:org:day:20261126:requests") == 1
        assert redis.counter("tts:org:minute:202611261200:units") == 136
        assert redis.counter("tts:org:minute:202611261200:requests") == 1

    async def test_user_daily_character_cap(self, quota, clock):
        for _ in range(3):
            assert (await quota.reserve(TTS, "u1", 200)).allowed
            clock.advance(61)  # stay under the per-minute caps

        denied = await quota.reserve(TTS, "u1", 200)

        assert denied.reason == vq.USER_DAILY

    async def test_organisation_daily_characters_are_shared(self, quota, clock):
        limit(quota, tts_user_daily_chars=1000, tts_org_daily_chars=300)
        assert (await quota.reserve(TTS, "u1", 200)).allowed
        clock.advance(61)

        denied = await quota.reserve(TTS, "u2", 200)

        assert denied.reason == vq.ORG_DAILY

    async def test_organisation_daily_request_cap(self, quota, clock):
        limit(quota, tts_org_daily_requests=2)
        for user in ("u1", "u2"):
            assert (await quota.reserve(TTS, user, 50)).allowed
            clock.advance(61)

        assert (await quota.reserve(TTS, "u3", 50)).reason == vq.ORG_DAILY

    async def test_organisation_minute_character_cap(self, quota):
        limit(quota, tts_user_daily_chars=1000, tts_org_daily_chars=1000)
        await quota.reserve(TTS, "u1", 400)

        denied = await quota.reserve(TTS, "u2", 400)

        assert denied.reason == vq.ORG_MINUTE

    async def test_organisation_minute_request_cap(self, quota):
        limit(quota, tts_org_minute_requests=2)
        await quota.reserve(TTS, "u1", 10)
        await quota.reserve(TTS, "u2", 10)

        assert (await quota.reserve(TTS, "u3", 10)).reason == vq.ORG_MINUTE

    @pytest.mark.parametrize("units", [0, -3, None])
    async def test_empty_text_is_a_programming_error(self, quota, redis, units):
        with pytest.raises(ValueError):
            await quota.reserve(TTS, "u1", units)
        assert redis.calls == 0

    async def test_speech_to_text_and_text_to_speech_do_not_share_counters(self, quota):
        limit(quota, stt_user_daily_requests=1)
        await quota.reserve(STT, "u1", 20)

        assert (await quota.reserve(TTS, "u1", 100)).allowed


# ── Correcting and returning a reservation ───────────────────────────────────


class TestAdjustAndRelease:
    async def test_adjust_replaces_the_reserved_seconds_with_the_real_ones(
        self, quota, redis
    ):
        decision = await quota.reserve(STT, "u1", 120)  # the longest allowed recording

        await quota.adjust(decision.reservation, 31.4)

        assert redis.counter("stt:user:u1:day:20261126:units") == 32
        assert redis.counter("stt:org:day:20261126:units") == 32
        assert redis.counter("stt:org:hour:2026112612:units") == 32
        # requests are not changed
        assert redis.counter("stt:user:u1:day:20261126:requests") == 1
        assert redis.counter("stt:org:minute:202611261200:requests") == 1

    async def test_adjust_never_goes_below_the_minimum_billing(self, quota, redis):
        decision = await quota.reserve(STT, "u1", 120)

        await quota.adjust(decision.reservation, 2.0)

        assert redis.counter("stt:user:u1:day:20261126:units") == 10

    async def test_adjust_can_raise_the_amount_and_is_not_capped(self, quota, redis):
        limit(quota, stt_user_daily_seconds=60)
        decision = await quota.reserve(STT, "u1", 50)

        await quota.adjust(decision.reservation, 90)  # the call already happened

        assert redis.counter("stt:user:u1:day:20261126:units") == 90

    async def test_a_second_adjust_works_from_the_new_amount(self, quota, redis):
        decision = await quota.reserve(STT, "u1", 100)
        await quota.adjust(decision.reservation, 40)

        await quota.adjust(decision.reservation, 20)

        assert redis.counter("stt:user:u1:day:20261126:units") == 20

    async def test_release_returns_everything(self, quota, redis):
        decision = await quota.reserve(STT, "u1", 30)

        await quota.release(decision.reservation)

        assert all(int(v) == 0 for v in redis.values.values())

    async def test_release_after_adjust_returns_the_adjusted_amount(self, quota, redis):
        decision = await quota.reserve(STT, "u1", 100)
        await quota.adjust(decision.reservation, 40)

        await quota.release(decision.reservation)

        assert all(int(v) == 0 for v in redis.values.values())

    async def test_releasing_twice_does_not_refund_twice(self, quota, redis):
        first = await quota.reserve(STT, "u1", 30)
        await quota.reserve(STT, "u2", 30)

        await quota.release(first.reservation)
        await quota.release(first.reservation)

        assert redis.counter("stt:org:day:20261126:requests") == 1
        assert redis.counter("stt:org:day:20261126:units") == 30

    async def test_adjust_after_release_does_nothing(self, quota, redis):
        decision = await quota.reserve(STT, "u1", 30)
        await quota.release(decision.reservation)

        await quota.adjust(decision.reservation, 99)

        assert all(int(v) == 0 for v in redis.values.values())

    async def test_none_is_accepted(self, quota):
        await quota.release(None)
        await quota.adjust(None, 5)

    async def test_a_released_slot_can_be_used_again(self, quota):
        limit(quota, stt_user_daily_requests=1)
        first = await quota.reserve(STT, "u1", 20)
        assert not (await quota.reserve(STT, "u1", 20)).allowed

        await quota.release(first.reservation)

        assert (await quota.reserve(STT, "u1", 20)).allowed

    async def test_adjust_survives_a_redis_failure(self, quota, redis):
        decision = await quota.reserve(STT, "u1", 100)
        redis.down = True

        await quota.adjust(decision.reservation, 40)  # must not raise
        await quota.release(decision.reservation)


# ── Switch, cool-down and failures ───────────────────────────────────────────


class TestSwitchAndCooldown:
    async def test_switched_off_denies_without_touching_redis(self, quota, redis):
        limit(quota, enabled=False)

        decision = await quota.reserve(STT, "u1", 20)

        assert not decision.allowed
        assert decision.reason == vq.DISABLED
        assert decision.http_status == 503
        assert "switched off" in decision.message
        assert redis.calls == 0

    async def test_a_rate_limited_answer_blocks_that_kind_for_everybody(self, quota):
        await quota.record_rate_limited(STT, 30)

        for user in ("u1", "u2"):
            decision = await quota.reserve(STT, user, 20)
            assert decision.reason == vq.COOLDOWN
            assert decision.retry_after == 30
            assert decision.http_status == 429

    async def test_the_cooldown_does_not_block_the_other_kind(self, quota):
        await quota.record_rate_limited(TTS, 30)

        assert (await quota.reserve(STT, "u1", 20)).allowed

    async def test_the_cooldown_ends(self, quota, clock):
        await quota.record_rate_limited(STT, 30)
        clock.advance(31)

        assert (await quota.reserve(STT, "u1", 20)).allowed

    async def test_retry_after_counts_down(self, quota, clock):
        await quota.record_rate_limited(STT, 30)
        clock.advance(10)

        assert (await quota.reserve(STT, "u1", 20)).retry_after == 20

    @pytest.mark.parametrize(
        ("given", "expected"),
        [(None, 60), (0, 1), (0.2, 1), (12.4, 13), (99999, 3600)],
    )
    async def test_cooldown_length_is_kept_sensible(
        self, quota, redis, given, expected
    ):
        await quota.record_rate_limited(STT, given)

        assert redis.expiry["voice:cooldown:stt"] - START == expected

    async def test_a_cooldown_denial_takes_nothing(self, quota, redis):
        await quota.record_rate_limited(STT, 30)
        before = dict(redis.values)

        await quota.reserve(STT, "u1", 20)

        assert redis.values == before

    async def test_recording_a_cooldown_survives_a_redis_failure(self, quota, redis):
        redis.down = True

        await quota.record_rate_limited(STT, 30)  # must not raise


class TestRedisFailures:
    async def test_redis_down_denies_and_does_not_raise(self, quota, redis, caplog):
        redis.down = True

        with caplog.at_level(logging.WARNING):
            decision = await quota.reserve(STT, "u1", 20)

        assert not decision.allowed
        assert decision.reason == vq.UNAVAILABLE
        assert decision.http_status == 503
        assert decision.message
        assert "ConnectionError" in caplog.text

    async def test_a_failure_part_way_rolls_back_what_was_taken(self, quota, redis):
        redis.fail_incrby_call = 4  # the fourth counter fails

        decision = await quota.reserve(STT, "u1", 20)

        assert decision.reason == vq.UNAVAILABLE
        assert all(int(v) == 0 for v in redis.values.values())

    async def test_the_log_does_not_contain_the_user_id(self, quota, redis, caplog):
        redis.down = True

        with caplog.at_level(logging.WARNING):
            await quota.reserve(STT, "secret-user-id", 20)

        assert "secret-user-id" not in caplog.text


# ── What the screen can show ─────────────────────────────────────────────────


class TestRemaining:
    async def test_a_fresh_user_has_the_full_allowance(self, quota):
        room = await quota.remaining(STT, "u1")

        assert room.enabled and room.available
        assert room.requests == 10  # the organisation's per-minute cap is the lowest
        assert room.units == 900  # the user's daily seconds
        assert room.cooldown_seconds == 0

    async def test_it_follows_the_lowest_of_user_and_organisation(self, quota):
        limit(quota, tts_user_daily_chars=600, tts_org_daily_chars=1000)
        await quota.reserve(
            TTS, "u2", 450
        )  # another user used most of the organisation's

        room = await quota.remaining(TTS, "u1")

        assert room.units == 150  # minute cap 600 - 450 is the lowest

    async def test_it_counts_down_after_use(self, quota, clock):
        await quota.reserve(TTS, "u1", 200)
        clock.advance(61)

        room = await quota.remaining(TTS, "u1")

        assert room.units == 400
        assert room.requests == 5  # the minute window is new

    async def test_it_never_goes_below_zero(self, quota):
        decision = await quota.reserve(STT, "u1", 20)
        await quota.adjust(decision.reservation, 5000)

        room = await quota.remaining(STT, "u1")

        assert room.units == 0

    async def test_switched_off(self, quota):
        limit(quota, enabled=False)

        room = await quota.remaining(STT, "u1")

        assert not room.enabled
        assert (room.requests, room.units) == (0, 0)

    async def test_during_a_cooldown_nothing_is_left(self, quota):
        await quota.record_rate_limited(TTS, 45)

        room = await quota.remaining(TTS, "u1")

        assert (room.requests, room.units) == (0, 0)
        assert room.cooldown_seconds == 45

    async def test_redis_down_is_reported_not_raised(self, quota, redis):
        redis.down = True

        room = await quota.remaining(STT, "u1")

        assert room.enabled and not room.available


# ── Keys and input ───────────────────────────────────────────────────────────


class TestKeysAndInput:
    async def test_user_ids_cannot_inject_key_parts(self, quota, redis):
        await quota.reserve(STT, "a:b*c", 20)

        assert any("user:a_b_c:" in key for key in redis.values)
        assert not any("a:b" in key for key in redis.values)

    async def test_keys_hold_only_the_kind_user_and_window(self, quota, redis):
        await quota.reserve(TTS, "u1", 20)

        assert all(key.startswith("voice:tts:") for key in redis.values)

    @pytest.mark.parametrize("user_id", ["", "   ", None])
    async def test_an_empty_user_id_is_refused(self, quota, user_id):
        with pytest.raises(ValueError):
            await quota.reserve(STT, user_id, 20)

    async def test_an_unknown_kind_is_refused(self, quota):
        with pytest.raises(ValueError):
            await quota.reserve("video", "u1", 20)
        with pytest.raises(ValueError):
            await quota.record_rate_limited("video", 5)
        with pytest.raises(ValueError):
            await quota.remaining("video", "u1")

    async def test_a_uuid_user_id_works(self, quota):
        assert (
            await quota.reserve(STT, "3f2b8c1e-9d4a-4b6e-8f0a-1c2d3e4f5a6b", 20)
        ).allowed


class TestDecision:
    @pytest.mark.parametrize(
        ("reason", "status"),
        [
            (vq.USER_DAILY, 429),
            (vq.ORG_DAILY, 429),
            (vq.ORG_HOURLY, 429),
            (vq.ORG_MINUTE, 429),
            (vq.COOLDOWN, 429),
            (vq.DISABLED, 503),
            (vq.UNAVAILABLE, 503),
        ],
    )
    def test_status_and_message_for_every_reason(self, reason, status):
        decision = Decision(False, reason)

        assert decision.http_status == status
        assert decision.message
        assert "type" in decision.message.lower()
