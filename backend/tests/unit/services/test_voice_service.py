"""Unit tests for VoiceService.

The quota guard is the real VoiceQuotaService on a fake Redis and a fake clock (the
fakes from test_voice_quota_service), so the tests check the counters themselves.
Groq is replaced by FakeSpeech, so nothing uses the network or a key.
"""

import inspect
import logging

import pytest
from fastapi import HTTPException

from app.services import speech_service as ss
from app.services.speech_service import Transcript
from app.services.voice_quota_service import STT, VoiceLimits, VoiceQuotaService
from app.services.voice_service import LENGTH_GRACE_SECONDS, VoiceService
from tests.unit.services.test_voice_quota_service import FakeClock, FakeRedis

AUDIO = b"fake audio bytes"
TYPE = "audio/webm;codecs=opus"
TEXT = "I would check the logs first and then the recent deploys."

USER_UNITS = "stt:user:u1:day:20261126:units"
USER_REQUESTS = "stt:user:u1:day:20261126:requests"
ORG_UNITS = "stt:org:day:20261126:units"


class FakeSpeech:
    """Stands in for SpeechService: returns a transcript or raises what it is given."""

    def __init__(self, redis):
        self.redis = redis
        self.calls: list[tuple] = []
        self.reserved_during_call: int | None = None
        self.error: Exception | None = None
        self.on_call = None
        self.result = Transcript(
            text=TEXT, audio_seconds=30.2, latency_s=0.3, segments=2
        )

    async def transcribe(self, audio, content_type, *, declared_seconds=None):
        self.calls.append((audio, content_type, declared_seconds))
        keys = [k for k in self.redis.values if k.endswith(USER_UNITS)]
        self.reserved_during_call = int(self.redis.values[keys[0]]) if keys else None
        if self.on_call:
            self.on_call()
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def redis():
    return FakeRedis(FakeClock())


@pytest.fixture
def voice(redis):
    service = VoiceService()
    service.speech = FakeSpeech(redis)
    service.quota = VoiceQuotaService()
    service.quota.client = redis
    service.quota.clock = redis.clock
    service.quota.limits = VoiceLimits()
    return service


async def answer(voice, user="u1", audio=AUDIO, content_type=TYPE, declared=None):
    return await voice.transcribe_answer(user, audio, content_type, declared)


async def failure(voice, **kwargs) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        await answer(voice, **kwargs)
    return caught.value


def counters_are_zero(redis) -> bool:
    return all(
        int(v) == 0
        for k, v in redis.values.items()
        if not k.startswith("voice:cooldown")
    )


# ── Construction ──────────────────────────────────────────────────────────────


class TestConstruction:
    def test_constructor_takes_no_arguments(self):
        # Routes use Depends(VoiceService); an argument would become a query field.
        assert list(inspect.signature(VoiceService.__init__).parameters) == ["self"]

    def test_it_is_built_from_the_real_services(self):
        service = VoiceService()

        assert isinstance(service.speech, ss.SpeechService)
        assert isinstance(service.quota, VoiceQuotaService)


# ── A good answer ─────────────────────────────────────────────────────────────


class TestSuccess:
    async def test_returns_the_text_the_length_and_what_is_left(self, voice):
        result = await answer(voice, declared=31)

        assert result["text"] == TEXT
        assert result["audio_seconds"] == 30.2
        assert result["remaining"] == {"requests": 9, "seconds": 869}

    async def test_the_audio_and_type_reach_the_speech_service_unchanged(self, voice):
        await answer(voice, declared=31)

        assert voice.speech.calls == [(AUDIO, TYPE, 31.0)]

    async def test_the_reservation_is_corrected_to_the_real_length(self, voice, redis):
        await answer(voice, declared=60)

        assert redis.counter(USER_UNITS) == 31  # 30.2 s, rounded up
        assert redis.counter(ORG_UNITS) == 31
        assert redis.counter(USER_REQUESTS) == 1

    async def test_the_declared_length_is_reserved_first(self, voice):
        await answer(voice, declared=45.5)

        assert voice.speech.reserved_during_call == 46

    async def test_without_a_declared_length_the_longest_answer_is_reserved(
        self, voice
    ):
        await answer(voice)

        assert voice.speech.reserved_during_call == 120

    async def test_a_declared_length_slightly_over_the_limit_is_accepted_and_capped(
        self, voice
    ):
        await answer(voice, declared=120 + LENGTH_GRACE_SECONDS)

        assert voice.speech.reserved_during_call == 120
        assert voice.speech.calls[0][2] == 122.0

    async def test_an_unknown_length_keeps_the_reservation(self, voice, redis):
        voice.speech.result = Transcript(TEXT, None, 0.3, 0)

        result = await answer(voice, declared=40)

        assert result["audio_seconds"] is None
        assert redis.counter(USER_UNITS) == 40

    async def test_the_audio_length_is_rounded_for_the_screen(self, voice):
        voice.speech.result = Transcript(TEXT, 7.123456, 0.3, 1)

        assert (await answer(voice, declared=8))["audio_seconds"] == 7.12

    @pytest.mark.parametrize(
        "declared", [None, 0, -4, float("nan"), float("inf"), True, "9"]
    )
    async def test_a_declared_length_that_is_not_sensible_counts_as_missing(
        self, voice, declared
    ):
        await answer(voice, declared=declared)

        assert voice.speech.reserved_during_call == 120
        assert voice.speech.calls[0][2] is None

    async def test_a_redis_failure_after_the_call_still_returns_the_text(
        self, voice, redis
    ):
        voice.speech.on_call = lambda: setattr(redis, "down", True)

        result = await answer(voice, declared=31)

        assert result["text"] == TEXT
        assert result["remaining"] is None


# ── Refused before Groq ──────────────────────────────────────────────────────


class TestRefusedBeforeGroq:
    async def test_a_declared_length_over_the_limit(self, voice, redis):
        error = await failure(voice, declared=120 + LENGTH_GRACE_SECONDS + 1)

        assert error.status_code == 413
        assert error.detail["code"] == "too_long"
        assert "120 seconds" in error.detail["message"]
        assert voice.speech.calls == []
        assert redis.calls == 0

    async def test_the_limit_follows_the_setting(self, voice):
        voice.quota.limits = VoiceLimits(max_recording_seconds=30)

        error = await failure(voice, declared=45)

        assert error.status_code == 413
        assert "30 seconds" in error.detail["message"]

    async def test_a_limit_of_zero_switches_recording_off(self, voice, redis):
        voice.quota.limits = VoiceLimits(max_recording_seconds=0)

        error = await failure(voice)

        assert error.status_code == 503
        assert error.detail["code"] == "disabled"
        assert redis.calls == 0

    async def test_the_user_cap_stops_the_call(self, voice, redis):
        voice.quota.limits = VoiceLimits(stt_user_daily_requests=1)
        await answer(voice, declared=20)

        error = await failure(voice, declared=20)

        assert error.status_code == 429
        assert error.detail["code"] == "user_daily"
        assert error.detail["retry_after"] > 0
        assert error.headers["Retry-After"] == str(error.detail["retry_after"])
        assert "type" in error.detail["message"].lower()
        assert len(voice.speech.calls) == 1  # the second call never reached Groq
        assert redis.counter(USER_REQUESTS) == 1

    async def test_the_organisation_cap_stops_the_call(self, voice):
        voice.quota.limits = VoiceLimits(stt_org_daily_requests=1)
        await answer(voice, user="u1", declared=20)

        error = await failure(voice, user="u2", declared=20)

        assert error.detail["code"] == "org_daily"
        assert error.status_code == 429

    async def test_switched_off(self, voice):
        voice.quota.limits = VoiceLimits(enabled=False)

        error = await failure(voice)

        assert error.status_code == 503
        assert error.detail["code"] == "disabled"
        assert "retry_after" not in error.detail
        assert error.headers is None

    async def test_redis_down(self, voice, redis):
        redis.down = True

        error = await failure(voice)

        assert error.status_code == 503
        assert error.detail["code"] == "unavailable"
        assert voice.speech.calls == []

    async def test_during_a_cooldown(self, voice):
        await voice.quota.record_rate_limited(STT, 30)

        error = await failure(voice)

        assert error.status_code == 429
        assert error.detail["code"] == "cooldown"
        assert error.detail["retry_after"] == 30
        assert voice.speech.calls == []


# ── What Groq says ───────────────────────────────────────────────────────────


class TestGroqOutcomes:
    async def test_a_429_releases_and_starts_a_cooldown(self, voice, redis):
        voice.speech.error = ss.SpeechRateLimited("busy", retry_after=9.0)

        error = await failure(voice, declared=40)

        assert error.status_code == 429
        assert error.detail == {
            "code": "rate_limited",
            "message": "busy",
            "retry_after": 9,
        }
        assert error.headers == {"Retry-After": "9"}
        assert counters_are_zero(redis)
        # the next call is stopped by the cool-down
        again = await failure(voice, declared=40)
        assert again.detail["code"] == "cooldown"
        assert again.detail["retry_after"] == 9

    async def test_a_429_without_retry_after_uses_the_default_wait(self, voice):
        voice.speech.error = ss.SpeechRateLimited("busy", retry_after=None)

        error = await failure(voice, declared=40)

        assert error.detail["retry_after"] == 60
        assert (await failure(voice, declared=40)).detail["code"] == "cooldown"

    async def test_no_speech_keeps_the_quota_used(self, voice, redis):
        voice.speech.error = ss.NoSpeechDetected("nothing heard", audio_seconds=45.0)

        error = await failure(voice, declared=120)

        assert error.status_code == 422
        assert error.detail == {"code": "no_speech", "message": "nothing heard"}
        assert redis.counter(USER_UNITS) == 45  # adjusted, not released
        assert redis.counter(USER_REQUESTS) == 1
        assert redis.counter(ORG_UNITS) == 45

    async def test_no_speech_with_an_unknown_length_keeps_the_reservation(
        self, voice, redis
    ):
        voice.speech.error = ss.NoSpeechDetected("nothing heard")

        await failure(voice, declared=50)

        assert redis.counter(USER_UNITS) == 50

    async def test_no_speech_does_not_start_a_cooldown(self, voice):
        voice.speech.error = ss.NoSpeechDetected("nothing heard", audio_seconds=20)
        await failure(voice, declared=20)
        voice.speech.error = None

        assert (await answer(voice, declared=20))["text"] == TEXT

    @pytest.mark.parametrize(
        ("error", "status_code", "code"),
        [
            (ss.SpeechAudioRejected("empty", status=400), 400, "audio_rejected"),
            (ss.SpeechAudioRejected("big", status=413), 413, "audio_rejected"),
            (ss.SpeechAudioRejected("type", status=415), 415, "audio_rejected"),
            (ss.SpeechNotConfigured("no key"), 503, "not_configured"),
            (ss.SpeechUnavailable("down"), 503, "unavailable"),
        ],
    )
    async def test_other_speech_errors_release_the_reservation(
        self, voice, redis, error, status_code, code
    ):
        voice.speech.error = error

        raised = await failure(voice, declared=40)

        assert raised.status_code == status_code
        assert raised.detail == {"code": code, "message": str(error)}
        assert counters_are_zero(redis)

    async def test_these_errors_do_not_start_a_cooldown(self, voice):
        voice.speech.error = ss.SpeechUnavailable("down")
        await failure(voice, declared=40)
        voice.speech.error = None

        assert (await answer(voice, declared=40))["text"] == TEXT

    async def test_an_unexpected_error_releases_and_is_raised(self, voice, redis):
        voice.speech.error = RuntimeError("bug")

        with pytest.raises(RuntimeError):
            await answer(voice, declared=40)

        assert counters_are_zero(redis)

    async def test_a_released_slot_can_be_used_again(self, voice):
        voice.quota.limits = VoiceLimits(stt_user_daily_requests=1)
        voice.speech.error = ss.SpeechUnavailable("down")
        await failure(voice, declared=20)
        voice.speech.error = None

        assert (await answer(voice, declared=20))["text"] == TEXT


# ── Privacy ──────────────────────────────────────────────────────────────────


class TestPrivacy:
    async def test_the_log_has_no_text_audio_or_user_id(self, voice, caplog):
        with caplog.at_level(logging.DEBUG):
            await answer(voice, user="secret-user", declared=31)
            voice.speech.error = ss.NoSpeechDetected("nothing heard", audio_seconds=5)
            await failure(voice, user="secret-user", declared=31)

        assert "Voice transcription: ok" in caplog.text
        assert "Voice transcription: no_speech" in caplog.text
        assert TEXT not in caplog.text
        assert AUDIO.decode() not in caplog.text
        assert "secret-user" not in caplog.text

    async def test_error_details_have_only_a_code_a_message_and_a_wait(self, voice):
        voice.speech.error = ss.SpeechRateLimited("busy", retry_after=5)

        error = await failure(voice, declared=40)

        assert set(error.detail) == {"code", "message", "retry_after"}
