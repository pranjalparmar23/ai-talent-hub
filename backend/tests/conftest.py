import pytest
import asyncio
from httpx import AsyncClient
from app.main import app
from app.database.postgres import engine, Base


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def test_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client(test_db):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def sample_resume_data():
    return {
        "name": "John Doe",
        "email": "john@example.com",
        "skills": ["Python", "FastAPI", "Docker"],
        "experience": 3,
        "projects": [{"name": "ML Pipeline", "description": "Built end-to-end ML pipeline"}],
    }


@pytest.fixture
def sample_jd_data():
    return {
        "title": "Senior Backend Engineer",
        "skills": ["Python", "FastAPI", "Kubernetes", "AWS"],
        "experience_years": 3,
    }
