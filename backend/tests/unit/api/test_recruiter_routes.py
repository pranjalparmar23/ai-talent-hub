"""HTTP-level tests for the recruiter routes.

The graph and service are replaced with fakes, and the logged-in user comes
from a dependency override, so these tests cover routing, role guards, request
validation and response shapes — not the pipeline itself (see
tests/unit/graphs/test_recruiter_graph.py).
"""

import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.recruiter_routes import get_recruiter_graph
from app.main import app
from app.middleware.auth import get_current_user
from app.services.recruiter_service import RecruiterService

RECRUITER = {"id": str(uuid.uuid4()), "email": "r@example.com", "role": "recruiter"}
CANDIDATE = {"id": str(uuid.uuid4()), "email": "c@example.com", "role": "candidate"}
JD_ID = str(uuid.uuid4())
CANDIDATE_ID = str(uuid.uuid4())
JD_TEXT = b"Senior Backend Engineer. We need Python, FastAPI and PostgreSQL skills."


class FakeJDService:
    async def list_user_jds(self, user_id):
        return [{"jd_id": JD_ID, "title": "Backend", "created_at": "2026-01-01T00:00"}]


class FakeService:
    def __init__(self):
        self.jd_service = FakeJDService()
        self.uploads: list[tuple] = []

    async def upload_jd(self, file, recruiter_id):
        self.uploads.append((file.filename, recruiter_id))
        if file.filename == "bad.docx":
            raise HTTPException(status_code=415, detail="Unsupported file type")
        return {"jd_id": JD_ID, "parsed_data": {"title": "T"}, "parse_error": False}


class FakeGraph:
    def __init__(self):
        self.calls: list[tuple] = []
        self.error: HTTPException | None = None

    def _maybe_fail(self):
        if self.error:
            raise self.error

    async def run_full_pipeline(self, jd_id, user_id, top_n=None):
        self.calls.append(("rank", jd_id, user_id, top_n))
        self._maybe_fail()
        return {
            "jd_id": jd_id,
            "jd_title": "Backend",
            "total_candidates": 1,
            "ranked_candidates": [
                {"rank": 1, "candidate_id": CANDIDATE_ID, "match_score": 91.2}
            ],
        }

    async def generate_questions(self, jd_id, candidate_id, user_id):
        self.calls.append(("questions", jd_id, candidate_id, user_id))
        self._maybe_fail()
        return {
            "jd_id": jd_id,
            "candidate_id": candidate_id,
            "questions": [
                {"question": "Q?", "category": "technical", "difficulty": "easy"}
            ],
            "parse_error": False,
        }

    async def get_recommendation(self, jd_id, candidate_id, user_id):
        self.calls.append(("recommendation", jd_id, candidate_id, user_id))
        self._maybe_fail()
        return {
            "jd_id": jd_id,
            "candidate_id": candidate_id,
            "recommendation": "HIRE",
            "confidence": 80,
            "flags": [],
            "reasoning": "Good.",
            "strengths": ["Python"],
            "risks": [],
            "reasoning_source": "llm",
        }


@pytest.fixture
def fakes():
    return FakeService(), FakeGraph()


@pytest_asyncio.fixture
async def http(fakes):
    service, graph = fakes
    app.dependency_overrides[get_current_user] = lambda: RECRUITER
    app.dependency_overrides[RecruiterService] = lambda: service
    app.dependency_overrides[get_recruiter_graph] = lambda: graph
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    for dependency in (get_current_user, RecruiterService, get_recruiter_graph):
        app.dependency_overrides.pop(dependency, None)


ENDPOINTS = [
    ("post", "/api/recruiter/jd/upload"),
    ("get", "/api/recruiter/jds"),
    ("post", f"/api/recruiter/jd/{JD_ID}/rank-candidates"),
    ("get", f"/api/recruiter/jd/{JD_ID}/questions/{CANDIDATE_ID}"),
    ("get", f"/api/recruiter/jd/{JD_ID}/recommendation/{CANDIDATE_ID}"),
]


class TestAccessControl:
    @pytest.mark.parametrize(("method", "path"), ENDPOINTS)
    async def test_candidates_are_forbidden(self, http, method, path):
        app.dependency_overrides[get_current_user] = lambda: CANDIDATE
        response = await http.request(method, path)
        assert response.status_code == 403

    @pytest.mark.parametrize(("method", "path"), ENDPOINTS)
    async def test_anonymous_requests_are_rejected(self, http, method, path):
        del app.dependency_overrides[get_current_user]
        response = await http.request(method, path)
        assert response.status_code == 401


class TestUploadJd:
    async def test_upload_returns_201_and_uses_the_callers_id(self, http, fakes):
        response = await http.post(
            "/api/recruiter/jd/upload",
            files={"file": ("jd.txt", JD_TEXT, "text/plain")},
        )
        assert response.status_code == 201
        assert response.json() == {
            "jd_id": JD_ID,
            "parsed_data": {"title": "T"},
            "parse_error": False,
        }
        assert fakes[0].uploads == [("jd.txt", RECRUITER["id"])]

    async def test_service_errors_pass_through(self, http):
        response = await http.post(
            "/api/recruiter/jd/upload", files={"file": ("bad.docx", b"x", "")}
        )
        assert response.status_code == 415

    async def test_missing_file_is_422(self, http):
        assert (await http.post("/api/recruiter/jd/upload")).status_code == 422


class TestListJds:
    async def test_lists_jds(self, http):
        response = await http.get("/api/recruiter/jds")
        assert response.status_code == 200
        assert response.json()[0]["jd_id"] == JD_ID


class TestRankCandidates:
    async def test_works_without_a_body(self, http, fakes):
        response = await http.post(f"/api/recruiter/jd/{JD_ID}/rank-candidates")
        assert response.status_code == 200
        body = response.json()
        assert body["total_candidates"] == 1
        assert body["ranked_candidates"][0]["rank"] == 1
        assert fakes[1].calls == [("rank", JD_ID, RECRUITER["id"], None)]

    async def test_top_n_is_forwarded(self, http, fakes):
        response = await http.post(
            f"/api/recruiter/jd/{JD_ID}/rank-candidates", json={"top_n": 5}
        )
        assert response.status_code == 200
        assert fakes[1].calls[0][3] == 5

    @pytest.mark.parametrize("top_n", [0, -1, 101, "ten"])
    async def test_invalid_top_n_is_422(self, http, fakes, top_n):
        response = await http.post(
            f"/api/recruiter/jd/{JD_ID}/rank-candidates", json={"top_n": top_n}
        )
        assert response.status_code == 422
        assert fakes[1].calls == []

    async def test_foreign_jd_is_404(self, http, fakes):
        fakes[1].error = HTTPException(status_code=404, detail="JD not found")
        response = await http.post(f"/api/recruiter/jd/{JD_ID}/rank-candidates")
        assert response.status_code == 404

    async def test_unanalysed_jd_is_422(self, http, fakes):
        fakes[1].error = HTTPException(status_code=422, detail="could not be analysed")
        response = await http.post(f"/api/recruiter/jd/{JD_ID}/rank-candidates")
        assert response.status_code == 422


class TestQuestionsAndRecommendation:
    async def test_questions(self, http, fakes):
        response = await http.get(f"/api/recruiter/jd/{JD_ID}/questions/{CANDIDATE_ID}")
        assert response.status_code == 200
        assert response.json()["questions"][0]["category"] == "technical"
        assert fakes[1].calls == [("questions", JD_ID, CANDIDATE_ID, RECRUITER["id"])]

    async def test_recommendation(self, http, fakes):
        response = await http.get(
            f"/api/recruiter/jd/{JD_ID}/recommendation/{CANDIDATE_ID}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["recommendation"] == "HIRE"
        assert body["reasoning_source"] == "llm"
        assert fakes[1].calls == [
            ("recommendation", JD_ID, CANDIDATE_ID, RECRUITER["id"])
        ]

    @pytest.mark.parametrize("kind", ["questions", "recommendation"])
    async def test_unknown_candidate_is_404(self, http, fakes, kind):
        fakes[1].error = HTTPException(status_code=404, detail="Candidate not found")
        response = await http.get(f"/api/recruiter/jd/{JD_ID}/{kind}/{CANDIDATE_ID}")
        assert response.status_code == 404


class TestGraphDependency:
    def test_graph_is_built_once_and_reused(self, monkeypatch):
        import app.api.recruiter_routes as routes

        built = []
        monkeypatch.setattr(
            routes, "RecruiterGraph", lambda: built.append(1) or object()
        )
        routes.get_recruiter_graph.cache_clear()
        first, second = routes.get_recruiter_graph(), routes.get_recruiter_graph()
        routes.get_recruiter_graph.cache_clear()
        assert first is second
        assert built == [1]
