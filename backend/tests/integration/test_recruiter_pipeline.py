"""Integration test — recruiter pipeline through the real HTTP API and database.

What is real:   FastAPI routes, auth (register/login/JWT), role guards,
                RecruiterService, JDService, the SQL queries, the graph,
                MatchingAgent, RankingAgent and the hiring-verdict logic.
What is faked:  the LLM calls (JD parsing, question text, explanation text)
                and the embedding model, so the test is fast, free and
                deterministic. Real-LLM checks live in test_candidate_flow.py.

Safety: this test writes users, resumes and JDs, so it only runs when the app's
DATABASE_URL points at the *test* database (the same one conftest.py resets).
Locally:
    DATABASE_URL=$TEST_DATABASE_URL pytest tests/integration/test_recruiter_pipeline.py
CI already sets both variables to the same database.
"""

import datetime as dt
import json
import uuid

import pytest
from sqlalchemy import select

from app.agents.recruiter.jd_analyzer import JDAnalyzerAgent
from app.api.recruiter_routes import get_recruiter_graph
from app.database.models import InterviewSession, Resume, User
from app.database.postgres import DATABASE_URL, AsyncSessionLocal
from app.graphs.recruiter_graph import RecruiterGraph
from app.main import app
from tests.conftest import TEST_DATABASE_URL
from tests.unit.agents._fakes import FakeChain, FakeMsg

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL != TEST_DATABASE_URL,
        reason="DATABASE_URL is not the test database — refusing to write test data",
    ),
]

PASSWORD = "SecurePass123"
T0 = dt.datetime(2026, 1, 1)

JD_TEXT = (
    "Senior Backend Engineer. 3+ years experience. We need Python, PostgreSQL "
    "and Docker; Kafka is a plus. You will build and own our ingestion APIs."
)
PARSED_JD = {
    "title": "Senior Backend Engineer",
    "skills_required": ["Python", "PostgreSQL", "Docker"],
    "skills_preferred": ["Kafka"],
    "experience_years_min": 3,
    "responsibilities": ["Build ingestion APIs"],
    "_parse_error": False,
}


class FakeEmbedder:
    def embed_batch(self, texts):
        return [[1.0, 0.0] for _ in texts]


async def fake_analyze(self, raw_text):
    return dict(PARSED_JD)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """These tests make many requests from one client; start each with a clean slate."""
    from app.middleware import rate_limit

    rate_limit._request_counts.clear()


@pytest.fixture
def graph(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(JDAnalyzerAgent, "analyze", fake_analyze)

    built = RecruiterGraph()
    built.matcher.embedder = FakeEmbedder()
    built.question_gen.chain = FakeChain(
        response=FakeMsg(
            json.dumps(
                {
                    "questions": [
                        {
                            "question": "How would you shard a Postgres table?",
                            "category": "technical",
                            "difficulty": "hard",
                            "rationale": "Core JD skill",
                        }
                    ]
                }
            )
        )
    )
    built.recommender.chain = FakeChain(
        response=FakeMsg(
            json.dumps({"reasoning": "Solid.", "strengths": ["Python"], "risks": []})
        )
    )
    app.dependency_overrides[get_recruiter_graph] = lambda: built
    yield built
    app.dependency_overrides.pop(get_recruiter_graph, None)


async def register_and_login(client, role: str) -> dict:
    email = f"{role}-{uuid.uuid4().hex[:10]}@example.com"
    created = await client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "role": role},
    )
    assert created.status_code == 201, created.text
    login = await client.post(
        "/api/auth/login", data={"username": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    return {
        "id": str(user.id),
        "headers": {"Authorization": f"Bearer {login.json()['access_token']}"},
    }


async def seed_candidate(name, skills, years, *, interview=None, resumes=None):
    """A candidate user + resume(s) + optional interview sessions, via the DB."""
    async with AsyncSessionLocal() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"cand-{uuid.uuid4().hex[:10]}@example.com",
            hashed_password="not-used",
            role="candidate",
        )
        db.add(user)
        await db.flush()
        for created_at, data in resumes or [
            (T0, {"name": name, "skills": skills, "experience_years": years})
        ]:
            db.add(Resume(user_id=user.id, parsed_data=data, created_at=created_at))
        for created_at, feedback in interview or []:
            db.add(
                InterviewSession(
                    user_id=user.id,
                    company="Acme",
                    role="Backend",
                    history=[],
                    feedback=feedback,
                    created_at=created_at,
                )
            )
        await db.commit()
        return str(user.id)


async def upload_jd(client, recruiter) -> str:
    response = await client.post(
        "/api/recruiter/jd/upload",
        files={"file": ("jd.txt", JD_TEXT.encode(), "text/plain")},
        headers=recruiter["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()["jd_id"]


def mine(response_json, ids):
    """Ranked rows for our own candidates (the table may hold other test users)."""
    return [c for c in response_json["ranked_candidates"] if c["candidate_id"] in ids]


# ── Tests ─────────────────────────────────────────────────────────────────


async def test_full_recruiter_flow(client, graph):
    recruiter = await register_and_login(client, "recruiter")
    jd_id = await upload_jd(client, recruiter)

    listing = await client.get("/api/recruiter/jds", headers=recruiter["headers"])
    assert [j["jd_id"] for j in listing.json()] == [jd_id]

    strong = await seed_candidate(
        "Strong",
        ["Python", "PostgreSQL", "Docker", "Kafka"],
        6,
        interview=[(T0, {"overall_score": 8.0})],
    )
    middle = await seed_candidate("Middle", ["Python", "PostgreSQL"], 4)
    weak = await seed_candidate("Weak", ["Excel"], 1)

    ranking = await client.post(
        f"/api/recruiter/jd/{jd_id}/rank-candidates", headers=recruiter["headers"]
    )
    assert ranking.status_code == 200, ranking.text
    body = ranking.json()
    assert body["jd_title"] == "Senior Backend Engineer"
    rows = mine(body, {strong, middle, weak})
    assert [r["candidate_id"] for r in rows] == [strong, middle, weak]
    assert rows[0]["interview_score"] == 8.0
    assert rows[1]["interview_score"] is None
    assert "Docker" in rows[1]["missing_skills"]
    assert "resume_data" not in rows[0]

    questions = await client.get(
        f"/api/recruiter/jd/{jd_id}/questions/{middle}", headers=recruiter["headers"]
    )
    assert questions.status_code == 200, questions.text
    assert questions.json()["questions"][0]["category"] == "technical"

    verdicts = {}
    for name, candidate_id in (("strong", strong), ("weak", weak)):
        response = await client.get(
            f"/api/recruiter/jd/{jd_id}/recommendation/{candidate_id}",
            headers=recruiter["headers"],
        )
        assert response.status_code == 200, response.text
        verdicts[name] = response.json()
    assert verdicts["strong"]["recommendation"] == "HIRE"
    assert verdicts["strong"]["interview_score"] == 8.0
    assert verdicts["weak"]["recommendation"] == "REJECT"


async def test_only_the_latest_resume_and_latest_interview_count(client, graph):
    recruiter = await register_and_login(client, "recruiter")
    jd_id = await upload_jd(client, recruiter)

    improved = await seed_candidate(
        "Improved",
        [],
        0,
        resumes=[
            (T0, {"name": "Improved", "skills": ["Excel"], "experience_years": 1}),
            (
                T0 + dt.timedelta(days=30),
                {
                    "name": "Improved",
                    "skills": ["Python", "PostgreSQL", "Docker"],
                    "experience_years": 4,
                },
            ),
        ],
        interview=[
            (T0, {"overall_score": 3.0}),
            (T0 + dt.timedelta(days=10), {"overall_score": 7.5}),
            (T0 + dt.timedelta(days=20), {"overall_score": 9.0, "_parse_error": True}),
            (T0 + dt.timedelta(days=25), None),
        ],
    )

    ranking = await client.post(
        f"/api/recruiter/jd/{jd_id}/rank-candidates", headers=recruiter["headers"]
    )
    [row] = mine(ranking.json(), {improved})
    assert set(row["matching_skills"]) == {"Python", "PostgreSQL", "Docker"}
    assert row["interview_score"] == 7.5  # newest *usable* session


async def test_recruiters_and_resume_less_users_are_not_candidates(client, graph):
    recruiter = await register_and_login(client, "recruiter")
    jd_id = await upload_jd(client, recruiter)
    async with AsyncSessionLocal() as db:
        db.add(
            Resume(
                user_id=uuid.UUID(recruiter["id"]),
                parsed_data={"name": "Recruiter", "skills": ["Python"]},
                created_at=T0,
            )
        )
        await db.commit()

    ranking = await client.post(
        f"/api/recruiter/jd/{jd_id}/rank-candidates", headers=recruiter["headers"]
    )
    ids = {c["candidate_id"] for c in ranking.json()["ranked_candidates"]}
    assert recruiter["id"] not in ids


async def test_top_n_limits_rows_but_not_total(client, graph):
    recruiter = await register_and_login(client, "recruiter")
    jd_id = await upload_jd(client, recruiter)
    for index in range(3):
        await seed_candidate(f"C{index}", ["Python"], index + 1)

    response = await client.post(
        f"/api/recruiter/jd/{jd_id}/rank-candidates",
        json={"top_n": 2},
        headers=recruiter["headers"],
    )
    body = response.json()
    assert len(body["ranked_candidates"]) == 2
    assert body["total_candidates"] >= 3


async def test_access_control(client, graph):
    owner = await register_and_login(client, "recruiter")
    other = await register_and_login(client, "recruiter")
    candidate_user = await register_and_login(client, "candidate")
    jd_id = await upload_jd(client, owner)
    target = await seed_candidate("Target", ["Python"], 3)

    paths = [
        ("post", f"/api/recruiter/jd/{jd_id}/rank-candidates"),
        ("get", f"/api/recruiter/jd/{jd_id}/questions/{target}"),
        ("get", f"/api/recruiter/jd/{jd_id}/recommendation/{target}"),
    ]
    for method, path in paths:
        # another recruiter: the JD does not exist for them
        stranger = await client.request(method, path, headers=other["headers"])
        assert stranger.status_code == 404, (path, stranger.text)
        # a candidate account: not allowed at all
        forbidden = await client.request(
            method, path, headers=candidate_user["headers"]
        )
        assert forbidden.status_code == 403, (path, forbidden.text)
        # no token
        assert (await client.request(method, path)).status_code == 401

    other_jds = await client.get("/api/recruiter/jds", headers=other["headers"])
    assert other_jds.json() == []


async def test_bad_ids_and_unknown_candidates_are_404(client, graph):
    recruiter = await register_and_login(client, "recruiter")
    jd_id = await upload_jd(client, recruiter)

    missing_jd = await client.post(
        f"/api/recruiter/jd/{uuid.uuid4()}/rank-candidates",
        headers=recruiter["headers"],
    )
    malformed_jd = await client.post(
        "/api/recruiter/jd/not-a-uuid/rank-candidates", headers=recruiter["headers"]
    )
    unknown_candidate = await client.get(
        f"/api/recruiter/jd/{jd_id}/recommendation/{uuid.uuid4()}",
        headers=recruiter["headers"],
    )
    malformed_candidate = await client.get(
        f"/api/recruiter/jd/{jd_id}/questions/nope", headers=recruiter["headers"]
    )
    assert [
        r.status_code
        for r in (missing_jd, malformed_jd, unknown_candidate, malformed_candidate)
    ] == [404, 404, 404, 404]


async def test_upload_validation_over_http(client, graph):
    recruiter = await register_and_login(client, "recruiter")
    headers = recruiter["headers"]

    async def post(name, content, content_type=""):
        return await client.post(
            "/api/recruiter/jd/upload",
            files={"file": (name, content, content_type)},
            headers=headers,
        )

    assert (await post("empty.txt", b"", "text/plain")).status_code == 400
    assert (await post("jd.docx", b"PK\x03\x04 zip")).status_code == 415
    assert (await post("short.txt", b"too short", "text/plain")).status_code == 422
    assert (await post("ok.txt", JD_TEXT.encode(), "text/plain")).status_code == 201
