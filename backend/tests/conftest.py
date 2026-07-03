import os
import asyncio
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.database.postgres import Base, get_db


# Use a separate test database so tests never touch dev data
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:root@localhost:5432/talent_hub_test",
)

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False, pool_pre_ping=True)
TestSessionLocal = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Override the DB dependency so the app uses test DB during tests
app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="session")
def event_loop():
    """Single event loop for the whole test session (pytest-asyncio quirk)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db():
    """Create all tables before tests, drop them after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture
async def client():
    """HTTP client that talks to the FastAPI app in-process (no real network)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Reusable test data fixtures ──────────────────────────────

@pytest.fixture
def user_payload():
    return {
        "email": "testuser@example.com",
        "password": "SecurePass123",
        "role": "candidate",
    }


@pytest.fixture
def recruiter_payload():
    return {
        "email": "recruiter@example.com",
        "password": "SecurePass123",
        "role": "recruiter",
    }


@pytest.fixture
def sample_resume_data():
    return {
        "name": "Pranjal Parmar",
        "email": "pranjal@example.com",
        "skills": ["Python", "FastAPI", "Docker", "PostgreSQL"],
        "experience_years": 3,
        "projects": [
            {"name": "AI Talent Hub", "description": "Full-stack AI recruitment platform"}
        ],
    }


@pytest.fixture
def sample_jd_data():
    return {
        "title": "Senior Backend Engineer",
        "skills": ["Python", "FastAPI", "Kubernetes", "AWS", "Redis"],
        "experience_years": 3,
    }