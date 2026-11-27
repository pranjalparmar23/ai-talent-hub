"""Route-level tests for POST /api/candidate/interview/{id}/speak.

These go through the real app, so they see what the unit tests cannot: sign-in,
ownership, the optional request body, the audio response and its headers, the error
bodies a browser would receive, CORS, the OpenAPI page and shutdown.

What is real: the app and its middleware, InterviewService (its Postgres and Redis
access replaced by in-memory fakes), VoiceService, the quota counters and the clip
cache (on a fake Redis and a fake clock). What is fake: Groq (FakeTts, FakeSpeech), so
nothing here uses the network or a key.
"""

import logging
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import main
from app.main import app
from app.middleware import rate_limit
from app.middleware.auth import get_current_user
from app.services import speech_service as ss
from app.services.interview_service import InterviewService
from app.services.tts_service import SilentAudio, TextRejected
from app.services.voice_cache import ClipCache
from app.services.voice_quota_service import VoiceLimits, VoiceQuotaService
from app.services.voice_service import VoiceService
from tests.unit.api.test_transcribe_routes import (
    OTHER_USER_ID,
    START,
    USER_ID,
    _interview_service,
    candidate,
    finish_the_interview,
)
from tests.unit.services.test_voice_quota_service import FakeClock, FakeRedis
from tests.unit.services.test_voice_service import (
    CLIP,
    FakeSpeech,
    FakeTts,
    failed_after,
    no_tts_counters,
    tts_counts,
)

pytestmark = pytest.mark.asyncio

OPENING = "Opening for Backend Engineer at Amazon"  # what the fake interviewer asks
ORIGIN = "http://localhost:5173"


def url(session_id: str) -> str:
    return f"/api/candidate/interview/{session_id}/speak"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def no_clips_in_flight():
    from app.services import voice_service

    voice_service._inflight.clear()
    yield
    voice_service._inflight.clear()


@pytest.fixture
def redis():
    return FakeRedis(FakeClock())


@pytest.fixture
def voice(redis):
    service = VoiceService()
    service.tts_provider = "orpheus"
    service.tts = FakeTts(redis)
    service.speech = FakeSpeech(redis)
    service.cache = ClipCache()
    service.cache.client = redis
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
    rate_limit._request_counts.clear()  # the per-IP limiter counts the whole run
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


async def answer_once(client, session_id) -> None:
    response = await client.post(
        f"/api/candidate/interview/{session_id}/respond", json={"response": "my answer"}
    )
    assert response.status_code == 200, response.text


async def speak(client, session_id, body=None, **kwargs):
    if body is None:
        return await client.post(url(session_id), **kwargs)
    return await client.post(url(session_id), json=body, **kwargs)


def detail(response) -> dict:
    """The error body: always an object with a code and a message."""
    body = response.json()["detail"]
    assert isinstance(body, dict), body
    assert set(body) <= {"code", "message", "retry_after"}
    assert body["code"] and body["message"]
    return body


# ── A question read aloud ─────────────────────────────────────────────────────


class TestSuccess:
    async def test_returns_the_clip_as_a_wav_file(self, client, session_id):
        response = await speak(client, session_id)

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "audio/wav"
        assert response.content == CLIP.to_bytes()
        assert response.content.startswith(b"RIFF")
        assert int(response.headers["content-length"]) == len(response.content)

    async def test_the_first_request_makes_the_clip_and_the_next_finds_it(
        self, client, session_id, voice
    ):
        first = await speak(client, session_id)
        second = await speak(client, session_id)

        assert first.headers["x-voice-cache"] == "miss"
        assert second.headers["x-voice-cache"] == "hit"
        assert second.content == first.content
        assert voice.tts.calls == [OPENING]

    async def test_the_clip_is_private_and_not_kept_by_browsers(
        self, client, session_id
    ):
        response = await speak(client, session_id)

        assert response.headers["cache-control"] == "private, no-store"

    async def test_without_a_body_the_latest_question_is_read(
        self, client, session_id, voice
    ):
        await answer_once(client, session_id)

        await speak(client, session_id)

        assert voice.tts.calls == ["Follow-up #2"]

    @pytest.mark.parametrize(
        "body", [None, {}, {"message_index": None}], ids=["none", "empty", "null"]
    )
    async def test_an_empty_body_means_the_latest_question(
        self, client, session_id, voice, body
    ):
        await answer_once(client, session_id)

        response = await client.post(url(session_id), json=body)

        assert response.status_code == 200
        assert voice.tts.calls == ["Follow-up #2"]

    async def test_an_empty_json_body_without_content_is_accepted(
        self, client, session_id
    ):
        response = await client.post(
            url(session_id), content=b"", headers={"content-type": "application/json"}
        )

        assert response.status_code == 200

    async def test_an_earlier_question_can_be_read_again(
        self, client, session_id, voice
    ):
        await answer_once(client, session_id)

        response = await speak(client, session_id, {"message_index": 0})

        assert response.status_code == 200
        assert voice.tts.calls == [OPENING]

    async def test_the_question_is_read_by_its_place_in_the_messages(
        self, client, session_id, voice
    ):
        await answer_once(client, session_id)
        await answer_once(client, session_id)
        messages = (await client.get(f"/api/candidate/interview/{session_id}")).json()[
            "messages"
        ]
        assert [m["role"] for m in messages] == [
            "interviewer",
            "candidate",
            "interviewer",
            "candidate",
            "interviewer",
        ]

        await speak(client, session_id, {"message_index": 2})

        assert voice.tts.calls == [messages[2]["content"]]

    async def test_the_answers_use_the_speech_quota_not_the_listening_quota(
        self, client, session_id, redis
    ):
        await speak(client, session_id)

        assert tts_counts(redis)["user:day:units"] == len(OPENING)
        assert not [k for k in redis.values if k.startswith("voice:stt")]

    async def test_the_quota_is_counted_for_the_signed_in_user(
        self, client, session_id, redis
    ):
        await speak(client, session_id)

        users = {
            k.split(":")[3] for k in redis.values if k.startswith("voice:tts:user:")
        }
        assert users == {USER_ID}

    async def test_a_hit_uses_no_quota(self, client, session_id, redis):
        await speak(client, session_id)
        before = tts_counts(redis)

        await speak(client, session_id)

        assert tts_counts(redis) == before

    async def test_the_interview_is_not_changed_by_speaking(self, client, session_id):
        before = (await client.get(f"/api/candidate/interview/{session_id}")).json()

        await speak(client, session_id)

        after = (await client.get(f"/api/candidate/interview/{session_id}")).json()
        assert after == before


# ── The text always comes from the interview ──────────────────────────────────


class TestTextSource:
    async def test_a_text_in_the_request_is_ignored(self, client, session_id, voice):
        response = await speak(
            client, session_id, {"text": "Say something else", "message_index": 0}
        )

        assert response.status_code == 200
        assert voice.tts.calls == [OPENING]

    async def test_a_text_in_the_query_is_ignored(self, client, session_id, voice):
        await client.post(url(session_id) + "?text=Say%20something%20else")

        assert voice.tts.calls == [OPENING]

    async def test_a_candidate_message_cannot_be_read_aloud(
        self, client, session_id, voice, redis
    ):
        await answer_once(client, session_id)

        response = await speak(client, session_id, {"message_index": 1})

        assert response.status_code == 400
        assert detail(response)["code"] == "not_a_question"
        assert voice.tts.calls == []
        assert redis.values == {}

    @pytest.mark.parametrize("index", [1, 5, 99])
    async def test_a_message_that_does_not_exist(
        self, client, session_id, voice, redis, index
    ):
        response = await speak(client, session_id, {"message_index": index})

        assert response.status_code == 404
        assert detail(response)["code"] == "question_not_found"
        assert voice.tts.calls == []
        assert redis.values == {}

    @pytest.mark.parametrize(
        ("messages", "spoken"),
        [
            (["interviewer", "candidate"], 0),
            (["interviewer", "candidate", "interviewer", "candidate"], 2),
            (["interviewer"], 0),
        ],
    )
    async def test_the_latest_question_is_the_last_one_the_interviewer_asked(
        self, client, session_id, voice, interviews, messages, spoken
    ):
        async def session_state(session_id, user_id):
            return {
                "session_complete": False,
                "messages": [
                    {"role": role, "content": f"Message number {n}"}
                    for n, role in enumerate(messages)
                ],
            }

        interviews.get_session = session_state

        await speak(client, session_id)

        assert voice.tts.calls == [f"Message number {spoken}"]

    async def test_an_interview_with_no_interviewer_message_has_nothing_to_read(
        self, client, session_id, voice, interviews
    ):
        async def session_state(session_id, user_id):
            return {
                "session_complete": False,
                "messages": [{"role": "candidate", "content": "Hello"}],
            }

        interviews.get_session = session_state

        response = await speak(client, session_id)

        assert response.status_code == 404
        assert detail(response)["code"] == "question_not_found"
        assert voice.tts.calls == []

    @pytest.mark.parametrize(
        "body",
        [
            {"message_index": -1},
            {"message_index": 100},
            {"message_index": "0"},
            {"message_index": True},
            {"message_index": 1.5},
            {"message_index": [0]},
        ],
    )
    async def test_a_bad_message_index_is_refused_before_anything_happens(
        self, client, session_id, voice, redis, body
    ):
        response = await speak(client, session_id, body)

        assert response.status_code == 422
        assert voice.tts.calls == []
        assert redis.values == {}

    async def test_a_body_that_is_not_json_is_refused(self, client, session_id, voice):
        response = await client.post(
            url(session_id),
            content=b"not json",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 422
        assert voice.tts.calls == []


# ── Who may call it, and when ─────────────────────────────────────────────────


class TestAccess:
    async def test_it_needs_a_signed_in_user(self, client, session_id, voice):
        app.dependency_overrides.pop(get_current_user)

        response = await speak(client, session_id)

        assert response.status_code == 401
        assert voice.tts.calls == []

    async def test_someone_elses_interview_is_not_found(
        self, client, session_id, voice, redis
    ):
        app.dependency_overrides[get_current_user] = lambda: candidate(OTHER_USER_ID)

        response = await speak(client, session_id)

        assert response.status_code == 404
        assert detail(response)["code"] == "not_found"
        assert voice.tts.calls == []
        assert redis.values == {}  # no quota and no cache was touched either

    async def test_someone_elses_clip_cannot_be_fetched_by_guessing(
        self, client, session_id, voice
    ):
        await speak(client, session_id)  # now cached
        app.dependency_overrides[get_current_user] = lambda: candidate(OTHER_USER_ID)

        response = await speak(client, session_id)

        assert response.status_code == 404

    async def test_an_unknown_interview_is_not_found(self, client, voice, redis):
        response = await speak(client, str(uuid.uuid4()))

        assert response.status_code == 404
        assert detail(response)["code"] == "not_found"
        assert redis.values == {}

    async def test_an_invalid_id_is_not_found(self, client):
        response = await speak(client, "not-a-uuid")

        assert response.status_code == 404
        assert detail(response)["code"] == "not_found"

    async def test_a_finished_interview_is_refused(
        self, client, session_id, voice, redis
    ):
        await finish_the_interview(client, session_id)

        response = await speak(client, session_id)

        assert response.status_code == 409
        assert detail(response)["code"] == "interview_complete"
        assert voice.tts.calls == []
        assert tts_counts(redis) == {}


# ── When a spoken question is not available ───────────────────────────────────


class TestErrors:
    async def test_the_browser_provider_has_no_spoken_questions(
        self, client, session_id, voice
    ):
        voice.tts_provider = "browser"

        response = await speak(client, session_id)

        assert response.status_code == 503
        assert detail(response)["code"] == "tts_off"
        assert "retry-after" not in response.headers

    async def test_voice_switched_off(self, client, session_id, voice):
        voice.quota.limits = VoiceLimits(enabled=False)

        response = await speak(client, session_id)

        assert response.status_code == 503
        assert detail(response)["code"] == "disabled"

    async def test_the_user_limit(self, client, session_id, voice, redis):
        voice.quota.limits = VoiceLimits(tts_user_daily_chars=10)

        response = await speak(client, session_id)

        body = detail(response)
        assert response.status_code == 429
        assert body["code"] == "user_daily"
        assert response.headers["retry-after"] == str(body["retry_after"])
        assert "screen" in body["message"]
        assert voice.tts.calls == []
        assert no_tts_counters(redis)

    async def test_a_limit_does_not_stop_a_question_that_is_already_made(
        self, client, session_id, voice
    ):
        await speak(client, session_id)
        voice.quota.limits = VoiceLimits(tts_user_daily_chars=1)

        response = await speak(client, session_id)

        assert response.status_code == 200
        assert response.headers["x-voice-cache"] == "hit"

    async def test_a_rate_limit_from_groq_starts_a_cooldown(
        self, client, session_id, voice, redis
    ):
        voice.tts.error = ss.SpeechRateLimited("busy", retry_after=9.0)

        response = await speak(client, session_id)

        assert response.status_code == 429
        assert detail(response) == {
            "code": "rate_limited",
            "message": "busy",
            "retry_after": 9,
        }
        assert response.headers["retry-after"] == "9"
        assert no_tts_counters(redis)
        again = await speak(client, session_id)
        assert detail(again)["code"] == "cooldown"
        assert detail(again)["retry_after"] == 9

    async def test_a_silent_clip_is_a_bad_gateway_and_is_charged(
        self, client, session_id, voice, redis
    ):
        voice.tts.error = failed_after(SilentAudio("silent"), len(OPENING))

        response = await speak(client, session_id)

        assert response.status_code == 502
        assert detail(response) == {"code": "silent_audio", "message": "silent"}
        assert tts_counts(redis)["user:day:units"] == len(OPENING)

    @pytest.mark.parametrize(
        ("error", "status_code", "code"),
        [
            (ss.SpeechNotConfigured("no key"), 503, "not_configured"),
            (ss.SpeechUnavailable("down"), 503, "unavailable"),
            (TextRejected("no", status=400), 400, "text_rejected"),
            (TextRejected("long", status=413), 413, "text_rejected"),
        ],
    )
    async def test_other_errors_keep_their_status_and_code(
        self, client, session_id, voice, redis, error, status_code, code
    ):
        voice.tts.error = failed_after(error, 0)

        response = await speak(client, session_id)

        assert response.status_code == status_code
        assert detail(response) == {"code": code, "message": str(error)}
        assert no_tts_counters(redis)

    async def test_redis_down(self, client, session_id, voice, redis):
        redis.down = True

        response = await speak(client, session_id)

        # the interview itself does not use this Redis, so the quota guard is what
        # answers, and it fails closed
        assert response.status_code == 503
        assert detail(response)["code"] == "unavailable"
        assert voice.tts.calls == []

    async def test_a_question_that_is_too_long_to_read_aloud(
        self, client, session_id, voice, interviews
    ):
        long_text = "word " * 200

        async def long_session(session_id, user_id):
            return {
                "session_complete": False,
                "messages": [{"role": "interviewer", "content": long_text}],
            }

        interviews.get_session = long_session

        response = await speak(client, session_id)

        assert response.status_code == 413
        assert detail(response)["code"] == "text_rejected"
        assert voice.tts.calls == []

    async def test_an_unexpected_error_is_a_plain_500_and_releases_the_quota(
        self, session_id, voice, redis, interviews
    ):
        voice.tts.error = RuntimeError("bug")
        app.dependency_overrides[InterviewService] = lambda: interviews
        app.dependency_overrides[VoiceService] = lambda: voice
        app.dependency_overrides[get_current_user] = lambda: candidate(USER_ID)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                rate_limit._request_counts.clear()
                started = await client.post(
                    "/api/candidate/interview/start", json=START
                )
                response = await client.post(url(started.json()["session_id"]))
        finally:
            for dependency in (InterviewService, VoiceService, get_current_user):
                app.dependency_overrides.pop(dependency, None)

        assert response.status_code == 500
        assert "bug" not in response.text
        assert no_tts_counters(redis)


# ── What a browser can read ───────────────────────────────────────────────────


class TestCors:
    async def test_the_cache_and_wait_headers_can_be_read_by_the_app(
        self, client, session_id
    ):
        response = await client.post(url(session_id), headers={"Origin": ORIGIN})

        exposed = response.headers["access-control-expose-headers"].lower()
        assert "x-voice-cache" in exposed
        assert "retry-after" in exposed
        assert response.headers["access-control-allow-origin"] == ORIGIN

    async def test_the_wait_header_is_exposed_on_errors_too(
        self, client, session_id, voice
    ):
        voice.quota.limits = VoiceLimits(tts_user_daily_chars=10)

        response = await client.post(url(session_id), headers={"Origin": ORIGIN})

        assert response.status_code == 429
        assert (
            "retry-after" in response.headers["access-control-expose-headers"].lower()
        )

    async def test_the_preflight_is_allowed(self, client, session_id):
        response = await client.options(
            url(session_id),
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

        assert response.status_code == 200
        assert "POST" in response.headers["access-control-allow-methods"]

    async def test_another_site_is_not_allowed(self, client, session_id):
        response = await client.post(
            url(session_id), headers={"Origin": "https://evil.example"}
        )

        assert "access-control-allow-origin" not in response.headers


# ── Privacy ───────────────────────────────────────────────────────────────────


class TestPrivacy:
    async def test_nothing_spoken_is_logged(self, client, session_id, voice, caplog):
        with caplog.at_level(logging.DEBUG):
            await speak(client, session_id)
            await speak(client, session_id)
            voice.quota.limits = VoiceLimits(tts_user_daily_chars=1)
            await answer_once(client, session_id)
            await speak(client, session_id)

        assert "Voice speak: ok" in caplog.text
        assert "Voice speak: cache hit" in caplog.text
        assert "Voice speak: denied (user_daily)" in caplog.text
        assert OPENING not in caplog.text
        assert "Follow-up #2" not in caplog.text
        assert USER_ID not in caplog.text


# ── The API page ──────────────────────────────────────────────────────────────


class TestOpenApi:
    @pytest_asyncio.fixture
    async def operation(self, client) -> dict:
        schema = (await client.get("/openapi.json")).json()
        path = "/api/candidate/interview/{session_id}/speak"
        return schema["paths"][path]["post"]

    async def test_the_route_is_registered_once(self):
        paths = [
            r.path for r in app.routes if getattr(r, "path", "").endswith("/speak")
        ]

        assert paths == ["/api/candidate/interview/{session_id}/speak"]

    async def test_the_page_lists_the_success_and_every_error(self, operation):
        assert {"200", "400", "404", "409", "413", "422", "429", "502", "503"} <= set(
            operation["responses"]
        )

    async def test_the_success_is_audio_with_the_cache_header(self, operation):
        ok = operation["responses"]["200"]

        assert list(ok["content"]) == ["audio/wav"]
        assert ok["content"]["audio/wav"]["schema"] == {
            "type": "string",
            "format": "binary",
        }
        assert ok["headers"]["X-Voice-Cache"]["schema"]["enum"] == ["hit", "miss"]

    async def test_the_body_is_optional_and_has_only_an_index(self, client, operation):
        schema = (await client.get("/openapi.json")).json()
        body = operation["requestBody"]

        assert body.get("required", False) is False
        ref = body["content"]["application/json"]["schema"]
        name = (ref.get("$ref") or ref["anyOf"][0]["$ref"]).rsplit("/", 1)[-1]
        assert name == "SpeakRequest"
        assert list(schema["components"]["schemas"]["SpeakRequest"]["properties"]) == [
            "message_index"
        ]

    async def test_errors_are_documented_with_the_error_model(self, operation):
        for code in ("404", "429", "502"):
            schema = operation["responses"][code]["content"]["application/json"][
                "schema"
            ]
            assert schema["$ref"].endswith("/VoiceErrorResponse")


# ── Shutdown ──────────────────────────────────────────────────────────────────


class TestShutdown:
    @pytest.fixture
    def events(self, monkeypatch) -> list[str]:
        events: list[str] = []

        async def fake_init_db():
            events.append("start")

        async def fake_close_speech():
            events.append("close speech")

        async def fake_close_tts():
            events.append("close tts")

        monkeypatch.setattr(main, "init_db", fake_init_db)
        monkeypatch.setattr(main, "close_speech_client", fake_close_speech)
        monkeypatch.setattr(main, "close_tts_client", fake_close_tts)
        return events

    async def test_both_connection_pools_are_closed_when_the_app_stops(self, events):
        async with main.lifespan(main.app):
            assert events == ["start"]  # still open while the app is running

        assert events == ["start", "close speech", "close tts"]

    async def test_the_second_pool_is_closed_even_if_the_first_fails(
        self, events, monkeypatch
    ):
        async def broken_close():
            events.append("close speech")
            raise RuntimeError("already closed")

        monkeypatch.setattr(main, "close_speech_client", broken_close)

        with pytest.raises(RuntimeError):
            async with main.lifespan(main.app):
                pass

        assert events == ["start", "close speech", "close tts"]
