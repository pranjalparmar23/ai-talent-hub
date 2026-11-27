"""VoiceQuotaService — keeps voice calls inside the Groq free-tier limits.

Why: Groq's free-tier limits belong to the whole organisation, not to one user, and
Orpheus (text to speech) is tight: about 3,600 characters a day, shared with every
test run (see docs/architecture/voice.md). Without a guard, one user or one afternoon
of testing could use the day's allowance for everybody.

How it is used (tasks 4 and 5 wire it into the endpoints):

    decision = await quota.reserve(STT, user_id, audio_seconds)
    if not decision.allowed:
        raise HTTPException(decision.http_status, decision.message)   # type instead
    try:
        result = await call_groq(...)
    except GroqRateLimited as exc:
        await quota.release(decision.reservation)
        await quota.record_rate_limited(STT, exc.retry_after)
        raise
    except Exception:
        await quota.release(decision.reservation)      # the call never used quota
        raise
    await quota.adjust(decision.reservation, real_audio_seconds)

What is counted (one Redis counter per line; all are caps below Groq's limits):

    speech to text   per user per day: requests, audio seconds
                     organisation per day: requests, audio seconds
                     organisation per hour: audio seconds; per minute: requests
    text to speech   per user per day: characters (one character is one token)
                     organisation per day: characters, requests
                     organisation per minute: characters, requests

A reservation adds to every counter at once. If any counter would go over its cap
the whole reservation is rolled back, so a denied call never uses anything.

Windows are fixed UTC windows. Groq's own windows are rolling, so a fixed window can
let through up to twice a cap across a boundary; the caps are therefore at most 50%
of Groq's limits, and a 429 from Groq starts a cool-down (`record_rate_limited`) that
blocks that kind of call for the organisation until Groq says it is free again.

If Redis is unavailable the call is denied ("unavailable"): voice is optional, and
typing always works, so a failure must not spend quota unchecked.

Keys hold only the kind, the user id and the window, never text or audio.

NOTE: __init__ must stay argument-free — routes use Depends(VoiceQuotaService), and
FastAPI would turn any extra parameter into a query-string field. Tests set the
`client`, `limits` and `clock` attributes instead.
"""

import logging
import math
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STT = "stt"
TTS = "tts"
KINDS = (STT, TTS)

# Why a call was denied.
DISABLED = "disabled"
COOLDOWN = "cooldown"
UNAVAILABLE = "unavailable"
USER_DAILY = "user_daily"
ORG_DAILY = "org_daily"
ORG_HOURLY = "org_hourly"
ORG_MINUTE = "org_minute"

COOLDOWN_DEFAULT_SECONDS = 60
COOLDOWN_MAX_SECONDS = 3600
KEY_EXPIRY_MARGIN_SECONDS = 60

_WINDOW_SECONDS = {"day": 86400, "hour": 3600, "minute": 60}
_WINDOW_FORMAT = {"day": "%Y%m%d", "hour": "%Y%m%d%H", "minute": "%Y%m%d%H%M"}
_WINDOW_NAME = {"day": "daily", "hour": "hourly", "minute": "minute"}

_MESSAGES = {
    DISABLED: "Voice is switched off. You can type your answer instead.",
    USER_DAILY: "You have used today's voice allowance. You can type your answer instead.",
    ORG_DAILY: "Voice is busy right now. You can type your answer instead.",
    ORG_HOURLY: "Voice is busy right now. You can type your answer instead.",
    ORG_MINUTE: "Voice is busy right now. Try again in a minute, or type your answer.",
    COOLDOWN: "Voice is busy right now. Try again in a minute, or type your answer.",
    UNAVAILABLE: "Voice is not available right now. You can type your answer instead.",
}


# ── Limits ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VoiceLimits:
    """Caps for one day, hour or minute. A cap of 0 switches that kind of call off.

    `max_recording_seconds` is not a quota but the longest answer the speech-to-text
    endpoint accepts; it sits here so that every VOICE_* number is read in one place.

    Defaults are at most 50% of the Groq free-tier limits read on 2026-11-25:
    Whisper 2,000 requests, 28,800 audio seconds a day, 7,200 audio seconds an hour,
    20 requests a minute; Orpheus 100 requests, 3,600 tokens a day, 1,200 tokens and
    10 requests a minute.
    """

    enabled: bool = True
    stt_min_billed_seconds: int = 10  # Groq bills at least 10 s per request
    max_recording_seconds: int = 120  # longest recording the endpoint accepts
    stt_user_daily_requests: int = 30
    stt_user_daily_seconds: int = 900
    stt_org_daily_requests: int = 600
    stt_org_daily_seconds: int = 10_000
    stt_org_hourly_seconds: int = 3_000
    stt_org_minute_requests: int = 10
    tts_user_daily_chars: int = 600
    tts_org_daily_chars: int = 1_800
    tts_org_daily_requests: int = 50
    tts_org_minute_chars: int = 600
    tts_org_minute_requests: int = 5

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "VoiceLimits":
        """Read VOICE_<FIELD> settings. A bad value is ignored (the default stays)."""
        env = os.environ if environ is None else environ
        values: dict[str, object] = {}
        for item in fields(cls):
            name = f"VOICE_{item.name.upper()}"
            raw = env.get(name)
            if raw is None or not str(raw).strip():
                continue
            if item.name == "enabled":
                values["enabled"] = str(raw).strip().lower() not in {
                    "0",
                    "false",
                    "no",
                    "off",
                }
                continue
            try:
                number = int(str(raw).strip())
            except ValueError:
                number = -1
            if number < 0:
                logger.warning(
                    "Ignoring %s: it must be a whole number, 0 or more", name
                )
                continue
            values[item.name] = number
        return cls(**values)


# (scope, window, metric, name of the cap in VoiceLimits)
_CHECKS: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    STT: (
        ("user", "day", "requests", "stt_user_daily_requests"),
        ("user", "day", "units", "stt_user_daily_seconds"),
        ("org", "day", "requests", "stt_org_daily_requests"),
        ("org", "day", "units", "stt_org_daily_seconds"),
        ("org", "hour", "units", "stt_org_hourly_seconds"),
        ("org", "minute", "requests", "stt_org_minute_requests"),
    ),
    TTS: (
        ("user", "day", "units", "tts_user_daily_chars"),
        ("org", "day", "units", "tts_org_daily_chars"),
        ("org", "day", "requests", "tts_org_daily_requests"),
        ("org", "minute", "units", "tts_org_minute_chars"),
        ("org", "minute", "requests", "tts_org_minute_requests"),
    ),
}


# ── Results ───────────────────────────────────────────────────────────────────


@dataclass
class _Entry:
    key: str
    delta: int
    metric: str


@dataclass
class Reservation:
    """What one call has added to the counters, so it can be corrected or undone."""

    kind: str
    units: int
    entries: list[_Entry] = field(default_factory=list)
    closed: bool = False


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None
    retry_after: int | None = None  # seconds until the call could be allowed again
    reservation: Reservation | None = None

    @property
    def message(self) -> str:
        """A short, friendly sentence for the candidate."""
        return _MESSAGES.get(self.reason or "", "")

    @property
    def http_status(self) -> int:
        """429 when a limit was reached, 503 when voice is off or unavailable."""
        if self.allowed:
            return 200
        return 503 if self.reason in (DISABLED, UNAVAILABLE) else 429


@dataclass(frozen=True)
class Remaining:
    """What this user can still use right now: the lowest of their and the org's room."""

    enabled: bool
    available: bool  # False when Redis could not be read
    requests: int
    units: int  # audio seconds for speech to text, characters for text to speech
    cooldown_seconds: int


# ── Helpers ───────────────────────────────────────────────────────────────────


def _safe_id(user_id: object) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]", "_", str(user_id or "").strip())
    if not text:
        raise ValueError("user_id must not be empty")
    return text


def _bucket(window: str, now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime(_WINDOW_FORMAT[window])


def _seconds_left(window: str, now: float) -> int:
    size = _WINDOW_SECONDS[window]
    return max(1, math.ceil(size - now % size))


def _key(
    kind: str, scope: str, user_id: str, window: str, metric: str, now: float
) -> str:
    who = "org" if scope == "org" else f"user:{user_id}"
    return f"voice:{kind}:{who}:{window}:{_bucket(window, now)}:{metric}"


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")


# ── The service ───────────────────────────────────────────────────────────────


class VoiceQuotaService:
    def __init__(self):
        self.limits = VoiceLimits.from_env()
        self.clock: Callable[[], float] = time.time
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

    # ── Public API ─────────────────────────────────────────────────────────

    async def reserve(self, kind: str, user_id: object, units: float) -> Decision:
        """Ask for room for one call and take it. Never raises for Redis problems."""
        _check_kind(kind)
        uid = _safe_id(user_id)
        billed = self._billed(kind, units)
        if not self.limits.enabled:
            return Decision(False, DISABLED)
        try:
            wait = await self._cooldown_left(kind)
            if wait > 0:
                return Decision(False, COOLDOWN, wait)
            return await self._take(kind, uid, billed)
        except Exception as exc:  # Redis down, timeout, ...
            logger.warning("Voice quota unavailable (%s)", type(exc).__name__)
            return Decision(False, UNAVAILABLE)

    async def adjust(
        self, reservation: Reservation | None, actual_units: float
    ) -> None:
        """Replace the reserved amount with the real one (e.g. the real audio length)."""
        if reservation is None or reservation.closed:
            return
        actual = self._billed(reservation.kind, actual_units)
        difference = actual - reservation.units
        if difference == 0:
            return
        try:
            for entry in reservation.entries:
                if entry.metric == "units":
                    await self.client.incrby(entry.key, difference)
                    entry.delta += difference
            reservation.units = actual
        except Exception as exc:
            logger.warning("Voice quota adjust failed (%s)", type(exc).__name__)

    async def release(self, reservation: Reservation | None) -> None:
        """Give a reservation back, for a call that never used Groq's quota."""
        if reservation is None or reservation.closed:
            return
        reservation.closed = True
        await self._undo(reservation.entries)

    async def record_rate_limited(
        self, kind: str, retry_after: float | None = None
    ) -> None:
        """Groq answered 429: block this kind of call for the organisation until it
        is free again (Groq's `retry-after`, 60 s when it is missing)."""
        _check_kind(kind)
        seconds = COOLDOWN_DEFAULT_SECONDS if retry_after is None else retry_after
        seconds = min(max(math.ceil(seconds), 1), COOLDOWN_MAX_SECONDS)
        try:
            await self.client.set(f"voice:cooldown:{kind}", "1", ex=seconds)
        except Exception as exc:
            logger.warning("Voice cool-down not saved (%s)", type(exc).__name__)

    async def remaining(self, kind: str, user_id: object) -> Remaining:
        """How much this user can still use now, for the interview screen."""
        _check_kind(kind)
        uid = _safe_id(user_id)
        limits = self.limits
        if not limits.enabled:
            return Remaining(False, True, 0, 0, 0)
        try:
            now = self.clock()
            room = {"requests": None, "units": None}
            for scope, window, metric, cap_name in _CHECKS[kind]:
                key = _key(kind, scope, uid, window, metric, now)
                used = int(await self.client.get(key) or 0)
                left = max(0, getattr(limits, cap_name) - used)
                best = room[metric]
                room[metric] = left if best is None else min(best, left)
            wait = await self._cooldown_left(kind)
        except Exception as exc:
            logger.warning("Voice quota unavailable (%s)", type(exc).__name__)
            return Remaining(True, False, 0, 0, 0)
        blocked = wait > 0
        return Remaining(
            True,
            True,
            0 if blocked else room["requests"] or 0,
            0 if blocked else room["units"] or 0,
            wait,
        )

    # ── Internals ──────────────────────────────────────────────────────────

    def _billed(self, kind: str, units: float) -> int:
        if kind == STT:
            seconds = math.ceil(units) if units and units > 0 else 0
            return max(seconds, self.limits.stt_min_billed_seconds)
        if not units or units < 1:
            raise ValueError("text must not be empty")
        return int(units)

    async def _cooldown_left(self, kind: str) -> int:
        ttl = await self.client.ttl(f"voice:cooldown:{kind}")
        return int(ttl) if ttl and ttl > 0 else 0

    async def _take(self, kind: str, uid: str, billed: int) -> Decision:
        now = self.clock()
        taken: list[_Entry] = []
        try:
            for scope, window, metric, cap_name in _CHECKS[kind]:
                delta = 1 if metric == "requests" else billed
                key = _key(kind, scope, uid, window, metric, now)
                total = await self.client.incrby(key, delta)
                taken.append(_Entry(key, delta, metric))
                await self.client.expire(
                    key, _WINDOW_SECONDS[window] + KEY_EXPIRY_MARGIN_SECONDS
                )
                if total > getattr(self.limits, cap_name):
                    await self._undo(taken)
                    reason = f"{scope}_{_WINDOW_NAME[window]}"
                    return Decision(False, reason, _seconds_left(window, now))
        except Exception:
            await self._undo(taken)
            raise
        return Decision(True, reservation=Reservation(kind, billed, taken))

    async def _undo(self, entries: list[_Entry]) -> None:
        for entry in entries:
            try:
                await self.client.decrby(entry.key, entry.delta)
            except Exception as exc:
                logger.warning("Voice quota rollback failed (%s)", type(exc).__name__)
