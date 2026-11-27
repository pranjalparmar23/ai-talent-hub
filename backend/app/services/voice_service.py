"""VoiceService — the voice part of an interview, inside the quota guard.

Two things happen here: a spoken answer becomes text (`transcribe_answer`, task 4) and
an interviewer question becomes speech (`speak`, task 5). The routes check that the
interview belongs to the caller and is still open, and then call this service.

Listening (POST /interview/{id}/transcribe):

  1. refuse a recording that is declared longer than the longest allowed answer;
  2. reserve speech-to-text capacity (VoiceQuotaService) for the declared length,
     or for the longest answer when none was declared;
  3. call Groq Whisper (SpeechService);
  4. settle the reservation as described in docs/architecture/voice.md:

       text returned        adjust to the real audio length
       no speech detected   adjust (Groq processed the audio, so quota was used)
       Groq 429             release, and start the cool-down with Groq's retry-after
       anything else        release (the call did not use Groq's quota)

Speaking (POST /interview/{id}/speak), only when VOICE_TTS_PROVIDER is "orpheus":

  1. clean and split the question into chunks of at most 200 characters (TtsService);
  2. look the clip up in the cache (ClipCache). A hit makes no Groq call, uses no
     quota, and is served even when the quota is used up;
  3. on a miss, reserve quota for the characters and the number of chunks (one Groq
     request each), call Groq Orpheus (TtsService), and settle the reservation:

       clip made            adjust to what was sent, then store the clip in the cache
       Groq 429             release (or adjust, if earlier chunks were charged), and
                            start the cool-down with Groq's retry-after
       silent clip          adjust: Groq charged the characters, nothing is cached
       any other failure    release when nothing was charged, else adjust
       anything unexpected  release

  Two requests for the same clip at the same moment (a page that asks twice, or two
  candidates with the same question) share one Groq call: the second waits for the
  first. The call runs as its own task, so a candidate who leaves does not leave the
  quota half settled.

Every failure is an HTTPException whose `detail` is an object the interview screen
can act on:

    {"code": "no_speech", "message": "We could not hear anything. ...",
     "retry_after": 30}                      # retry_after only when it is known

and a `Retry-After` header when `retry_after` is present. The messages are short
and safe to show; they point the candidate to typing. Nothing here logs audio,
transcripts or user ids, only outcome codes.

NOTE: __init__ must stay argument-free — routes use Depends(VoiceService), and
FastAPI would turn any extra parameter into a query-string field. Tests replace the
`speech` and `quota` attributes instead.
"""

import asyncio
import logging
import math
import os
from typing import Any

from fastapi import HTTPException, status

from app.services.speech_service import (
    NoSpeechDetected,
    SpeechError,
    SpeechRateLimited,
    SpeechService,
)
from app.services.tts_service import SilentAudio, TtsService, used_by
from app.services.voice_cache import ClipCache
from app.services.voice_quota_service import (
    COOLDOWN_DEFAULT_SECONDS,
    STT,
    TTS,
    VoiceQuotaService,
)

logger = logging.getLogger(__name__)

TTS_PROVIDERS = ("browser", "orpheus")
DEFAULT_TTS_PROVIDER = "browser"  # the browser reads questions aloud, with no quota

# What a candidate sees when a spoken question is not available. The quota guard's own
# messages talk about typing an answer, which does not fit a question being read out.
_SPEAK_MESSAGES = {
    "tts_off": "Spoken questions are switched off. You can read the question on the screen.",
    "disabled": "Voice is switched off. You can read the question on the screen.",
    "unavailable": "Spoken questions are not available right now. "
    "You can read the question on the screen.",
    "user_daily": "You have used today's spoken questions. "
    "You can read the question on the screen.",
    "busy": "Spoken questions are busy right now. "
    "You can read the question on the screen.",
}
_BUSY_REASONS = ("org_daily", "org_hourly", "org_minute", "cooldown")

# Clips being made right now, by cache key, so that the same clip is paid for once.
_inflight: dict[str, "asyncio.Future[tuple[bytes, bool]]"] = {}

# A recorder stops a moment after the limit, so a declared length slightly over the
# longest allowed answer is still accepted.
LENGTH_GRACE_SECONDS = 2


def _tts_provider() -> str:
    """VOICE_TTS_PROVIDER: "browser" (the default) or "orpheus"."""
    raw = (os.getenv("VOICE_TTS_PROVIDER") or "").strip().lower()
    if not raw:
        return DEFAULT_TTS_PROVIDER
    if raw not in TTS_PROVIDERS:
        logger.warning(
            "Ignoring VOICE_TTS_PROVIDER: it must be one of %s",
            ", ".join(TTS_PROVIDERS),
        )
        return DEFAULT_TTS_PROVIDER
    return raw


def _finished(key: str, task: "asyncio.Future[tuple[bytes, bool]]") -> None:
    """A clip task is over: forget it, and never leave its error unread."""
    if _inflight.get(key) is task:
        del _inflight[key]
    if not task.cancelled():
        task.exception()


def _chunks_answered(chunks: list[str], characters: int) -> int:
    """How many leading chunks add up to `characters` (what Groq has charged)."""
    total = 0
    for number, chunk in enumerate(chunks, start=1):
        total += len(chunk)
        if total >= characters:
            return number
    return len(chunks)


def _declared(value: object) -> float | None:
    """The length the browser reported, if it is a sensible number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


class VoiceService:
    def __init__(self):
        self.speech = SpeechService()
        self.quota = VoiceQuotaService()
        self.tts = TtsService()
        self.cache = ClipCache()
        self.tts_provider = _tts_provider()

    async def transcribe_answer(
        self,
        user_id: str,
        audio: bytes,
        content_type: str,
        declared_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Return {"text", "audio_seconds", "remaining"} or raise HTTPException."""
        longest = self.quota.limits.max_recording_seconds
        if longest <= 0:
            raise self._fail(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "disabled",
                "Voice is switched off. You can type your answer instead.",
            )

        declared = _declared(declared_seconds)
        if declared is not None and declared > longest + LENGTH_GRACE_SECONDS:
            logger.info("Voice transcription: too_long")
            raise self._fail(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "too_long",
                f"That answer is longer than {longest} seconds. "
                "Please keep it shorter, or type it.",
            )
        reserved = min(declared, longest) if declared is not None else longest

        decision = await self.quota.reserve(STT, user_id, reserved)
        if not decision.allowed:
            logger.info("Voice transcription: denied (%s)", decision.reason)
            raise self._fail(
                decision.http_status,
                decision.reason or "denied",
                decision.message,
                decision.retry_after,
            )
        reservation = decision.reservation

        try:
            transcript = await self.speech.transcribe(
                audio, content_type, declared_seconds=declared
            )
        except SpeechRateLimited as exc:
            await self.quota.release(reservation)
            await self.quota.record_rate_limited(STT, exc.retry_after)
            wait = (
                exc.retry_after
                if exc.retry_after is not None
                else COOLDOWN_DEFAULT_SECONDS
            )
            logger.info("Voice transcription: rate_limited")
            raise self._fail(exc.status, exc.code, str(exc), wait) from exc
        except NoSpeechDetected as exc:
            # Groq processed the audio, so the quota was used: adjust, do not release.
            if exc.audio_seconds is not None:
                await self.quota.adjust(reservation, exc.audio_seconds)
            logger.info("Voice transcription: no_speech")
            raise self._fail(exc.status, exc.code, str(exc)) from exc
        except SpeechError as exc:
            await self.quota.release(reservation)
            logger.info("Voice transcription: %s", exc.code)
            raise self._fail(exc.status, exc.code, str(exc)) from exc
        except Exception:
            await self.quota.release(reservation)
            raise

        if transcript.audio_seconds is not None:
            await self.quota.adjust(reservation, transcript.audio_seconds)
        room = await self.quota.remaining(STT, user_id)
        logger.info("Voice transcription: ok")
        return {
            "text": transcript.text,
            "audio_seconds": (
                None
                if transcript.audio_seconds is None
                else round(transcript.audio_seconds, 2)
            ),
            "remaining": (
                {"requests": room.requests, "seconds": room.units}
                if room.available
                else None
            ),
        }

    async def speak(self, user_id: str, text: str) -> dict[str, Any]:
        """Return {"audio": WAV bytes, "cache": "hit" | "miss"} or raise HTTPException.

        `text` is an interviewer question that the route read from the candidate's own
        interview. "hit" means this request made no Groq call: the clip came from the
        cache, or from a call that another request had already started.
        """
        if self.tts_provider != "orpheus":
            raise self._fail(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "tts_off",
                _SPEAK_MESSAGES["tts_off"],
            )
        if not self.quota.limits.enabled:
            raise self._fail(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "disabled",
                _SPEAK_MESSAGES["disabled"],
            )
        try:
            chunks = self.tts.prepare(text)
        except SpeechError as exc:
            logger.info("Voice speak: %s", exc.code)
            raise self._fail(exc.status, exc.code, str(exc)) from exc

        key = self.cache.key(self.tts.model, self.tts.voice, "\n".join(chunks))
        cached = await self.cache.get(key)
        if cached is not None:
            logger.info("Voice speak: cache hit")
            return {"audio": cached, "cache": "hit"}

        task = _inflight.get(key)
        shared = task is not None
        if task is None:
            task = asyncio.ensure_future(self._make_clip(user_id, text, chunks, key))
            _inflight[key] = task
            task.add_done_callback(lambda done, key=key: _finished(key, done))
        # shield: a caller that gives up must not cancel the call half way, because
        # the quota would be left unsettled and the clip, once paid for, lost.
        audio, made = await asyncio.shield(task)
        return {"audio": audio, "cache": "miss" if made and not shared else "hit"}

    async def _make_clip(
        self, user_id: str, text: str, chunks: list[str], key: str
    ) -> tuple[bytes, bool]:
        """The clip, and whether this call made it (False: it was found in the cache)."""
        # Another request may have stored this clip since the caller looked.
        cached = await self.cache.get(key)
        if cached is not None:
            return cached, False

        decision = await self.quota.reserve(
            TTS, user_id, sum(len(chunk) for chunk in chunks), requests=len(chunks)
        )
        if not decision.allowed:
            reason = decision.reason or "denied"
            logger.info("Voice speak: denied (%s)", reason)
            if reason in _BUSY_REASONS:
                message = _SPEAK_MESSAGES["busy"]
            else:
                message = _SPEAK_MESSAGES.get(reason, _SPEAK_MESSAGES["unavailable"])
            raise self._fail(
                decision.http_status, reason, message, decision.retry_after
            )
        reservation = decision.reservation

        try:
            speech = await self.tts.synthesize(text)
        except SpeechRateLimited as exc:
            await self._settle_failed(reservation, chunks, exc)
            await self.quota.record_rate_limited(TTS, exc.retry_after)
            wait = (
                exc.retry_after
                if exc.retry_after is not None
                else COOLDOWN_DEFAULT_SECONDS
            )
            logger.info("Voice speak: rate_limited")
            raise self._fail(exc.status, exc.code, str(exc), wait) from exc
        except SpeechError as exc:
            await self._settle_failed(reservation, chunks, exc)
            logger.info("Voice speak: %s", exc.code)
            raise self._fail(exc.status, exc.code, str(exc)) from exc
        except Exception:
            await self.quota.release(reservation)
            raise

        await self.quota.adjust(
            reservation, speech.characters, actual_requests=speech.chunks
        )
        audio = speech.clip.to_bytes()
        await self.cache.set(key, audio)
        logger.info("Voice speak: ok (%d chunks)", speech.chunks)
        return audio, True

    async def _settle_failed(self, reservation, chunks: list[str], error) -> None:
        """Give back what Groq did not use; keep what it charged.

        `used_by` is the number of characters Groq answered for before the error. With
        none, the whole reservation is released. Otherwise the reservation shrinks to
        those characters and to the requests made: the chunks that were answered, plus
        the one that failed (a silent clip was answered, so there is no extra one).
        """
        used = used_by(error)
        if used <= 0:
            await self.quota.release(reservation)
            return
        requests = _chunks_answered(chunks, used)
        if not isinstance(error, SilentAudio):
            requests = min(len(chunks), requests + 1)
        await self.quota.adjust(reservation, used, actual_requests=requests)

    @staticmethod
    def _fail(
        status_code: int,
        code: str,
        message: str,
        retry_after: float | None = None,
    ) -> HTTPException:
        detail: dict[str, Any] = {"code": code, "message": message}
        headers = None
        if retry_after is not None:
            seconds = max(1, math.ceil(retry_after))
            detail["retry_after"] = seconds
            headers = {"Retry-After": str(seconds)}
        return HTTPException(status_code=status_code, detail=detail, headers=headers)
