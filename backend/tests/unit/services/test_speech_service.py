"""Unit tests for SpeechService. Groq is replaced by an httpx mock transport, so no
test uses the network, an API key or quota."""

import inspect
import logging
import re

import httpx
import pytest

from app.services import speech_service as ss
from app.services.speech_service import (
    NoSpeechDetected,
    SpeechAudioRejected,
    SpeechNotConfigured,
    SpeechRateLimited,
    SpeechService,
    SpeechUnavailable,
    Transcript,
)

KEY = "gsk_test_secret_key_123"
AUDIO = b"RIFF....fake audio bytes....WAVE"
SENTENCE = "Tell me about a time you had to debug a production issue under pressure."

GOOD = {"no_speech_prob": 0.01, "avg_logprob": -0.2}
SILENT = {"no_speech_prob": 0.95, "avg_logprob": -1.8}


def segment(text="hello", start=0.0, end=2.0, **stats):
    return {"id": 0, "start": start, "end": end, "text": text, **(stats or GOOD)}


def reply(text=SENTENCE, segments=None):
    return {
        "text": f" {text}",
        "segments": [segment(text)] if segments is None else segments,
    }


class FakeGroq:
    """Answers the transcription endpoint and remembers what it was sent."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.body: object = reply()
        self.headers: dict[str, str] = {}
        self.raises: Exception | None = None
        self.raw: bytes | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises:
            raise self.raises
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw, headers=self.headers)
        return httpx.Response(self.status, json=self.body, headers=self.headers)

    def error(self, status, message="Something went wrong", **headers):
        self.status = status
        self.body = {"error": {"message": message}}
        self.headers = headers


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.delenv("VOICE_STT_MODEL", raising=False)
    monkeypatch.delenv("VOICE_MAX_AUDIO_BYTES", raising=False)


@pytest.fixture
def groq():
    return FakeGroq()


@pytest.fixture
async def service(groq):
    svc = SpeechService()
    svc.http = httpx.AsyncClient(transport=httpx.MockTransport(groq))
    ticks = iter(range(0, 1000))
    svc.clock = lambda: next(ticks) * 0.25  # every call to the clock moves 0.25 s
    yield svc
    await svc.http.aclose()


async def say(service, audio=AUDIO, content_type="audio/webm", **kwargs):
    return await service.transcribe(audio, content_type, **kwargs)


# ── Settings ──────────────────────────────────────────────────────────────────


class TestSettings:
    def test_defaults(self):
        svc = SpeechService()

        assert svc.model == "whisper-large-v3-turbo"
        assert svc.max_audio_bytes == 10 * 1024 * 1024

    def test_model_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("VOICE_STT_MODEL", "whisper-large-v3")

        assert SpeechService().model == "whisper-large-v3"

    def test_a_blank_model_keeps_the_default(self, monkeypatch):
        monkeypatch.setenv("VOICE_STT_MODEL", "   ")

        assert SpeechService().model == "whisper-large-v3-turbo"

    def test_max_audio_bytes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("VOICE_MAX_AUDIO_BYTES", "2048")

        assert SpeechService().max_audio_bytes == 2048

    @pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5"])
    def test_a_bad_size_is_ignored(self, monkeypatch, caplog, value):
        monkeypatch.setenv("VOICE_MAX_AUDIO_BYTES", value)

        with caplog.at_level(logging.WARNING):
            svc = SpeechService()

        assert svc.max_audio_bytes == 10 * 1024 * 1024
        assert "VOICE_MAX_AUDIO_BYTES" in caplog.text

    def test_constructor_takes_no_arguments(self):
        # Routes use Depends(SpeechService); an argument would become a query field.
        assert list(inspect.signature(SpeechService.__init__).parameters) == ["self"]


# ── A good answer ─────────────────────────────────────────────────────────────


class TestTranscribe:
    async def test_returns_the_trimmed_text(self, service):
        transcript = await say(service)

        assert isinstance(transcript, Transcript)
        assert transcript.text == SENTENCE

    async def test_audio_length_is_the_end_of_the_last_segment(self, service, groq):
        groq.body = reply(
            segments=[
                segment("a", 0.0, 3.1),
                segment("b", 3.1, 7.4),
                segment("c", 7.4, 5.0),
            ]
        )

        transcript = await say(service, declared_seconds=5)

        assert transcript.audio_seconds == 7.4
        assert transcript.segments == 3

    async def test_a_recording_that_ends_in_silence_counts_in_full(self, service, groq):
        # Real Groq output: a 7.6 s clip had one segment, ending at 0.5 s. Groq bills
        # the whole recording, so the declared length wins when it is longer.
        groq.body = reply("hello", segments=[segment("hello", 0, 0.5)])

        assert (await say(service, declared_seconds=7.6)).audio_seconds == 7.6

    @pytest.mark.parametrize(
        "declared", [None, 0, -1, float("nan"), float("inf"), True, "7", [3]]
    )
    async def test_a_declared_length_that_is_not_sensible_is_ignored(
        self, service, groq, declared
    ):
        groq.body = reply("hello", segments=[segment("hello", 0, 4.5)])

        assert (await say(service, declared_seconds=declared)).audio_seconds == 4.5

    async def test_falls_back_to_the_declared_length(self, service, groq):
        groq.body = {"text": "hello there", "segments": []}

        assert (await say(service, declared_seconds=12.5)).audio_seconds == 12.5

    async def test_length_is_unknown_without_segments_or_a_declared_length(
        self, service, groq
    ):
        groq.body = {"text": "hello there"}

        assert (await say(service)).audio_seconds is None

    async def test_segment_ends_that_are_not_numbers_are_ignored(self, service, groq):
        groq.body = reply(
            segments=[
                segment("a", end="soon"),
                segment("b", end=4.5),
                segment("c", end=None),
            ]
        )

        assert (await say(service)).audio_seconds == 4.5

    async def test_latency_is_how_long_groq_took(self, service):
        assert (await say(service)).latency_s == 0.25

    async def test_makes_exactly_one_request(self, service, groq):
        await say(service)

        assert len(groq.requests) == 1


class TestRequest:
    async def test_goes_to_the_transcription_endpoint_with_the_key(self, service, groq):
        await say(service)

        request = groq.requests[0]
        assert str(request.url) == ss.STT_URL
        assert request.method == "POST"
        assert request.headers["authorization"] == f"Bearer {KEY}"

    async def test_sends_the_settings(self, service, groq):
        await say(service)

        body = groq.requests[0].content.decode("latin-1")
        for name, value in {
            "model": "whisper-large-v3-turbo",
            "language": "en",
            "temperature": "0",
            "response_format": "verbose_json",
        }.items():
            assert re.search(rf'name="{name}"\r\n\r\n{value}\r\n', body), name

    async def test_sends_the_audio_unchanged(self, service, groq):
        await say(service)

        assert AUDIO in groq.requests[0].content

    async def test_uses_the_configured_model(self, service, groq):
        service.model = "whisper-large-v3"

        await say(service)

        assert b"whisper-large-v3\r\n" in groq.requests[0].content

    @pytest.mark.parametrize(
        ("content_type", "extension"),
        [
            ("audio/webm", "webm"),
            ("audio/webm;codecs=opus", "webm"),
            ("audio/webm; codecs=opus", "webm"),
            ("AUDIO/WEBM", "webm"),
            ("audio/ogg;codecs=opus", "ogg"),
            ("audio/mp4", "m4a"),
            ("audio/x-m4a", "m4a"),
            ("audio/mpeg", "mp3"),
            ("audio/wav", "wav"),
            ("audio/x-wav", "wav"),
            ("audio/flac", "flac"),
        ],
    )
    async def test_the_file_name_matches_the_content_type(
        self, service, groq, content_type, extension
    ):
        await say(service, content_type=content_type)

        body = groq.requests[0].content.decode("latin-1")
        assert f'filename="answer.{extension}"' in body

    async def test_the_part_content_type_has_no_codec_suffix(self, service, groq):
        await say(service, content_type="audio/webm;codecs=opus")

        body = groq.requests[0].content.decode("latin-1")
        assert "Content-Type: audio/webm\r\n" in body
        assert "codecs" not in body


# ── Refused before any call ──────────────────────────────────────────────────


class TestRefusedLocally:
    async def test_empty_audio(self, service, groq):
        with pytest.raises(SpeechAudioRejected) as caught:
            await say(service, audio=b"")

        assert caught.value.status == 400
        assert groq.requests == []

    async def test_audio_that_is_too_large(self, service, groq):
        service.max_audio_bytes = 100

        with pytest.raises(SpeechAudioRejected) as caught:
            await say(service, audio=b"x" * 101)

        assert caught.value.status == 413
        assert "too long" in str(caught.value)
        assert groq.requests == []

    async def test_audio_exactly_at_the_limit_is_accepted(self, service, groq):
        service.max_audio_bytes = 100

        await say(service, audio=b"x" * 100)

        assert len(groq.requests) == 1

    @pytest.mark.parametrize(
        "content_type",
        ["text/plain", "video/mp4", "application/json", "", "audio/aiff"],
    )
    async def test_unsupported_types(self, service, groq, content_type):
        with pytest.raises(SpeechAudioRejected) as caught:
            await say(service, content_type=content_type)

        assert caught.value.status == 415
        assert groq.requests == []

    async def test_a_missing_content_type(self, service, groq):
        with pytest.raises(SpeechAudioRejected):
            await say(service, content_type=None)

        assert groq.requests == []

    @pytest.mark.parametrize("value", ["", "   "])
    async def test_no_api_key(self, service, groq, monkeypatch, value):
        monkeypatch.setenv("GROQ_API_KEY", value)

        with pytest.raises(SpeechNotConfigured):
            await say(service)

        assert groq.requests == []

    async def test_missing_api_key_variable(self, service, groq, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY")

        with pytest.raises(SpeechNotConfigured) as caught:
            await say(service)

        assert "GROQ_API_KEY" in caught.value.detail
        assert "GROQ_API_KEY" not in str(caught.value)


# ── Groq errors ───────────────────────────────────────────────────────────────


class TestGroqErrors:
    async def test_429_carries_retry_after_and_is_not_retried(self, service, groq):
        groq.error(429, "Rate limit reached", **{"retry-after": "9"})

        with pytest.raises(SpeechRateLimited) as caught:
            await say(service)

        assert caught.value.retry_after == 9.0
        assert caught.value.status == 429
        assert len(groq.requests) == 1

    @pytest.mark.parametrize("value", [None, "soon", "-3", "nan", ""])
    async def test_429_without_a_usable_retry_after(self, service, groq, value):
        headers = {} if value is None else {"retry-after": value}
        groq.error(429, "Rate limit reached", **headers)

        with pytest.raises(SpeechRateLimited) as caught:
            await say(service)

        assert caught.value.retry_after is None

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_rejected_key(self, service, groq, status):
        groq.error(status, "Invalid API Key")

        with pytest.raises(SpeechNotConfigured) as caught:
            await say(service)

        assert "Invalid API Key" in caught.value.detail
        assert "Invalid API Key" not in str(caught.value)
        assert caught.value.status == 503

    @pytest.mark.parametrize(
        ("groq_status", "expected"), [(400, 400), (413, 413), (415, 415), (422, 400)]
    )
    async def test_audio_groq_cannot_read(self, service, groq, groq_status, expected):
        groq.error(groq_status, "could not process file")

        with pytest.raises(SpeechAudioRejected) as caught:
            await say(service)

        assert caught.value.status == expected
        assert "could not process file" in caught.value.detail

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 418])
    async def test_server_errors_are_unavailable_and_not_retried(
        self, service, groq, status
    ):
        groq.error(status, "oops")

        with pytest.raises(SpeechUnavailable) as caught:
            await say(service)

        assert caught.value.status == 503
        assert f"HTTP {status}" in caught.value.detail
        assert len(groq.requests) == 1

    async def test_timeout(self, service, groq):
        groq.raises = httpx.ReadTimeout("slow")

        with pytest.raises(SpeechUnavailable) as caught:
            await say(service)

        assert caught.value.detail == "timed out"
        assert len(groq.requests) == 1

    async def test_network_error(self, service, groq):
        groq.raises = httpx.ConnectError("down")

        with pytest.raises(SpeechUnavailable) as caught:
            await say(service)

        assert caught.value.detail == "ConnectError"

    @pytest.mark.parametrize("raw", [b"not json", b"", b"[1, 2]", b'"text"'])
    async def test_an_unreadable_reply(self, service, groq, raw):
        groq.raw = raw

        with pytest.raises(SpeechUnavailable):
            await say(service)

    async def test_an_error_body_that_is_not_json(self, service, groq):
        groq.status = 502
        groq.raw = b"<html>Bad gateway</html>"

        with pytest.raises(SpeechUnavailable) as caught:
            await say(service)

        assert "Bad gateway" in caught.value.detail


class TestSecrets:
    @pytest.mark.parametrize("status", [401, 400, 429, 500])
    async def test_a_key_echoed_by_groq_is_removed(self, service, groq, status):
        groq.error(status, f"Invalid API Key {KEY}")

        with pytest.raises(ss.SpeechError) as caught:
            await say(service)

        for text in (str(caught.value), caught.value.detail, repr(caught.value)):
            assert KEY not in text
        assert "[redacted]" in caught.value.detail

    async def test_messages_never_contain_the_audio(self, service, groq):
        groq.error(400, "bad file")

        with pytest.raises(ss.SpeechError) as caught:
            await say(service)

        assert AUDIO.decode() not in str(caught.value) + caught.value.detail

    async def test_the_log_has_no_transcript_audio_or_key(self, service, caplog):
        with caplog.at_level(logging.DEBUG):
            await say(service)

        assert "Speech to text" in caplog.text
        assert SENTENCE not in caplog.text
        assert KEY not in caplog.text
        assert AUDIO.decode() not in caplog.text

    async def test_the_key_is_read_at_call_time(self, service, groq, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_other_key")

        await say(service)

        assert groq.requests[0].headers["authorization"] == "Bearer gsk_other_key"


# ── Silence and made-up answers ──────────────────────────────────────────────


class TestNoSpeech:
    @pytest.mark.parametrize("text", ["", "   ", ".", " . ", "...", "?!", "♪"])
    async def test_no_words(self, service, groq, text):
        groq.body = {"text": text, "segments": [segment(text)]}

        with pytest.raises(NoSpeechDetected) as caught:
            await say(service)

        assert caught.value.status == 422
        assert "could not hear" in str(caught.value)

    async def test_a_missing_text(self, service, groq):
        groq.body = {"segments": []}

        with pytest.raises(NoSpeechDetected):
            await say(service)

    async def test_every_segment_is_silence(self, service, groq):
        groq.body = reply(
            "you", segments=[segment("you", **SILENT), segment("you", 2, 4, **SILENT)]
        )

        with pytest.raises(NoSpeechDetected) as caught:
            await say(service)

        assert caught.value.detail == "silent segments"

    async def test_one_confident_segment_saves_the_answer(self, service, groq):
        groq.body = reply(
            "I would check the logs first",
            segments=[segment("hm", **SILENT), segment("I would check the logs first")],
        )

        assert (await say(service)).text == "I would check the logs first"

    @pytest.mark.parametrize(
        "stats",
        [
            {
                "no_speech_prob": 0.95,
                "avg_logprob": -0.3,
            },  # unsure it is speech, but sure of the words
            {"no_speech_prob": 0.4, "avg_logprob": -1.8},
            {"no_speech_prob": 0.6, "avg_logprob": -1.0},  # exactly on the thresholds
        ],
    )
    async def test_only_clearly_silent_segments_count_as_silence(
        self, service, groq, stats
    ):
        groq.body = reply("I would add an index", segments=[segment("x", **stats)])

        assert (await say(service)).text == "I would add an index"

    async def test_segments_without_numbers_are_not_called_silent(self, service, groq):
        groq.body = reply("I would add an index", segments=[{"text": "x", "end": 1}])

        assert (await say(service)).text == "I would add an index"

    @pytest.mark.parametrize(
        "text", ["you", "You.", "Thank you.", "thank you!", "Bye."]
    )
    async def test_a_filler_answer_with_an_unsure_segment(self, service, groq, text):
        groq.body = {
            "text": text,
            "segments": [segment(text, no_speech_prob=0.4, avg_logprob=-0.5)],
        }

        with pytest.raises(NoSpeechDetected) as caught:
            await say(service)

        assert caught.value.detail == "filler answer"

    async def test_a_filler_answer_with_a_low_confidence_score(self, service, groq):
        groq.body = {
            "text": " you",
            "segments": [segment("you", no_speech_prob=0.0, avg_logprob=-0.9)],
        }

        with pytest.raises(NoSpeechDetected):
            await say(service)

    async def test_a_filler_answer_without_any_segment_information(self, service, groq):
        groq.body = {"text": " you"}

        with pytest.raises(NoSpeechDetected):
            await say(service)

    async def test_the_filler_seen_in_the_first_real_runs(self, service, groq):
        groq.body = {
            "text": " Thank you.",
            "segments": [
                segment("Thank you.", 0, 7.2, no_speech_prob=0.8, avg_logprob=-1.4)
            ],
        }

        with pytest.raises(NoSpeechDetected):
            await say(service)

    @pytest.mark.parametrize("text", ["Thank you.", "you", "Thank you for watching"])
    async def test_a_confident_short_answer_is_kept(self, service, groq, text):
        groq.body = {"text": text, "segments": [segment(text, **GOOD)]}

        assert (await say(service)).text == text

    async def test_filler_words_inside_a_real_answer_are_kept(self, service, groq):
        text = "Thank you, that is a good question. I would start with the logs."
        groq.body = {
            "text": text,
            "segments": [segment(text, no_speech_prob=0.5, avg_logprob=-0.9)],
        }

        assert (await say(service)).text == text

    async def test_the_reason_is_not_leaked_to_the_candidate(self, service, groq):
        groq.body = {"text": ".", "segments": []}

        with pytest.raises(NoSpeechDetected) as caught:
            await say(service)

        assert str(caught.value) == ss._SAFE_MESSAGES["no_speech"]
        assert caught.value.detail == "no words"


# ── Error types ──────────────────────────────────────────────────────────────


class TestNoSpeechCarriesTheAudioLength:
    """Groq processed the audio, so quota was used: the endpoint adjusts, not releases."""

    async def test_with_a_declared_length(self, service, groq):
        groq.body = {"text": ".", "segments": []}

        with pytest.raises(NoSpeechDetected) as caught:
            await say(service, declared_seconds=7.6)

        assert caught.value.audio_seconds == 7.6

    async def test_a_filler_answer_on_a_mostly_silent_recording(self, service, groq):
        groq.body = {
            "text": " you",
            "segments": [segment("you", 0, 0.5, no_speech_prob=0, avg_logprob=-1.478)],
        }

        with pytest.raises(NoSpeechDetected) as caught:
            await say(service, declared_seconds=7.6)

        assert caught.value.detail == "filler answer"
        assert caught.value.audio_seconds == 7.6

    async def test_without_a_declared_length_it_uses_the_segments(self, service, groq):
        groq.body = {
            "text": " you",
            "segments": [segment("you", 0, 0.5, no_speech_prob=0, avg_logprob=-1.478)],
        }

        with pytest.raises(NoSpeechDetected) as caught:
            await say(service)

        assert caught.value.audio_seconds == 0.5

    async def test_silent_segments_carry_it_too(self, service, groq):
        groq.body = reply("you", segments=[segment("you", 0, 3, **SILENT)])

        with pytest.raises(NoSpeechDetected) as caught:
            await say(service, declared_seconds=4)

        assert caught.value.audio_seconds == 4

    async def test_unknown_when_nothing_is_known(self, service, groq):
        groq.body = {"text": ""}

        with pytest.raises(NoSpeechDetected) as caught:
            await say(service)

        assert caught.value.audio_seconds is None

    def test_the_error_can_be_made_without_a_length(self):
        assert NoSpeechDetected("m").audio_seconds is None


class TestMeasuredOnGroq:
    """The four clips of 2026-11-26, as Groq answered (whisper-large-v3-turbo).

    Groq reported no_speech_prob 0 for every clip, silent ones included, so only
    avg_logprob tells good speech (-0.04, -0.08) from silence (-1.04, -1.48).
    """

    CLIP_1 = (
        "Tell me about a time you had to debug a production issue under pressure. "
        "What did you do first, and how did you decide what to try next?"
    )
    CLIP_2 = (
        "Walk me through how you would design a rate limiter for a public API. "
        "Start with the requirements you would ask about before drawing any boxes."
    )

    async def test_clip_1_is_kept(self, service, groq):
        groq.body = {
            "text": f" {self.CLIP_1}",
            "segments": [
                segment("a", 0, 3.92, no_speech_prob=0, avg_logprob=-0.08398107),
                segment("b", 3.92, 7.2, no_speech_prob=0, avg_logprob=-0.08398107),
            ],
        }

        transcript = await say(service, declared_seconds=7.2)

        assert transcript.text == self.CLIP_1
        assert transcript.audio_seconds == 7.2

    async def test_clip_2_is_kept(self, service, groq):
        groq.body = {
            "text": f" {self.CLIP_2}",
            "segments": [
                segment("a", 0, 4.32, no_speech_prob=0, avg_logprob=-0.043951657),
                segment("b", 4.32, 8.08, no_speech_prob=0, avg_logprob=-0.043951657),
            ],
        }

        assert (await say(service)).audio_seconds == 8.08

    async def test_clip_3_is_refused_as_no_words(self, service, groq):
        groq.body = {
            "text": " .",
            "segments": [segment(".", 0, 7, no_speech_prob=0, avg_logprob=-1.0373122)],
        }

        with pytest.raises(NoSpeechDetected) as caught:
            await say(service, declared_seconds=7.2)

        assert caught.value.detail == "no words"

    async def test_clip_4_is_refused_as_a_filler_answer(self, service, groq):
        groq.body = {
            "text": " you",
            "segments": [
                segment("you", 0, 0.5, no_speech_prob=0, avg_logprob=-1.4782548)
            ],
        }

        with pytest.raises(NoSpeechDetected) as caught:
            await say(service, declared_seconds=7.6)

        assert caught.value.detail == "filler answer"


class TestErrorTypes:
    @pytest.mark.parametrize(
        ("error", "code", "status"),
        [
            (SpeechNotConfigured("m"), "not_configured", 503),
            (SpeechAudioRejected("m"), "audio_rejected", 400),
            (SpeechRateLimited("m"), "rate_limited", 429),
            (SpeechUnavailable("m"), "unavailable", 503),
            (NoSpeechDetected("m"), "no_speech", 422),
        ],
    )
    def test_code_and_status(self, error, code, status):
        assert isinstance(error, ss.SpeechError)
        assert (error.code, error.status) == (code, status)

    def test_a_status_can_be_set_per_error(self):
        assert SpeechAudioRejected("m", status=413).status == 413

    def test_every_safe_message_points_to_typing_or_retrying(self):
        for message in ss._SAFE_MESSAGES.values():
            assert "type" in message.lower() or "try again" in message.lower()


# ── The shared client ────────────────────────────────────────────────────────


class TestSharedClient:
    async def test_it_is_created_once_and_closed_on_request(self, monkeypatch):
        monkeypatch.setattr(ss, "_client", None)

        first = ss._shared_client()
        assert ss._shared_client() is first

        await ss.close_speech_client()

        assert first.is_closed
        assert ss._client is None
        await ss.close_speech_client()  # closing twice is fine

    async def test_a_service_without_its_own_client_uses_the_shared_one(
        self, monkeypatch, groq
    ):
        shared = httpx.AsyncClient(transport=httpx.MockTransport(groq))
        monkeypatch.setattr(ss, "_client", shared)

        await SpeechService().transcribe(AUDIO, "audio/webm")

        assert len(groq.requests) == 1
        await ss.close_speech_client()
