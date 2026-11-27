"""Unit tests for VoiceService: listening (transcribe_answer) and speaking (speak).

The quota guard is the real VoiceQuotaService on a fake Redis and a fake clock (the
fakes from test_voice_quota_service), so the tests check the counters themselves. The
clip cache is the real ClipCache on the same fake Redis. Groq is replaced by FakeSpeech
(speech to text) and FakeTts (text to speech), so nothing uses the network or a key.
"""

import asyncio
import inspect
import logging

import pytest
from fastapi import HTTPException

from app.services import speech_service as ss
from app.services import voice_service as vs
from app.services.speech_service import Transcript
from app.services.tts_service import (
    SilentAudio,
    Speech,
    TextRejected,
    TtsService,
)
from app.services.voice_cache import ClipCache
from app.services.voice_quota_service import (
    STT,
    TTS,
    VoiceLimits,
    VoiceQuotaService,
)
from app.services.voice_service import LENGTH_GRACE_SECONDS, VoiceService
from app.services.wav_audio import WavClip
from tests.unit.services.test_voice_quota_service import FakeClock, FakeRedis
from tests.unit.services.test_wav_audio import tone

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


@pytest.fixture(autouse=True)
def no_clips_in_flight():
    """No test starts with, or leaves behind, a clip that is being made."""
    vs._inflight.clear()
    yield
    leftover = dict(vs._inflight)
    vs._inflight.clear()
    assert not leftover, "a clip was still being made when the test ended"


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


# ══ Speaking: POST /interview/{id}/speak ══════════════════════════════════════════
#
# The question is an interviewer message. The cache and the quota guard share the fake
# Redis, so a test can see both the stored clips and the counters.

QUESTION = "Tell me about a time you had to debug a production issue under pressure."
LONG_QUESTION = " ".join(
    f"Sentence number {n} " + "word " * 25 + "ends here." for n in range(1, 3)
)  # two sentences of about 140 characters: two requests to Groq
CLIP = WavClip(1, 24000, tone(1.0))
ORPHEUS = "canopylabs/orpheus-v1-english"


def sentence(length: int, number: int = 1) -> str:
    body = f"Sentence number {number} " + "word " * 60
    return body[: length - 1].rstrip() + "."


def tts_counts(redis) -> dict[str, int]:
    """The text-to-speech counters by name, e.g. "org:day:requests"."""
    counts = {}
    for key, value in redis.values.items():
        parts = key.split(":")
        if parts[:2] != ["voice", "tts"] or parts[2] == "clip":
            continue
        if parts[2] == "org":
            name = f"org:{parts[3]}:{parts[5]}"
        else:
            name = f"user:{parts[4]}:{parts[6]}"  # voice:tts:user:<id>:<window>:...
        counts[name] = int(value)
    return counts


def no_tts_counters(redis) -> bool:
    return all(value == 0 for value in tts_counts(redis).values())


class FakeTts(TtsService):
    """A TtsService that does not call Groq: the text is prepared by the real code, the
    request is replaced by a scripted result or error."""

    def __init__(self, redis):
        super().__init__()
        self.redis = redis
        self.calls: list[str] = []
        self.error: Exception | None = None
        self.on_call = None
        self.gate: asyncio.Event | None = None  # hold the call until it is set
        self.started = asyncio.Event()
        self.during_call: dict[str, int] = {}
        self.characters: int | None = None  # what Groq charged; default: all of it

    async def synthesize(self, text):
        self.calls.append(text)
        self.during_call = tts_counts(self.redis)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.on_call:
            self.on_call()
        if self.error:
            raise self.error
        chunks = self.prepare(text)
        characters = sum(len(c) for c in chunks)
        if self.characters is not None:
            characters = self.characters
        return Speech(clip=CLIP, characters=characters, chunks=len(chunks), latency_s=1)


@pytest.fixture
def speaker(voice, redis):
    voice.tts_provider = "orpheus"
    voice.tts = FakeTts(redis)
    voice.cache = ClipCache()
    voice.cache.client = redis
    return voice


async def say(voice, text=QUESTION, user="u1"):
    return await voice.speak(user, text)


async def refused(voice, text=QUESTION, user="u1") -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        await say(voice, text, user)
    return caught.value


def chunks_of(voice, text) -> list[str]:
    return voice.tts.prepare(text)


def failed_after(error, characters):
    """An error as TtsService raises it: with what Groq has already charged."""
    error.characters_used = characters
    return error


# ── Construction and settings ────────────────────────────────────────────────


class TestSpeakConstruction:
    def test_it_is_built_from_the_real_services(self, monkeypatch):
        monkeypatch.delenv("VOICE_TTS_PROVIDER", raising=False)

        service = VoiceService()

        assert isinstance(service.tts, TtsService)
        assert isinstance(service.cache, ClipCache)

    def test_the_browser_reads_questions_unless_orpheus_is_chosen(self, monkeypatch):
        monkeypatch.delenv("VOICE_TTS_PROVIDER", raising=False)

        assert VoiceService().tts_provider == "browser"

    @pytest.mark.parametrize(
        ("setting", "provider"),
        [
            ("orpheus", "orpheus"),
            ("  Orpheus ", "orpheus"),
            ("ORPHEUS", "orpheus"),
            ("browser", "browser"),
            ("", "browser"),
            ("   ", "browser"),
        ],
    )
    def test_the_provider_follows_the_setting(self, monkeypatch, setting, provider):
        monkeypatch.setenv("VOICE_TTS_PROVIDER", setting)

        assert VoiceService().tts_provider == provider

    def test_an_unknown_provider_means_the_browser_and_says_so(
        self, monkeypatch, caplog
    ):
        monkeypatch.setenv("VOICE_TTS_PROVIDER", "elevenlabs")

        with caplog.at_level(logging.WARNING):
            service = VoiceService()

        assert service.tts_provider == "browser"
        assert "VOICE_TTS_PROVIDER" in caplog.text


# ── Refused before anything is spent ─────────────────────────────────────────


class TestSpeakRefused:
    async def test_the_browser_provider_makes_no_spoken_questions(self, speaker, redis):
        speaker.tts_provider = "browser"

        error = await refused(speaker)

        assert error.status_code == 503
        assert error.detail["code"] == "tts_off"
        assert "screen" in error.detail["message"]
        assert speaker.tts.calls == []
        assert redis.calls == 0

    async def test_voice_switched_off(self, speaker, redis):
        speaker.quota.limits = VoiceLimits(enabled=False)

        error = await refused(speaker)

        assert error.status_code == 503
        assert error.detail["code"] == "disabled"
        assert "retry_after" not in error.detail
        assert error.headers is None
        assert redis.calls == 0

    async def test_the_provider_is_checked_before_the_switch(self, speaker):
        speaker.tts_provider = "browser"
        speaker.quota.limits = VoiceLimits(enabled=False)

        assert (await refused(speaker)).detail["code"] == "tts_off"

    @pytest.mark.parametrize("text", ["", "   ", "!!! ???", "[]", "**", None])
    async def test_a_question_with_nothing_to_read_aloud(self, speaker, redis, text):
        error = await refused(speaker, text)

        assert error.status_code == 400
        assert error.detail["code"] == "text_rejected"
        assert speaker.tts.calls == []
        assert redis.calls == 0

    async def test_a_question_that_is_too_long(self, speaker, redis):
        error = await refused(speaker, "word " * 200)

        assert error.status_code == 413
        assert error.detail["code"] == "text_rejected"
        assert redis.calls == 0

    async def test_the_message_is_the_one_the_tts_service_wrote(self, speaker):
        with pytest.raises(TextRejected) as raised:
            speaker.tts.prepare("")

        error = await refused(speaker, "")

        assert error.detail["message"] == str(raised.value)
        assert "on the screen" in error.detail["message"]


# ── A question that is made and spoken ───────────────────────────────────────


class TestSpeakSuccess:
    async def test_returns_a_wav_file_and_says_it_was_made(self, speaker):
        result = await say(speaker)

        assert result["cache"] == "miss"
        assert result["audio"] == CLIP.to_bytes()
        assert result["audio"].startswith(b"RIFF")
        assert set(result) == {"audio", "cache"}

    async def test_groq_gets_the_question_once(self, speaker):
        await say(speaker)

        assert speaker.tts.calls == [QUESTION]

    async def test_the_characters_and_requests_are_reserved_before_the_call(
        self, speaker
    ):
        await say(speaker, LONG_QUESTION)

        total = sum(len(c) for c in chunks_of(speaker, LONG_QUESTION))
        assert len(chunks_of(speaker, LONG_QUESTION)) == 2
        during = speaker.tts.during_call
        assert during["user:day:units"] == total
        assert during["org:day:units"] == total
        assert during["org:minute:units"] == total
        assert during["org:day:requests"] == 2
        assert during["org:minute:requests"] == 2

    async def test_one_chunk_is_one_request(self, speaker, redis):
        await say(speaker)

        assert tts_counts(redis) == {
            "user:day:units": len(QUESTION),
            "org:day:units": len(QUESTION),
            "org:day:requests": 1,
            "org:minute:units": len(QUESTION),
            "org:minute:requests": 1,
        }

    async def test_two_chunks_are_two_requests(self, speaker, redis):
        await say(speaker, LONG_QUESTION)

        total = sum(len(c) for c in chunks_of(speaker, LONG_QUESTION))
        counts = tts_counts(redis)
        assert counts["user:day:units"] == total
        assert counts["org:day:requests"] == 2
        assert counts["org:minute:requests"] == 2

    async def test_the_reservation_is_corrected_to_what_groq_charged(
        self, speaker, redis
    ):
        speaker.tts.characters = 20

        await say(speaker)

        assert tts_counts(redis)["user:day:units"] == 20
        assert tts_counts(redis)["org:day:units"] == 20

    async def test_the_requests_are_corrected_to_the_ones_that_were_made(
        self, speaker, redis
    ):
        async def one_request(text):
            return Speech(clip=CLIP, characters=len(text), chunks=1, latency_s=1)

        speaker.tts.synthesize = one_request

        await say(speaker, LONG_QUESTION)

        assert tts_counts(redis)["org:day:requests"] == 1
        assert tts_counts(redis)["org:minute:requests"] == 1

    async def test_the_clip_is_stored_for_next_time(self, speaker):
        await say(speaker)

        key = speaker.cache.key(speaker.tts.model, speaker.tts.voice, QUESTION)
        assert await speaker.cache.get(key) == CLIP.to_bytes()

    async def test_the_stored_clip_expires(self, speaker, redis):
        await say(speaker)

        keys = [k for k in redis.values if k.startswith("voice:tts:clip:")]
        assert len(keys) == 1
        assert await redis.ttl(keys[0]) == speaker.cache.ttl_seconds

    async def test_a_second_request_for_the_same_question_is_a_cache_hit(
        self, speaker, redis
    ):
        await say(speaker)
        before = tts_counts(redis)

        result = await say(speaker)

        assert result["cache"] == "hit"
        assert result["audio"] == CLIP.to_bytes()
        assert speaker.tts.calls == [QUESTION]  # Groq was not called again
        assert tts_counts(redis) == before  # and nothing more was counted

    async def test_another_user_gets_the_same_clip_for_free(self, speaker, redis):
        await say(speaker, user="u1")

        result = await say(speaker, user="u2")

        assert result["cache"] == "hit"
        assert len(speaker.tts.calls) == 1
        assert not [k for k in redis.values if ":u2:" in k]

    async def test_a_cache_hit_needs_no_quota(self, speaker):
        await say(speaker)
        speaker.quota.limits = VoiceLimits(tts_user_daily_chars=1)

        assert (await say(speaker))["cache"] == "hit"
        assert (await refused(speaker, "A different question to ask?")).detail[
            "code"
        ] == "user_daily"

    async def test_a_cache_hit_is_served_during_a_cooldown(self, speaker):
        await say(speaker)
        await speaker.quota.record_rate_limited(TTS, 30)

        assert (await say(speaker))["cache"] == "hit"

    async def test_markup_does_not_change_the_clip(self, speaker):
        await say(speaker, "Tell me about **your** last role.")

        result = await say(speaker, "Tell me about your last role.")

        assert result["cache"] == "hit"
        assert len(speaker.tts.calls) == 1

    async def test_another_question_is_made_again(self, speaker):
        await say(speaker)

        result = await say(speaker, "What would you do differently next time?")

        assert result["cache"] == "miss"
        assert len(speaker.tts.calls) == 2

    @pytest.mark.parametrize("attribute", ["model", "voice"])
    async def test_a_new_model_or_voice_is_a_new_clip(self, speaker, attribute):
        await say(speaker)
        setattr(speaker.tts, attribute, "something-else")

        assert (await say(speaker))["cache"] == "miss"
        assert len(speaker.tts.calls) == 2

    async def test_a_damaged_cache_entry_is_made_again(self, speaker, redis):
        await say(speaker)
        key = next(k for k in redis.values if k.startswith("voice:tts:clip:"))
        redis.values[key] = "not base64 !!!"

        result = await say(speaker)

        assert result["cache"] == "miss"
        assert len(speaker.tts.calls) == 2

    async def test_a_cache_that_cannot_store_still_returns_the_audio(
        self, speaker, redis
    ):
        async def broken(key, audio):
            return False

        speaker.cache.set = broken

        result = await say(speaker)

        assert result["audio"] == CLIP.to_bytes()
        assert tts_counts(redis)["org:day:requests"] == 1

    async def test_a_redis_failure_after_the_call_still_returns_the_audio(
        self, speaker, redis
    ):
        speaker.tts.on_call = lambda: setattr(redis, "down", True)

        result = await say(speaker)

        assert result["audio"] == CLIP.to_bytes()
        assert result["cache"] == "miss"

    async def test_a_clip_that_was_stored_meanwhile_is_not_made_again(self, speaker):
        stored = CLIP.to_bytes()
        answers = [None, stored]

        async def get(key):
            return answers.pop(0) if answers else stored

        speaker.cache.get = get

        result = await say(speaker)

        assert result == {"audio": stored, "cache": "hit"}
        assert speaker.tts.calls == []

    async def test_the_speech_quota_is_separate_from_the_listening_quota(self, speaker):
        speaker.quota.limits = VoiceLimits(
            stt_user_daily_requests=0, stt_org_daily_requests=0
        )
        await speaker.quota.record_rate_limited(STT, 30)

        assert (await say(speaker))["cache"] == "miss"


# ── The quota guard says no ──────────────────────────────────────────────────


class TestSpeakQuota:
    async def test_the_user_cap(self, speaker, redis):
        speaker.quota.limits = VoiceLimits(tts_user_daily_chars=10)

        error = await refused(speaker)

        assert error.status_code == 429
        assert error.detail["code"] == "user_daily"
        assert error.detail["retry_after"] > 0
        assert error.headers["Retry-After"] == str(error.detail["retry_after"])
        assert speaker.tts.calls == []
        assert no_tts_counters(redis)

    async def test_the_message_points_to_the_screen_not_to_typing(self, speaker):
        speaker.quota.limits = VoiceLimits(tts_user_daily_chars=10)

        message = (await refused(speaker)).detail["message"].lower()

        assert "read the question on the screen" in message
        assert "type" not in message

    @pytest.mark.parametrize(
        ("limits", "code"),
        [
            (VoiceLimits(tts_org_daily_chars=10), "org_daily"),
            (VoiceLimits(tts_org_daily_requests=0), "org_daily"),
            (VoiceLimits(tts_org_minute_chars=10), "org_minute"),
            (VoiceLimits(tts_org_minute_requests=0), "org_minute"),
        ],
    )
    async def test_the_organisation_caps(self, speaker, redis, limits, code):
        speaker.quota.limits = limits

        error = await refused(speaker)

        assert error.status_code == 429
        assert error.detail["code"] == code
        assert "busy" in error.detail["message"]
        assert "read the question on the screen" in error.detail["message"]
        assert speaker.tts.calls == []
        assert no_tts_counters(redis)

    async def test_every_request_of_a_long_question_must_fit(self, speaker, redis):
        speaker.quota.limits = VoiceLimits(tts_org_minute_requests=1)

        error = await refused(speaker, LONG_QUESTION)

        assert error.detail["code"] == "org_minute"
        assert no_tts_counters(redis)

    async def test_the_next_question_is_allowed_after_a_user_has_room(self, speaker):
        speaker.quota.limits = VoiceLimits(tts_user_daily_chars=len(QUESTION))
        await say(speaker)

        error = await refused(speaker, "What would you do differently?")

        assert error.detail["code"] == "user_daily"
        assert (await say(speaker, user="u2"))["cache"] == "hit"

    async def test_during_a_cooldown(self, speaker):
        await speaker.quota.record_rate_limited(TTS, 30)

        error = await refused(speaker)

        assert error.status_code == 429
        assert error.detail["code"] == "cooldown"
        assert error.detail["retry_after"] == 30
        assert "busy" in error.detail["message"]
        assert speaker.tts.calls == []

    async def test_redis_down(self, speaker, redis):
        redis.down = True

        error = await refused(speaker)

        assert error.status_code == 503
        assert error.detail["code"] == "unavailable"
        assert "screen" in error.detail["message"]
        assert speaker.tts.calls == []


# ── What Groq says ───────────────────────────────────────────────────────────


class TestSpeakGroqOutcomes:
    async def test_a_429_before_any_chunk_releases_and_starts_a_cooldown(
        self, speaker, redis
    ):
        speaker.tts.error = ss.SpeechRateLimited("busy", retry_after=9.0)

        error = await refused(speaker)

        assert error.status_code == 429
        assert error.detail == {
            "code": "rate_limited",
            "message": "busy",
            "retry_after": 9,
        }
        assert error.headers == {"Retry-After": "9"}
        assert no_tts_counters(redis)
        again = await refused(speaker, "Another question to read out?")
        assert again.detail["code"] == "cooldown"
        assert again.detail["retry_after"] == 9

    async def test_a_429_without_retry_after_uses_the_default_wait(self, speaker):
        speaker.tts.error = ss.SpeechRateLimited("busy", retry_after=None)

        error = await refused(speaker)

        assert error.detail["retry_after"] == 60
        assert (await refused(speaker)).detail["code"] == "cooldown"

    async def test_a_tts_cooldown_does_not_stop_listening(self, speaker):
        speaker.tts.error = ss.SpeechRateLimited("busy", retry_after=9.0)
        await refused(speaker)

        assert (await answer(speaker, declared=20))["text"] == TEXT

    async def test_a_429_on_the_second_chunk_keeps_the_first_charged(
        self, speaker, redis
    ):
        first = len(chunks_of(speaker, LONG_QUESTION)[0])
        speaker.tts.error = failed_after(
            ss.SpeechRateLimited("busy", retry_after=7), first
        )

        error = await refused(speaker, LONG_QUESTION)

        assert error.detail["code"] == "rate_limited"
        assert tts_counts(redis)["user:day:units"] == first
        assert tts_counts(redis)["org:day:units"] == first
        assert tts_counts(redis)["org:day:requests"] == 2  # chunk 1, and the 429
        assert tts_counts(redis)["org:minute:requests"] == 2
        assert (await refused(speaker, LONG_QUESTION)).detail["code"] == "cooldown"

    async def test_another_error_on_the_second_chunk_keeps_the_first_charged(
        self, speaker, redis
    ):
        first = len(chunks_of(speaker, LONG_QUESTION)[0])
        speaker.tts.error = failed_after(ss.SpeechUnavailable("down"), first)

        error = await refused(speaker, LONG_QUESTION)

        assert error.status_code == 503
        assert error.detail == {"code": "unavailable", "message": "down"}
        assert tts_counts(redis)["user:day:units"] == first
        assert tts_counts(redis)["org:day:requests"] == 2
        # it is not a rate limit: no cool-down
        speaker.tts.error = None
        assert (await say(speaker, LONG_QUESTION))["cache"] == "miss"

    @pytest.mark.parametrize(
        ("error", "status_code", "code"),
        [
            (ss.SpeechNotConfigured("no key"), 503, "not_configured"),
            (ss.SpeechUnavailable("down"), 503, "unavailable"),
            (TextRejected("no", status=400), 400, "text_rejected"),
            (TextRejected("long", status=413), 413, "text_rejected"),
        ],
    )
    async def test_an_error_before_anything_was_charged_releases_everything(
        self, speaker, redis, error, status_code, code
    ):
        speaker.tts.error = failed_after(error, 0)

        raised = await refused(speaker, LONG_QUESTION)

        assert raised.status_code == status_code
        assert raised.detail == {"code": code, "message": str(error)}
        assert no_tts_counters(redis)

    async def test_an_error_that_does_not_say_what_it_cost_releases_everything(
        self, speaker, redis
    ):
        speaker.tts.error = ss.SpeechUnavailable("down")  # no characters_used

        await refused(speaker)

        assert no_tts_counters(redis)

    async def test_these_errors_do_not_start_a_cooldown(self, speaker):
        speaker.tts.error = ss.SpeechUnavailable("down")
        await refused(speaker)
        speaker.tts.error = None

        assert (await say(speaker))["cache"] == "miss"

    async def test_a_silent_clip_is_charged_and_not_stored(self, speaker, redis):
        speaker.tts.error = failed_after(SilentAudio("silent"), len(QUESTION))

        error = await refused(speaker)

        assert error.status_code == 502
        assert error.detail == {"code": "silent_audio", "message": "silent"}
        assert tts_counts(redis)["user:day:units"] == len(QUESTION)
        assert tts_counts(redis)["org:day:requests"] == 1  # no extra request
        assert not [k for k in redis.values if k.startswith("voice:tts:clip:")]

    async def test_a_silent_clip_is_not_remembered_so_the_next_try_asks_again(
        self, speaker
    ):
        speaker.tts.error = failed_after(SilentAudio("silent"), len(QUESTION))
        await refused(speaker)
        speaker.tts.error = None

        assert (await say(speaker))["cache"] == "miss"
        assert len(speaker.tts.calls) == 2

    async def test_a_silent_second_chunk_charges_both_chunks(self, speaker, redis):
        total = sum(len(c) for c in chunks_of(speaker, LONG_QUESTION))
        speaker.tts.error = failed_after(SilentAudio("silent"), total)

        await refused(speaker, LONG_QUESTION)

        assert tts_counts(redis)["user:day:units"] == total
        assert tts_counts(redis)["org:day:requests"] == 2

    async def test_a_silent_first_chunk_charges_only_the_first(self, speaker, redis):
        first = len(chunks_of(speaker, LONG_QUESTION)[0])
        speaker.tts.error = failed_after(SilentAudio("silent"), first)

        await refused(speaker, LONG_QUESTION)

        assert tts_counts(redis)["user:day:units"] == first
        assert tts_counts(redis)["org:day:requests"] == 1

    async def test_an_error_after_the_last_chunk_is_not_counted_as_an_extra_request(
        self, speaker, redis
    ):
        total = sum(len(c) for c in chunks_of(speaker, LONG_QUESTION))
        speaker.tts.error = failed_after(ss.SpeechUnavailable("cannot join"), total)

        await refused(speaker, LONG_QUESTION)

        assert tts_counts(redis)["user:day:units"] == total
        assert tts_counts(redis)["org:day:requests"] == 2

    async def test_an_unexpected_error_releases_and_is_raised(self, speaker, redis):
        speaker.tts.error = RuntimeError("bug")

        with pytest.raises(RuntimeError):
            await say(speaker)

        assert no_tts_counters(redis)

    async def test_nothing_is_stored_after_a_failure(self, speaker, redis):
        speaker.tts.error = ss.SpeechUnavailable("down")
        await refused(speaker)

        assert not [k for k in redis.values if k.startswith("voice:tts:clip:")]

    async def test_a_released_slot_can_be_used_again(self, speaker):
        speaker.quota.limits = VoiceLimits(tts_org_daily_requests=1)
        speaker.tts.error = ss.SpeechUnavailable("down")
        await refused(speaker)
        speaker.tts.error = None

        assert (await say(speaker))["cache"] == "miss"


class TestChunksAnswered:
    @pytest.mark.parametrize(
        ("lengths", "used", "answered"),
        [
            ([100], 100, 1),
            ([100, 80], 100, 1),
            ([100, 80], 180, 2),
            ([100, 80], 101, 2),
            ([100, 80], 1, 1),
            ([100, 80], 500, 2),  # more than the text: all of them
            ([100, 80, 60], 180, 2),
        ],
    )
    def test_counts_the_leading_chunks_that_add_up_to_the_characters(
        self, lengths, used, answered
    ):
        chunks = ["x" * n for n in lengths]

        assert vs._chunks_answered(chunks, used) == answered


# ── Two requests at the same moment ──────────────────────────────────────────


class TestSpeakConcurrency:
    async def test_the_same_question_twice_at_once_costs_one_call(self, speaker, redis):
        speaker.tts.gate = asyncio.Event()
        first = asyncio.create_task(say(speaker, user="u1"))
        await speaker.tts.started.wait()
        second = asyncio.create_task(say(speaker, user="u2"))
        await asyncio.sleep(0)
        speaker.tts.gate.set()

        results = await asyncio.gather(first, second)

        assert [r["cache"] for r in results] == ["miss", "hit"]
        assert results[0]["audio"] == results[1]["audio"] == CLIP.to_bytes()
        assert speaker.tts.calls == [QUESTION]
        assert tts_counts(redis)["org:day:requests"] == 1
        assert tts_counts(redis)["org:day:units"] == len(QUESTION)
        assert not [k for k in redis.values if ":u2:" in k]

    async def test_many_requests_share_one_call(self, speaker):
        speaker.tts.gate = asyncio.Event()
        tasks = [asyncio.create_task(say(speaker)) for _ in range(5)]
        await speaker.tts.started.wait()
        await asyncio.sleep(0)
        speaker.tts.gate.set()

        results = await asyncio.gather(*tasks)

        assert sorted(r["cache"] for r in results) == ["hit"] * 4 + ["miss"]
        assert len(speaker.tts.calls) == 1

    async def test_different_questions_do_not_share(self, speaker):
        speaker.tts.gate = asyncio.Event()
        first = asyncio.create_task(say(speaker, QUESTION))
        second = asyncio.create_task(say(speaker, "What would you change?"))
        await asyncio.sleep(0.01)
        speaker.tts.gate.set()

        results = await asyncio.gather(first, second)

        assert [r["cache"] for r in results] == ["miss", "miss"]
        assert len(speaker.tts.calls) == 2

    async def test_a_failure_reaches_everyone_waiting_and_is_settled_once(
        self, speaker, redis
    ):
        speaker.tts.gate = asyncio.Event()
        speaker.tts.error = ss.SpeechUnavailable("down")
        first = asyncio.create_task(refused(speaker, user="u1"))
        await speaker.tts.started.wait()
        second = asyncio.create_task(refused(speaker, user="u2"))
        await asyncio.sleep(0)
        speaker.tts.gate.set()

        errors = await asyncio.gather(first, second)

        assert [e.detail["code"] for e in errors] == ["unavailable", "unavailable"]
        assert len(speaker.tts.calls) == 1
        assert no_tts_counters(redis)

    async def test_after_a_failure_the_next_request_tries_again(self, speaker):
        speaker.tts.error = ss.SpeechUnavailable("down")
        await refused(speaker)
        speaker.tts.error = None

        assert (await say(speaker))["cache"] == "miss"
        assert vs._inflight == {}

    async def test_a_request_that_is_given_up_does_not_stop_the_call(
        self, speaker, redis
    ):
        speaker.tts.gate = asyncio.Event()
        leaving = asyncio.create_task(say(speaker))
        await speaker.tts.started.wait()
        leaving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leaving
        speaker.tts.gate.set()
        await asyncio.gather(*vs._inflight.values())

        # the quota is settled and the paid-for clip is kept
        assert tts_counts(redis)["org:day:requests"] == 1
        assert tts_counts(redis)["user:day:units"] == len(QUESTION)
        assert (await say(speaker))["cache"] == "hit"
        assert len(speaker.tts.calls) == 1

    async def test_one_waiter_leaving_does_not_stop_the_others(self, speaker):
        speaker.tts.gate = asyncio.Event()
        first = asyncio.create_task(say(speaker, user="u1"))
        await speaker.tts.started.wait()
        second = asyncio.create_task(say(speaker, user="u2"))
        await asyncio.sleep(0)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        speaker.tts.gate.set()

        assert (await first)["cache"] == "miss"

    async def test_a_given_up_request_that_then_fails_leaves_the_counters_clean(
        self, speaker, redis
    ):
        speaker.tts.gate = asyncio.Event()
        speaker.tts.error = ss.SpeechUnavailable("down")
        leaving = asyncio.create_task(say(speaker))
        await speaker.tts.started.wait()
        leaving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leaving
        speaker.tts.gate.set()
        await asyncio.gather(*vs._inflight.values(), return_exceptions=True)

        assert no_tts_counters(redis)

    async def test_nothing_is_left_in_flight_after_success_or_failure(self, speaker):
        await say(speaker)
        speaker.tts.error = ss.SpeechUnavailable("down")
        await refused(speaker, "Another question for this test?")

        assert vs._inflight == {}


# ── Privacy ──────────────────────────────────────────────────────────────────


class TestSpeakPrivacy:
    async def test_the_log_has_no_question_audio_or_user_id(self, speaker, caplog):
        with caplog.at_level(logging.DEBUG):
            await say(speaker, user="secret-user")
            await say(speaker, user="secret-user")
            speaker.tts.error = failed_after(SilentAudio("silent"), 5)
            await refused(speaker, "A second private question to ask.", "secret-user")
            speaker.quota.limits = VoiceLimits(tts_user_daily_chars=1)
            await refused(speaker, "A third private question to ask.", "secret-user")

        assert "Voice speak: ok" in caplog.text
        assert "Voice speak: cache hit" in caplog.text
        assert "Voice speak: silent_audio" in caplog.text
        assert "Voice speak: denied (user_daily)" in caplog.text
        assert "private question" not in caplog.text
        assert "debug a production issue" not in caplog.text
        assert "secret-user" not in caplog.text

    async def test_error_details_have_only_a_code_a_message_and_a_wait(self, speaker):
        speaker.tts.error = ss.SpeechRateLimited("busy", retry_after=5)

        error = await refused(speaker)

        assert set(error.detail) == {"code", "message", "retry_after"}

    async def test_no_cache_key_holds_the_question_or_a_user(self, speaker, redis):
        await say(speaker, user="secret-user")

        keys = " ".join(redis.values)
        assert "debug" not in keys
        assert "secret-user" not in " ".join(k for k in redis.values if "clip" in k)
