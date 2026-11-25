"""Route-level tests for the interview endpoints.

The service tests call InterviewService directly, so they cannot see what the route
does to its answer: FastAPI keeps only the fields of the route's `response_model` and
drops the rest without any error. These tests go through the real app, so a field the
service returns but the response model lacks shows up here.

The service itself is real, with its Postgres and Redis access replaced by in-memory
fakes, so what is saved by POST /interview/start is what GET /interview/{id} reads.
"""

import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.agents.shared.memory_agent import MemoryAgent
from app.main import app
from app.middleware.auth import get_current_user
from app.services.interview_service import InterviewService
from tests.unit.agents.test_memory_agent import FakeRedis
from tests.unit.services.test_interview_service import NOTICE, SOURCE, FakeInterviewer

USER_ID = str(uuid.uuid4())
OTHER_USER_ID = str(uuid.uuid4())
START = {"company": "Amazon", "role": "Backend Engineer"}


def _make_service(monkeypatch) -> InterviewService:
    """A real InterviewService whose storage lives in memory."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    service = InterviewService()
    service.interviewer = FakeInterviewer()
    redis = FakeRedis()
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

    service._create_row = create_row
    service._get_row = get_row
    return service


@pytest.fixture
def service(monkeypatch):
    return _make_service(monkeypatch)


@pytest_asyncio.fixture
async def client(service):
    """The real app, signed in as USER_ID, using the in-memory service."""
    app.dependency_overrides[InterviewService] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: {
        "id": USER_ID,
        "email": "candidate@example.com",
        "role": "candidate",
    }
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(InterviewService, None)
        app.dependency_overrides.pop(get_current_user, None)


async def _start(client) -> dict:
    response = await client.post("/api/candidate/interview/start", json=START)
    assert response.status_code == 200, response.text
    return response.json()


class TestStartRoute:
    @pytest.mark.asyncio
    async def test_returns_the_sources(self, client, service):
        service.interviewer = FakeInterviewer(sources=[SOURCE])

        body = await _start(client)

        assert body["sources"] == [SOURCE]
        assert body["notice"] is None

    @pytest.mark.asyncio
    async def test_returns_the_notice(self, client, service):
        service.interviewer = FakeInterviewer(notice=NOTICE)

        body = await _start(client)

        assert body["sources"] == []
        assert body["notice"] == NOTICE


class TestSessionRoute:
    @pytest.mark.asyncio
    async def test_returns_the_saved_sources(self, client, service):
        service.interviewer = FakeInterviewer(sources=[SOURCE])
        started = await _start(client)

        response = await client.get(f"/api/candidate/interview/{started['session_id']}")

        assert response.status_code == 200
        body = response.json()
        assert body["sources"] == [SOURCE]
        assert body["notice"] is None

    @pytest.mark.asyncio
    async def test_returns_the_saved_notice(self, client, service):
        service.interviewer = FakeInterviewer(notice=NOTICE)
        started = await _start(client)

        body = (
            await client.get(f"/api/candidate/interview/{started['session_id']}")
        ).json()

        assert body["sources"] == []
        assert body["notice"] == NOTICE

    @pytest.mark.asyncio
    async def test_a_session_with_nothing_saved_still_has_both_fields(self, client):
        started = await _start(client)

        body = (
            await client.get(f"/api/candidate/interview/{started['session_id']}")
        ).json()

        assert body["sources"] == []
        assert body["notice"] is None

    @pytest.mark.asyncio
    async def test_no_field_the_service_returns_is_dropped_by_the_route(
        self, client, service
    ):
        """Guards the whole class of bug: a field added to the service but not to the
        response model is removed by FastAPI without any error."""
        service.interviewer = FakeInterviewer(sources=[SOURCE], notice=NOTICE)
        started = await _start(client)
        session_id = started["session_id"]

        from_service = await service.get_session(session_id, USER_ID)
        from_route = (await client.get(f"/api/candidate/interview/{session_id}")).json()

        assert set(from_service) <= set(from_route)

    @pytest.mark.asyncio
    async def test_someone_elses_session_is_not_found(self, client, service):
        started = await _start(client)
        app.dependency_overrides[get_current_user] = lambda: {
            "id": OTHER_USER_ID,
            "email": "other@example.com",
            "role": "candidate",
        }

        response = await client.get(f"/api/candidate/interview/{started['session_id']}")

        assert response.status_code == 404
