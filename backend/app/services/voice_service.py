"""VoiceService — turns one spoken answer into text, inside the quota guard.

Phase 8, task 4. The route (POST /interview/{id}/transcribe) checks that the
interview belongs to the caller and is still open, reads the audio, and calls
`transcribe_answer`. This service does the voice part:

  1. refuse a recording that is declared longer than the longest allowed answer;
  2. reserve speech-to-text capacity (VoiceQuotaService) for the declared length,
     or for the longest answer when none was declared;
  3. call Groq Whisper (SpeechService);
  4. settle the reservation as described in docs/architecture/voice.md:

       text returned        adjust to the real audio length
       no speech detected   adjust (Groq processed the audio, so quota was used)
       Groq 429             release, and start the cool-down with Groq's retry-after
       anything else        release (the call did not use Groq's quota)

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

import logging
import math
from typing import Any

from fastapi import HTTPException, status

from app.services.speech_service import (
    NoSpeechDetected,
    SpeechError,
    SpeechRateLimited,
    SpeechService,
)
from app.services.voice_quota_service import (
    COOLDOWN_DEFAULT_SECONDS,
    STT,
    VoiceQuotaService,
)

logger = logging.getLogger(__name__)

# A recorder stops a moment after the limit, so a declared length slightly over the
# longest allowed answer is still accepted.
LENGTH_GRACE_SECONDS = 2


def _declared(value: object) -> float | None:
    """The length the browser reported, if it is a sensible number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


class VoiceService:
    def __init__(self):
        self.speech = SpeechService()
        self.quota = VoiceQuotaService()

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
