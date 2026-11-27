"""Route-level tests for POST /api/candidate/interview/{id}/transcribe.

These go through the real app, so they see what the unit tests cannot: sign-in,
ownership, the capped body reader, FastAPI's response model, the error bodies and
headers a browser would receive, and the OpenAPI page.

What is real: the app and its middleware, InterviewService (its Postgres and Redis
access replaced by in-memory fakes), VoiceService and the quota counters (on a fake
Redis and a fake clock). What is fake: Groq (FakeSpeech), so nothing here uses the
network or a key.
"""

import logging
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app import main
from app.agents.shared.memory_agent import MemoryAgent
from app.main import app
from app.middleware import rate_limit
from app.middleware.auth import get_current_user
from app.services.interview_service import MAX_QUESTIONS, InterviewService
from app.services.speech_service import (
    NoSpeechDetected,
    SpeechAudioRejected,
    SpeechNotConfigured,
    SpeechRateLimited,
    SpeechUnavailable,
)
from app.services.voice_quota_service import VoiceLimits, VoiceQuotaService
from app.services.voice_service import VoiceService
from tests.unit.agents.test_memory_agent import FakeRedis as MemoryFakeRedis
from tests.unit.services.test_interview_service import FakeInterviewer
from tests.unit.services.test_voice_quota_service import FakeClock
from tests.unit.services.test_voice_quota_service import FakeRedis as QuotaFakeRedis
from tests.unit.services.test_voice_service import TEXT, FakeSpeech

pytestmark = pytest.mark.asyncio

USER_ID = str(uuid.uuid4())
OTHER_USER_ID = str(uuid.uuid4())
START = {"company": "Amazon", "role": "Backend Engineer"}

AUDIO = b"fake audio bytes" * 10  # 160 bytes
TYPE = "audio/webm;codecs=opus"
CAP = 1000  # the biggest body these tests accept (VOICE_MAX_AUDIO_BYTES in real life)
DAY = "20261126"  # the fake clock's day


def url(session_id: str) -> str:
    return f"/api/candidate/interview/{session_id}/transcribe"


def candidate(user_id: str) -> dict:
    return {"id": user_id, "email": "candidate@example.com", "role": "candidate"}


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _interview_service(monkeypatch) -> InterviewService:
    """A real InterviewService whose storage lives in memory (the same fakes as
    test_interview_service, so /start, /respond and GET all work)."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    service = InterviewService()
    service.interviewer = FakeInterviewer()
    redis = MemoryFakeRedis()
    service.memory_factory = lambda sid: MemoryAgent(sid, client=redis)
    rows: dict[uuid.UUID, dict] = {}

    async def create_row(
        session_id, user_id, company, role, history, sources=None, notice=None
    ):
        rows[session_id] = {
            "id": session_id,
            "user_id": str(user_id),
            "company": company,
            "role": role,
            "history": list(history),
            "feedback": None,
            "sources": list(sources or []),
            "notice": notice,
        }

    async def get_row(session_id, user_id):
        try:
            row = rows.get(uuid.UUID(session_id))
        except ValueError:
            row = None
        if row is None or row["user_id"] != str(user_id):
            raise HTTPException(404, "Interview session not found.")
        return {k: v for k, v in row.items() if k != "user_id"}

    async def save_history(session_id, history):
        rows[session_id]["history"] = list(history)

    service._create_row = create_row
    service._get_row = get_row
    service._save_history = save_history
    return service


@pytest.fixture
def redis():
    return QuotaFakeRedis(FakeClock())


@pytest.fixture
def voice(redis):
    service = VoiceService()
    service.speech = FakeSpeech(redis)
    service.speech.max_audio_bytes = CAP
    service.quota = VoiceQuotaService()
    service.quota.client = redis
    service.quota.clock = redis.clock
    service.quota.limits = VoiceLimits()
    return service


@pytest.fixture
def interviews(monkeypatch):
    return _interview_service(monkeypatch)


@pytest_asyncio.fixture
async def client(interviews, voice):
    """The real app, signed in as USER_ID."""
    # The app's per-IP limiter (100 calls a minute) counts across the whole test run,
    # so start each test with an empty count.
    rate_limit._request_counts.clear()
    app.dependency_overrides[InterviewService] = lambda: interviews
    app.dependency_overrides[VoiceService] = lambda: voice
    app.dependency_overrides[get_current_user] = lambda: candidate(USER_ID)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        for dependency in (InterviewService, VoiceService, get_current_user):
            app.dependency_overrides.pop(dependency, None)


@pytest_asyncio.fixture
async def session_id(client) -> str:
    response = await client.post("/api/candidate/interview/start", json=START)
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


async def send(client, session_id, body=AUDIO, content_type=TYPE, **headers):
    if content_type is not None:
        headers["Content-Type"] = content_type
    return await client.post(url(session_id), content=body, headers=headers)


def detail(response) -> dict:
    """The error body: always an object with a code and a message."""
    body = response.json()["detail"]
    assert isinstance(body, dict), body
    assert set(body) <= {"code", "message", "retry_after"}
    assert body["code"] and body["message"]
    return body


def used(redis, what: str) -> int:
    return redis.counter(f"stt:user:{USER_ID}:day:{DAY}:{what}")


def nothing_was_counted(redis) -> bool:
    return all(int(v) == 0 for k, v in redis.values.items() if "cooldown" not in k)


async def finish_the_interview(client, session_id) -> None:
    for _ in range(MAX_QUESTIONS):
        response = await client.post(
            f"/api/candidate/interview/{session_id}/respond",
            json={"response": "my answer"},
        )
        assert response.status_code == 200, response.text


# ── A good answer ─────────────────────────────────────────────────────────────


class TestSuccess:
    async def test_returns_the_text_the_length_and_what_is_left(
        self, client, session_id
    ):
        response = await send(client, session_id, **{"X-Audio-Duration": "31"})

        assert response.status_code == 200, response.text
        assert response.json() == {
            "text": TEXT,
            "audio_seconds": 30.2,
            # the lowest room: the organisation's 10 calls a minute, less this one
            "remaining": {"requests": 9, "seconds": 869},
        }

    async def test_no_field_the_service_returns_is_dropped_by_the_route(
        self, client, session_id, voice
    ):
        """FastAPI removes fields the response model lacks, without any error."""
        from_service = await voice.transcribe_answer(USER_ID, AUDIO, TYPE, 31)

        from_route = (
            await send(client, session_id, **{"X-Audio-Duration": "31"})
        ).json()

        assert set(from_service) <= set(from_route)

    async def test_the_audio_and_its_type_reach_the_speech_service_unchanged(
        self, client, session_id, voice
    ):
        await send(client, session_id, **{"X-Audio-Duration": "12.5"})

        audio, content_type, declared = voice.speech.calls[0]
        assert audio == AUDIO
        assert content_type == TYPE
        assert declared == 12.5

    async def test_the_duration_header_is_optional(self, client, session_id, voice):
        response = await send(client, session_id)

        assert response.status_code == 200
        assert voice.speech.calls[0][2] is None

    @pytest.mark.parametrize("value", ["abc", "", "nan", "-5", "0"])
    async def test_a_bad_duration_is_ignored_not_refused(
        self, client, session_id, voice, value
    ):
        response = await send(client, session_id, **{"X-Audio-Duration": value})

        assert response.status_code == 200
        assert voice.speech.calls[0][2] is None

    async def test_the_answer_counts_against_the_users_limit(
        self, client, session_id, redis
    ):
        await send(client, session_id, **{"X-Audio-Duration": "31"})

        assert used(redis, "requests") == 1
        assert used(redis, "units") == 31  # the real length, rounded up

    async def test_the_transcript_is_not_part_of_the_interview(
        self, client, session_id
    ):
        before = (await client.get(f"/api/candidate/interview/{session_id}")).json()

        await send(client, session_id)

        after = (await client.get(f"/api/candidate/interview/{session_id}")).json()
        assert after["messages"] == before["messages"]
        assert after["question_number"] == before["question_number"]

    async def test_nothing_spoken_is_logged(self, client, session_id, caplog):
        with caplog.at_level(logging.DEBUG):
            await send(client, session_id)

        assert TEXT not in caplog.text
        assert USER_ID not in caplog.text


# ── The test setup itself ─────────────────────────────────────────────────────


class TestSetup:
    async def test_each_test_starts_with_an_empty_per_ip_count(self, client):
        # Without this, the app's 100-calls-a-minute limiter would start answering
        # 429 part-way through the suite.
        assert rate_limit._request_counts == {}


# ── Who may call it, and when ─────────────────────────────────────────────────


class TestAccess:
    async def test_it_needs_a_signed_in_user(self, client, session_id, voice):
        app.dependency_overrides.pop(get_current_user)

        response = await send(client, session_id)

        assert response.status_code == 401
        assert voice.speech.calls == []

    async def test_someone_elses_interview_is_not_found(
        self, client, session_id, voice, redis
    ):
        app.dependency_overrides[get_current_user] = lambda: candidate(OTHER_USER_ID)

        response = await send(client, session_id)

        assert response.status_code == 404
        assert detail(response)["code"] == "not_found"
        assert voice.speech.calls == []
        assert redis.values == {}  # no quota was touched either

    async def test_an_unknown_interview_is_not_found(self, client, voice, redis):
        response = await send(client, str(uuid.uuid4()))

        assert response.status_code == 404
        assert detail(response)["code"] == "not_found"
        assert redis.values == {}

    async def test_an_invalid_id_is_not_found(self, client):
        response = await send(client, "not-a-uuid")

        assert response.status_code == 404
        assert detail(response)["code"] == "not_found"

    async def test_a_finished_interview_is_refused(
        self, client, session_id, voice, redis
    ):
        await finish_the_interview(client, session_id)

        response = await send(client, session_id)

        assert response.status_code == 409
        assert detail(response)["code"] == "interview_complete"
        assert voice.speech.calls == []
        assert redis.values == {}

    async def test_the_last_open_question_can_still_be_answered(
        self, client, session_id
    ):
        for _ in range(MAX_QUESTIONS - 1):
            await client.post(
                f"/api/candidate/interview/{session_id}/respond",
                json={"response": "my answer"},
            )

        response = await send(client, session_id)

        assert response.status_code == 200


# ── The request body ──────────────────────────────────────────────────────────


class TestBody:
    async def test_an_empty_recording_is_refused_before_any_quota_is_used(
        self, client, session_id, voice, redis
    ):
        response = await send(client, session_id, body=b"")

        assert response.status_code == 400
        assert detail(response)["code"] == "audio_rejected"
        assert voice.speech.calls == []
        assert redis.values == {}

    async def test_a_body_exactly_at_the_limit_is_accepted(
        self, client, session_id, voice
    ):
        response = await send(client, session_id, body=b"x" * CAP)

        assert response.status_code == 200
        assert len(voice.speech.calls[0][0]) == CAP

    async def test_one_byte_over_the_limit_is_refused_by_its_length(
        self, client, session_id, voice, redis
    ):
        response = await send(client, session_id, body=b"x" * (CAP + 1))

        assert response.status_code == 413
        assert detail(response)["code"] == "audio_rejected"
        assert voice.speech.calls == []
        assert redis.values == {}

    async def test_a_body_announced_as_too_big_is_not_read_at_all(
        self, client, session_id, voice
    ):
        read = []

        async def chunks():
            read.append(1)
            yield b"y" * 300

        response = await send(
            client,
            session_id,
            body=chunks(),
            **{"Content-Length": str(CAP + 1)},
        )

        assert response.status_code == 413
        assert read == []  # refused from the header, before any of it came in

    async def test_a_body_with_no_length_is_stopped_while_it_streams_in(
        self, client, session_id, voice, redis
    ):
        read = []

        async def chunks():
            for _ in range(20):  # 20 x 300 = 6,000 bytes, announced by nobody
                read.append(1)
                yield b"y" * 300

        response = await send(client, session_id, body=chunks())

        assert response.status_code == 413
        assert detail(response)["code"] == "audio_rejected"
        assert voice.speech.calls == []
        assert redis.values == {}
        assert len(read) < 20  # it gave up early instead of reading everything

    async def test_a_streamed_body_within_the_limit_is_put_back_together(
        self, client, session_id, voice
    ):
        async def chunks():
            for piece in (b"a" * 100, b"b" * 100, b"c" * 100):
                yield piece

        response = await send(client, session_id, body=chunks())

        assert response.status_code == 200
        assert voice.speech.calls[0][0] == b"a" * 100 + b"b" * 100 + b"c" * 100

    async def test_the_limit_comes_from_the_speech_service_setting(
        self, client, session_id, voice
    ):
        voice.speech.max_audio_bytes = 50

        response = await send(client, session_id, body=b"x" * 51)

        assert response.status_code == 413

    async def test_a_recording_declared_too_long_is_refused(
        self, client, session_id, voice, redis
    ):
        response = await send(client, session_id, **{"X-Audio-Duration": "500"})

        assert response.status_code == 413
        body = detail(response)
        assert body["code"] == "too_long"
        assert "120 seconds" in body["message"]
        assert voice.speech.calls == []
        assert redis.values == {}

    async def test_a_format_the_speech_service_rejects_is_reported_and_not_charged(
        self, client, session_id, voice, redis
    ):
        voice.speech.error = SpeechAudioRejected(
            "This browser's recording format is not supported. "
            "Please type your answer.",
            status=415,
        )

        response = await send(client, session_id, content_type="video/quicktime")

        assert response.status_code == 415
        assert detail(response)["code"] == "audio_rejected"
        assert nothing_was_counted(redis)

    async def test_a_missing_content_type_still_reaches_the_speech_service(
        self, client, session_id, voice
    ):
        voice.speech.error = SpeechAudioRejected("Unsupported.", status=415)

        response = await client.post(url(session_id), content=AUDIO)

        assert response.status_code == 415
        # httpx sends no Content-Type for raw bytes; the service decides what to do
        assert voice.speech.calls[0][1] == ""


# ── Errors a candidate can hit ────────────────────────────────────────────────


class TestErrors:
    async def test_no_speech_is_422_and_still_counts_as_used(
        self, client, session_id, voice, redis
    ):
        voice.speech.error = NoSpeechDetected(
            "We could not hear anything. Please try again, or type your answer.",
            audio_seconds=3.0,
        )

        response = await send(client, session_id, **{"X-Audio-Duration": "3"})

        assert response.status_code == 422
        assert detail(response)["code"] == "no_speech"
        assert "Retry-After" not in response.headers
        assert used(redis, "requests") == 1  # Groq processed it

    async def test_groq_busy_is_429_with_its_wait_and_nothing_is_charged(
        self, client, session_id, voice, redis
    ):
        voice.speech.error = SpeechRateLimited("Voice is busy.", retry_after=20)

        response = await send(client, session_id)

        assert response.status_code == 429
        body = detail(response)
        assert body["code"] == "rate_limited"
        assert body["retry_after"] == 20
        assert response.headers["Retry-After"] == "20"
        assert used(redis, "requests") == 0

    async def test_after_groq_is_busy_calls_wait_without_reaching_groq(
        self, client, session_id, voice, redis
    ):
        voice.speech.error = SpeechRateLimited("Voice is busy.", retry_after=20)
        await send(client, session_id)
        voice.speech.error = None
        reached_groq = len(voice.speech.calls)

        waiting = await send(client, session_id)

        assert waiting.status_code == 429
        body = detail(waiting)
        assert body["code"] == "cooldown"
        assert 1 <= body["retry_after"] <= 20
        assert waiting.headers["Retry-After"] == str(body["retry_after"])
        assert len(voice.speech.calls) == reached_groq

        redis.clock.advance(21)
        assert (await send(client, session_id)).status_code == 200

    async def test_the_users_daily_limit_is_429_with_a_wait(
        self, client, session_id, voice
    ):
        voice.quota.limits = VoiceLimits(stt_user_daily_requests=1)
        assert (await send(client, session_id)).status_code == 200

        response = await send(client, session_id)

        assert response.status_code == 429
        body = detail(response)
        assert body["code"] == "user_daily"
        assert body["retry_after"] >= 1
        assert response.headers["Retry-After"] == str(body["retry_after"])
        assert len(voice.speech.calls) == 1

    async def test_the_organisations_limit_is_429(self, client, session_id, voice):
        voice.quota.limits = VoiceLimits(stt_org_minute_requests=1)
        assert (await send(client, session_id)).status_code == 200

        response = await send(client, session_id)

        assert response.status_code == 429
        assert detail(response)["code"] == "org_minute"

    async def test_voice_switched_off_is_503(self, client, session_id, voice):
        voice.quota.limits = VoiceLimits(enabled=False)

        response = await send(client, session_id)

        assert response.status_code == 503
        assert detail(response)["code"] == "disabled"
        assert voice.speech.calls == []

    async def test_a_longest_answer_of_zero_switches_voice_off(
        self, client, session_id, voice
    ):
        voice.quota.limits = VoiceLimits(max_recording_seconds=0)

        response = await send(client, session_id)

        assert response.status_code == 503
        assert detail(response)["code"] == "disabled"

    async def test_redis_down_fails_closed(self, client, session_id, voice, redis):
        redis.down = True

        response = await send(client, session_id)

        assert response.status_code == 503
        assert detail(response)["code"] == "unavailable"
        assert voice.speech.calls == []

    @pytest.mark.parametrize(
        ("error", "code"),
        [
            (SpeechUnavailable("Voice is unavailable."), "unavailable"),
            (SpeechNotConfigured("Voice is not set up."), "not_configured"),
        ],
    )
    async def test_groq_problems_are_503_and_not_charged(
        self, client, session_id, voice, redis, error, code
    ):
        voice.speech.error = error

        response = await send(client, session_id)

        assert response.status_code == 503
        assert detail(response)["code"] == code
        assert nothing_was_counted(redis)

    async def test_an_unexpected_failure_is_a_500_and_not_charged(
        self, client, session_id, voice, redis
    ):
        voice.speech.error = RuntimeError("boom")
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                url(session_id), content=AUDIO, headers={"Content-Type": TYPE}
            )

        assert response.status_code == 500
        assert "boom" not in response.text
        assert nothing_was_counted(redis)

    async def test_every_voice_error_has_a_message_a_person_can_read(
        self, client, session_id, voice
    ):
        voice.speech.error = NoSpeechDetected("We could not hear anything.")

        body = detail(await send(client, session_id))

        assert body["message"][0].isupper()
        assert "gsk_" not in body["message"]
        assert "Traceback" not in body["message"]


# ── The API page ──────────────────────────────────────────────────────────────


class TestOpenApi:
    @pytest_asyncio.fixture
    async def operation(self, client) -> dict:
        schema = (await client.get("/openapi.json")).json()
        path = "/api/candidate/interview/{session_id}/transcribe"
        return schema["paths"][path]["post"]

    async def test_the_route_is_registered_once(self):
        paths = [
            r.path for r in app.routes if getattr(r, "path", "").endswith("/transcribe")
        ]

        assert paths == ["/api/candidate/interview/{session_id}/transcribe"]

    async def test_the_page_lists_the_success_and_every_error(self, operation):
        assert {"200", "400", "404", "409", "413", "415", "422", "429", "503"} <= set(
            operation["responses"]
        )

    async def test_the_page_describes_a_raw_audio_body(self, operation):
        content = operation["requestBody"]["content"]

        assert operation["requestBody"]["required"] is True
        assert "audio/webm" in content
        assert content["audio/webm"]["schema"] == {
            "type": "string",
            "format": "binary",
        }

    async def test_the_page_lists_the_duration_header(self, operation):
        headers = [p for p in operation["parameters"] if p["in"] == "header"]

        assert [p["name"] for p in headers] == ["x-audio-duration"]
        assert headers[0]["required"] is False

    async def test_errors_are_documented_with_the_error_model(self, operation):
        for code in ("404", "422", "429"):
            schema = operation["responses"][code]["content"]["application/json"][
                "schema"
            ]
            assert schema["$ref"].endswith("/VoiceErrorResponse")


# ── Shutdown ──────────────────────────────────────────────────────────────────


class TestShutdown:
    async def test_the_speech_connection_pool_is_closed_when_the_app_stops(
        self, monkeypatch
    ):
        events: list[str] = []

        async def fake_init_db():
            events.append("start")

        async def fake_close():
            events.append("close")

        monkeypatch.setattr(main, "init_db", fake_init_db)
        monkeypatch.setattr(main, "close_speech_client", fake_close)

        async with main.lifespan(main.app):
            assert events == ["start"]  # still open while the app is running

        assert events == ["start", "close"]
