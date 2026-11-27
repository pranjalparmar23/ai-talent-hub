"""TtsService — turns text into speech with Groq Orpheus.

Phase 8, task 5. Step 3 adds the speak flow (cache, quota guard) around this; this
module only talks to Groq and checks what comes back.

    speech = await TtsService().synthesize("Tell me about a time you led a project.")
    speech.clip.to_bytes()        # a clean WAV file
    speech.characters             # what the call cost (one character is one token)

Design (see docs/architecture/voice.md):

- Plain httpx, one request per chunk, **no retries**. A retry would spend the 3,600
  daily tokens behind the quota guard's back. A 429 is raised with Groq's
  `retry-after` so the guard can start its cool-down.
- Orpheus accepts at most 200 characters per request, so the text is cleaned of
  markup and split at sentence ends (then clause marks, then spaces) into chunks of
  at most 200 characters. The chunks are joined into one clip with a short pause.
- **Every clip is checked for sound.** About half of the measured clips were nearly
  silent although their length, header and cost looked normal. A silent clip raises
  `SilentAudio`; it is never returned, so it cannot be cached or played.
- Charges. One character is one token, and a clip that comes back (even a silent
  one) has been charged. Every error raised by `synthesize` therefore carries
  `characters_used`: the characters of the chunks Groq answered with 200 so far. The
  caller adjusts its quota reservation to that number, and releases it only when it
  is 0. `used_by(error)` reads it.
- Errors have one type per cause, a short message that is safe to show (`str(error)`)
  and a `detail` for logs. Neither contains the API key or the text. The text of the
  question is never logged, only counts, status codes and timing.
- Groq returns a streaming WAV header with a placeholder length; `wav_audio` reads
  the real length from the bytes and writes a correct header.

NOTE: __init__ must stay argument-free — routes use Depends on services, and FastAPI
would turn any extra parameter into a query-string field. Tests set the `http`,
`model`, `voice`, `max_chars` and `clock` attributes instead.
"""

import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.services.speech_service import (
    SpeechError,
    SpeechNotConfigured,
    SpeechRateLimited,
    SpeechUnavailable,
    _error_text,
    _retry_after,
)
from app.services.wav_audio import WavClip, WavError, join_clips, parse_wav

logger = logging.getLogger(__name__)

TTS_URL = "https://api.groq.com/openai/v1/audio/speech"
DEFAULT_MODEL = "canopylabs/orpheus-v1-english"
DEFAULT_VOICE = "daniel"
MAX_CHUNK_CHARS = 200  # Orpheus input limit per request (Groq docs, 2026-11-25)
MAX_TEXT_CHARS = 600  # the organisation's per-minute cap; longer text is refused
TIMEOUT_SECONDS = 60.0

_SAFE_MESSAGES = {
    "not_configured": "Spoken questions are not available right now. "
    "You can read the question on the screen.",
    "unavailable": "Spoken questions are not available right now. "
    "You can read the question on the screen.",
    "rate_limited": "Spoken questions are busy right now. "
    "You can read the question on the screen.",
    "silent_audio": "The spoken version did not come out right. "
    "You can read the question on the screen.",
    "text_rejected": "That question cannot be read aloud. "
    "You can read it on the screen.",
}


# ── Errors ────────────────────────────────────────────────────────────────────
# Rate limits, a missing key and an unreachable service are the same causes as in
# speech to text, so those error types are reused (and handled the same way).


class TextRejected(SpeechError):
    """The text is empty after cleaning, too long, or refused by Groq (400 or 413)."""

    code = "text_rejected"
    status = 400


class SilentAudio(SpeechError):
    """Groq answered, but the clip has almost no sound. It has been charged."""

    code = "silent_audio"
    status = 502


def used_by(error: BaseException) -> int:
    """Characters Groq has already charged for before `error` was raised."""
    return int(getattr(error, "characters_used", 0) or 0)


# ── Result ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Speech:
    clip: WavClip
    characters: int  # what Groq charged: the characters of all chunks
    chunks: int  # how many requests were made
    latency_s: float


# ── Preparing the text ────────────────────────────────────────────────────────

_URL = re.compile(r"https?://\S+")
_EMPHASIS = re.compile(r"[*`]+")
_HEADING_OR_BULLET = re.compile(r"(?m)^\s*(?:#{1,6}|[-•>])\s+")
_TAG = re.compile(r"<[A-Za-z][A-Za-z ]{0,20}>")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE = re.compile(r"(?<=[,;:])\s+")
_WORD = re.compile(r"\s+")
_SEPARATORS = (_SENTENCE, _CLAUSE, _WORD)


def clean_text(text: str) -> str:
    """Text as it should be spoken: no markup, links or tags, one line of words.

    Square brackets are removed because Orpheus reads `[cheerful]` as a spoken
    direction (as far as we know), and a question must not be able to carry one.
    """
    text = _URL.sub("the link", text or "")
    text = _TAG.sub(" ", text)
    text = _HEADING_OR_BULLET.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = text.replace("_", " ").replace("[", " ").replace("]", " ")
    return " ".join(text.split())


def split_text(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text into chunks of at most `limit` characters.

    Sentences are kept together as far as the limit allows. A sentence that is too
    long is cut at clause marks, then at spaces, and a word longer than the limit is
    cut where it stands. Nothing is dropped: apart from spaces, the chunks add up to
    the text.
    """
    text = " ".join(text.split())
    return _split(text, limit, 0) if text else []


def _split(text: str, limit: int, level: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    if level == len(_SEPARATORS):
        return [text[i : i + limit] for i in range(0, len(text), limit)]
    pieces: list[str] = []
    for part in _SEPARATORS[level].split(text):
        if part:
            pieces.extend(_split(part, limit, level + 1))
    return _pack(pieces, limit)


def _pack(pieces: list[str], limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + 1 + len(piece) <= limit:
            current = f"{current} {piece}"
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


# ── Shared client ─────────────────────────────────────────────────────────────

_client: httpx.AsyncClient | None = None


def _shared_client() -> httpx.AsyncClient:
    """One connection pool for all requests (a new service is made per request)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    return _client


async def close_tts_client() -> None:
    """Close the shared client (call on application shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ── The service ───────────────────────────────────────────────────────────────


class TtsService:
    def __init__(self):
        self.model = (os.getenv("VOICE_TTS_MODEL") or "").strip() or DEFAULT_MODEL
        self.voice = (os.getenv("VOICE_TTS_VOICE") or "").strip() or DEFAULT_VOICE
        self.max_chars = MAX_TEXT_CHARS
        self.timeout = TIMEOUT_SECONDS
        self.clock: Callable[[], float] = time.perf_counter
        self.http: httpx.AsyncClient | None = None

    def prepare(self, text: str) -> list[str]:
        """The chunks that `synthesize` would send. Nothing is sent. The total length
        is what the call will cost, so the caller reserves quota with it."""
        spoken = clean_text(text)
        if not any(character.isalnum() for character in spoken):
            raise TextRejected(
                _SAFE_MESSAGES["text_rejected"], detail="nothing to read aloud"
            )
        if len(spoken) > self.max_chars:
            raise TextRejected(
                _SAFE_MESSAGES["text_rejected"],
                detail=f"{len(spoken)} characters, the limit is {self.max_chars}",
                status=413,
            )
        return split_text(spoken)

    async def synthesize(self, text: str) -> Speech:
        """Speak `text` and return one clip that has sound, or raise a SpeechError.

        Every error carries `characters_used` (see `used_by`).
        """
        chunks = self.prepare(text)
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            raise SpeechNotConfigured(
                _SAFE_MESSAGES["not_configured"], detail="GROQ_API_KEY is not set"
            )

        client = self.http or _shared_client()
        started = self.clock()
        clips: list[WavClip] = []
        used = 0
        for number, chunk in enumerate(chunks, start=1):
            try:
                audio = await self._request(client, key, chunk, number, len(chunks))
            except SpeechError as exc:
                exc.characters_used = used  # type: ignore[attr-defined]
                raise
            used += len(chunk)  # Groq answered, so these characters are charged

            try:
                clip = parse_wav(audio)
            except WavError as exc:
                error = SpeechUnavailable(
                    _SAFE_MESSAGES["unavailable"], detail=f"unreadable audio: {exc}"
                )
                error.characters_used = used  # type: ignore[attr-defined]
                raise error from exc
            if not clip.has_sound():
                logger.warning(
                    "Text to speech: silent clip (%d chars, %.1f%% of samples with sound)",
                    len(chunk),
                    clip.active_share() * 100,
                )
                error = SilentAudio(
                    _SAFE_MESSAGES["silent_audio"],
                    detail=f"chunk {number} of {len(chunks)} is silent",
                )
                error.characters_used = used  # type: ignore[attr-defined]
                raise error
            clips.append(clip)

        try:
            joined = join_clips(clips)
        except WavError as exc:
            error = SpeechUnavailable(
                _SAFE_MESSAGES["unavailable"], detail=f"cannot join audio: {exc}"
            )
            error.characters_used = used  # type: ignore[attr-defined]
            raise error from exc
        return Speech(
            clip=joined,
            characters=used,
            chunks=len(chunks),
            latency_s=self.clock() - started,
        )

    # ── Internals ──────────────────────────────────────────────────────────

    async def _request(
        self, client: httpx.AsyncClient, key: str, chunk: str, number: int, total: int
    ) -> bytes:
        started = self.clock()
        try:
            response = await client.post(
                TTS_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": self.model,
                    "input": chunk,
                    "voice": self.voice,
                    "response_format": "wav",
                },
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

        logger.info(
            "Text to speech: chunk %d of %d, %d chars, status %d, %.2fs",
            number,
            total,
            len(chunk),
            response.status_code,
            self.clock() - started,
        )
        if response.status_code != 200:
            raise self._error_for(response, key)
        return response.content

    @staticmethod
    def _error_for(response: httpx.Response, key: str) -> SpeechError:
        status = response.status_code
        text = _error_text(response, key)
        detail = f"HTTP {status}: {text}"
        if status == 429:
            return SpeechRateLimited(
                _SAFE_MESSAGES["rate_limited"],
                retry_after=_retry_after(response),
                detail=detail,
            )
        if status in (401, 403) or (status == 400 and "terms" in text.lower()):
            # 400 "terms": the Orpheus terms were not accepted in the Groq console.
            return SpeechNotConfigured(_SAFE_MESSAGES["not_configured"], detail=detail)
        if status in (400, 413, 422):
            return TextRejected(
                _SAFE_MESSAGES["text_rejected"],
                detail=detail,
                status=413 if status == 413 else 400,
            )
        return SpeechUnavailable(_SAFE_MESSAGES["unavailable"], detail=detail)
