"""SpeechService — turns a recorded answer into text with Groq Whisper.

Phase 8, task 3. Task 4 adds the endpoint that calls this and combines it with the
quota guard (VoiceQuotaService). This module only talks to Groq.

    transcript = await SpeechService().transcribe(audio_bytes, "audio/webm;codecs=opus")
    transcript.text, transcript.audio_seconds

Design (see docs/architecture/voice.md):

- Plain httpx, one request, **no retries**. A retry would spend quota behind the
  guard's back. A 429 is reported with Groq's `retry-after`, so the guard can start
  its cool-down (`VoiceQuotaService.record_rate_limited`).
- `verbose_json` is requested because it carries, per segment, `no_speech_prob` and
  `avg_logprob`. The first real runs showed Whisper answering "you" or "." on nearly
  silent audio; such results are raised as `NoSpeechDetected` and never shown as an
  answer. Measured on Groq (2026-11-26): `no_speech_prob` was 0 even for silent
  clips, so on whisper-large-v3-turbo the usable signal is `avg_logprob` (about
  -0.05 to -0.1 for good speech, -1.0 and -1.5 for the two silent clips).
- The call used Groq quota even when it ends as `NoSpeechDetected`. That error
  carries `audio_seconds`, so the endpoint can adjust the reservation and not
  release it.
- The file is sent with an extension that matches the browser's content type
  (webm, ogg, m4a, ...), because Groq's documentation does not say whether it
  detects the format from the name or from the content.
- Empty, too large or unsupported audio is refused before any call is made.
- Errors have one type per cause, a short message that is safe to show a candidate
  (`str(error)`), and a `detail` for logs. Neither contains the API key or audio.
- Privacy: audio stays in memory and is dropped after the call. Nothing here logs
  audio or transcripts, only sizes, status codes and timing. This is not an LLM
  call and is not traced in LangSmith.

NOTE: __init__ must stay argument-free — routes use Depends(SpeechService), and
FastAPI would turn any extra parameter into a query-string field. Tests set the
`http`, `model`, `max_audio_bytes` and `clock` attributes instead.
"""

import logging
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_MODEL = "whisper-large-v3-turbo"
DEFAULT_MAX_AUDIO_BYTES = 10 * 1024 * 1024  # Groq's free-plan limit is 25 MB
TIMEOUT_SECONDS = 30.0
LANGUAGE = "en"  # English only in the first release

# Browser content type -> file extension for the upload.
EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}

# Whisper's own "this segment is silence" rule.
SILENT_NO_SPEECH_PROB = 0.6
SILENT_AVG_LOGPROB = -1.0

# Answers Whisper invents for silence ("you", "Thank you."). A short answer like this
# is dropped only when the segment is not confident speech.
FILLER_ANSWERS = frozenset(
    {"you", "thank you", "thanks for watching", "thank you for watching", "bye"}
)
FILLER_NO_SPEECH_PROB = 0.3
FILLER_AVG_LOGPROB = -0.8

_SAFE_MESSAGES = {
    "not_configured": "Voice is not available right now. You can type your answer instead.",
    "unavailable": "Voice is not available right now. You can type your answer instead.",
    "rate_limited": "Voice is busy right now. Try again in a minute, or type your answer.",
    "no_speech": "We could not hear anything. Please try again, or type your answer.",
}


# ── Errors ────────────────────────────────────────────────────────────────────


class SpeechError(Exception):
    """Base class. `str(error)` is safe to show; `detail` is for logs only."""

    code = "speech_error"
    status = 503

    def __init__(self, message: str, *, detail: str = "", status: int | None = None):
        super().__init__(message)
        self.detail = detail
        if status is not None:
            self.status = status


class SpeechNotConfigured(SpeechError):
    """No API key, or Groq rejected it."""

    code = "not_configured"
    status = 503


class SpeechAudioRejected(SpeechError):
    """The audio is empty, too large, unsupported or unreadable (400, 413 or 415)."""

    code = "audio_rejected"
    status = 400


class SpeechRateLimited(SpeechError):
    """Groq answered 429. `retry_after` is in seconds, or None when Groq did not say."""

    code = "rate_limited"
    status = 429

    def __init__(
        self, message: str, *, retry_after: float | None = None, detail: str = ""
    ):
        super().__init__(message, detail=detail)
        self.retry_after = retry_after


class SpeechUnavailable(SpeechError):
    """Timeout, network failure, a server error or an unreadable reply."""

    code = "unavailable"
    status = 503


class NoSpeechDetected(SpeechError):
    """The audio held no speech (silence, noise, or a made-up filler answer).

    Groq did process the audio, so quota was used: `audio_seconds` is what to adjust
    the reservation to (the call must not be released).
    """

    code = "no_speech"
    status = 422

    def __init__(
        self, message: str, *, detail: str = "", audio_seconds: float | None = None
    ):
        super().__init__(message, detail=detail)
        self.audio_seconds = audio_seconds


# ── Result ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Transcript:
    text: str
    audio_seconds: (
        float | None
    )  # the longer of the last segment's end and the declared length
    latency_s: float  # how long Groq took
    segments: int


# ── Helpers ───────────────────────────────────────────────────────────────────


def _int_setting(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        value = 0
    if value <= 0:
        logger.warning("Ignoring %s: it must be a whole number above 0", name)
        return default
    return value


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]") if secret else text


def _error_text(response: httpx.Response, secret: str) -> str:
    """Groq's error message, short and without the key (for logs only)."""
    try:
        body = response.json()
        message = body["error"]["message"] if isinstance(body, dict) else str(body)
    except Exception:
        message = response.text
    return _redact(" ".join(str(message).split()), secret)[:200]


def _retry_after(response: httpx.Response) -> float | None:
    try:
        value = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _declared(value: object) -> float | None:
    """The length the browser reported, if it is a sensible number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def _has_words(text: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9]", text))


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def _number(segment: dict, name: str) -> float | None:
    value = segment.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _looks_silent(segment: dict) -> bool:
    no_speech = _number(segment, "no_speech_prob")
    logprob = _number(segment, "avg_logprob")
    return (
        no_speech is not None
        and logprob is not None
        and no_speech > SILENT_NO_SPEECH_PROB
        and logprob < SILENT_AVG_LOGPROB
    )


def _looks_unsure(segment: dict) -> bool:
    no_speech = _number(segment, "no_speech_prob")
    logprob = _number(segment, "avg_logprob")
    return (no_speech is not None and no_speech >= FILLER_NO_SPEECH_PROB) or (
        logprob is not None and logprob <= FILLER_AVG_LOGPROB
    )


_client: httpx.AsyncClient | None = None


def _shared_client() -> httpx.AsyncClient:
    """One connection pool for all requests (a new service is made per request)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    return _client


async def close_speech_client() -> None:
    """Close the shared client (call on application shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ── The service ───────────────────────────────────────────────────────────────


class SpeechService:
    def __init__(self):
        self.model = (os.getenv("VOICE_STT_MODEL") or "").strip() or DEFAULT_MODEL
        self.max_audio_bytes = _int_setting(
            "VOICE_MAX_AUDIO_BYTES", DEFAULT_MAX_AUDIO_BYTES
        )
        self.timeout = TIMEOUT_SECONDS
        self.clock: Callable[[], float] = time.perf_counter
        self.http: httpx.AsyncClient | None = None

    async def transcribe(
        self,
        audio: bytes,
        content_type: str,
        *,
        declared_seconds: float | None = None,
    ) -> Transcript:
        """Send one recording to Whisper and return the text.

        `declared_seconds` is the length the browser reported (the endpoint caps it at
        the longest allowed recording). The audio length returned is the longer of it
        and the end of Groq's last segment.
        """
        base_type, extension = self._check_audio(audio, content_type)
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            raise SpeechNotConfigured(
                _SAFE_MESSAGES["not_configured"], detail="GROQ_API_KEY is not set"
            )

        client = self.http or _shared_client()
        started = self.clock()
        try:
            response = await client.post(
                STT_URL,
                headers={"Authorization": f"Bearer {key}"},
                data={
                    "model": self.model,
                    "language": LANGUAGE,
                    "temperature": "0",
                    "response_format": "verbose_json",
                },
                files={"file": (f"answer.{extension}", audio, base_type)},
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise SpeechUnavailable(
                _SAFE_MESSAGES["unavailable"], detail="timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise SpeechUnavailable(
                _SAFE_MESSAGES["unavailable"], detail=type(exc).__name__
            ) from exc
        latency = self.clock() - started

        logger.info(
            "Speech to text: %d bytes, status %d, %.2fs",
            len(audio),
            response.status_code,
            latency,
        )
        if response.status_code != 200:
            raise self._error_for(response, key)
        return self._to_transcript(response, latency, declared_seconds)

    # ── Internals ──────────────────────────────────────────────────────────

    def _check_audio(self, audio: bytes, content_type: str) -> tuple[str, str]:
        if not audio:
            raise SpeechAudioRejected("No audio was received. Please try again.")
        if len(audio) > self.max_audio_bytes:
            raise SpeechAudioRejected(
                "That recording is too long. Please keep your answer shorter.",
                status=413,
            )
        base_type = (content_type or "").split(";")[0].strip().lower()
        extension = EXTENSIONS.get(base_type)
        if extension is None:
            raise SpeechAudioRejected(
                "This browser's recording format is not supported. "
                "Please type your answer.",
                status=415,
            )
        return base_type, extension

    def _error_for(self, response: httpx.Response, key: str) -> SpeechError:
        status = response.status_code
        detail = f"HTTP {status}: {_error_text(response, key)}"
        if status == 429:
            return SpeechRateLimited(
                _SAFE_MESSAGES["rate_limited"],
                retry_after=_retry_after(response),
                detail=detail,
            )
        if status in (401, 403):
            return SpeechNotConfigured(_SAFE_MESSAGES["not_configured"], detail=detail)
        if status in (400, 413, 415, 422):
            return SpeechAudioRejected(
                "That recording could not be read. Please try again, "
                "or type your answer.",
                detail=detail,
                status=status if status in (413, 415) else 400,
            )
        return SpeechUnavailable(_SAFE_MESSAGES["unavailable"], detail=detail)

    def _to_transcript(
        self,
        response: httpx.Response,
        latency: float,
        declared_seconds: float | None,
    ) -> Transcript:
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise SpeechUnavailable(
                _SAFE_MESSAGES["unavailable"], detail="unreadable reply"
            )

        text = str(body.get("text") or "").strip()
        raw_segments = body.get("segments")
        segments = (
            [s for s in raw_segments if isinstance(s, dict)]
            if isinstance(raw_segments, list)
            else []
        )

        # The segments end where the speech ends, so a recording that ends in silence
        # is longer than its last segment (a 7.6 s clip had one segment ending at
        # 0.5 s). Groq bills the whole recording, so take the longer of the two.
        ends = [e for e in (_number(s, "end") for s in segments) if e is not None]
        declared = _declared(declared_seconds)
        known = ends + ([declared] if declared is not None else [])
        seconds = max(known) if known else None

        if not _has_words(text):
            raise NoSpeechDetected(
                _SAFE_MESSAGES["no_speech"], detail="no words", audio_seconds=seconds
            )
        if segments and all(_looks_silent(s) for s in segments):
            raise NoSpeechDetected(
                _SAFE_MESSAGES["no_speech"],
                detail="silent segments",
                audio_seconds=seconds,
            )
        if _normal(text) in FILLER_ANSWERS and (
            not segments or any(_looks_unsure(s) for s in segments)
        ):
            raise NoSpeechDetected(
                _SAFE_MESSAGES["no_speech"],
                detail="filler answer",
                audio_seconds=seconds,
            )

        return Transcript(
            text=text,
            audio_seconds=seconds,
            latency_s=round(latency, 3),
            segments=len(segments),
        )
