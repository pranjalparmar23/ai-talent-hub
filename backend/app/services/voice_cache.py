"""ClipCache — keeps spoken questions so that the same question is paid for once.

Phase 8, task 5. Orpheus has about 3,600 tokens (characters) a day for the whole
organisation (see docs/architecture/voice.md), so a clip that has been made and
checked for sound is stored in Redis and served again. A cache hit makes no Groq call
and uses no quota.

    cache = ClipCache()
    key = cache.key(model, voice, spoken_text)
    audio = await cache.get(key)            # WAV bytes, or None
    if audio is None:
        ...make it...
        await cache.set(key, audio)         # only clips that have sound

Design:

- The key is a SHA-256 of the model, the voice and the spoken text, so the text is
  never in a key, a log line or a counter. A change of model or voice is a new key.
- The app's shared Redis client decodes every reply as text, so it cannot hold raw
  bytes. The WAV is stored as base64 (a third larger, about 470 KB for 7 s of
  speech) in that same client, which avoids a second connection pool.
- The cache is **fail-open**: a Redis problem makes `get` a miss and `set` a no-op, so
  voice keeps working. (The quota guard is the opposite, it fails closed: spending
  the Groq allowance unchecked is worse than serving one clip without the cache.)
- Entries expire after `VOICE_TTS_CACHE_SECONDS` (3 days by default). Only
  clips that Orpheus was charged for are ever stored, so the quota caps how much
  can be cached in a day. A clip over `max_bytes` is not stored.
- Nothing here logs audio or text, only the kind of error.

NOTE: __init__ must stay argument-free, like the other services. Tests set the
`client`, `ttl_seconds` and `max_bytes` attributes instead.
"""

import base64
import binascii
import hashlib
import logging

from app.services.speech_service import _int_setting

logger = logging.getLogger(__name__)

KEY_PREFIX = "voice:tts:clip:"
DEFAULT_TTL_SECONDS = 3 * 24 * 3600
DEFAULT_MAX_BYTES = 3 * 1024 * 1024  # about 60 s of 24 kHz 16-bit mono speech


class ClipCache:
    def __init__(self):
        self.ttl_seconds = _int_setting("VOICE_TTS_CACHE_SECONDS", DEFAULT_TTL_SECONDS)
        self.max_bytes = DEFAULT_MAX_BYTES
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from app.database.redis import redis_client

            self._client = redis_client
        return self._client

    @client.setter
    def client(self, value):
        self._client = value

    @staticmethod
    def key(model: str, voice: str, text: str) -> str:
        """The cache key of a clip. `text` is the text as it is spoken."""
        digest = hashlib.sha256(
            "\n".join((model, voice, text)).encode("utf-8")
        ).hexdigest()
        return f"{KEY_PREFIX}{digest}"

    async def get(self, key: str) -> bytes | None:
        """The stored WAV file, or None (not stored, expired, damaged or no Redis)."""
        try:
            stored = await self.client.get(key)
        except Exception as exc:
            logger.warning("Voice cache read failed (%s)", type(exc).__name__)
            return None
        if stored is None:
            return None
        try:
            audio = base64.b64decode(stored, validate=True)  # text or bytes
        except (binascii.Error, ValueError):
            logger.warning("Voice cache entry is damaged")
            return None
        if not audio.startswith(b"RIFF"):
            logger.warning("Voice cache entry is not audio")
            return None
        return audio

    async def set(self, key: str, audio: bytes) -> bool:
        """Store a WAV file. Returns whether it was stored; never raises."""
        if not isinstance(audio, bytes) or not audio or len(audio) > self.max_bytes:
            logger.info("Voice cache: clip not stored (empty or too large)")
            return False
        try:
            await self.client.set(
                key, base64.b64encode(audio).decode("ascii"), ex=self.ttl_seconds
            )
        except Exception as exc:
            logger.warning("Voice cache write failed (%s)", type(exc).__name__)
            return False
        return True
