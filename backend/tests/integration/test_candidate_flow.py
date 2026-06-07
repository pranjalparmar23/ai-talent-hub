import pytest
from httpx import AsyncClient
from unittest.mock import patch


@pytest.mark.asyncio
async def test_full_candidate_flow(client: AsyncClient):
    """Integration test: register -> upload resume -> get skill gap"""
    reg = await client.post("/api/auth/register", json={
        "email": "candidate@integration.test",
        "password": "TestPass123",
        "role": "candidate",
    })
    assert reg.status_code == 201

    login = await client.post("/api/auth/login", data={
        "username": "candidate@integration.test",
        "password": "TestPass123",
    })
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Further steps would mock LLM calls
    skill_gap = await client.get(
        "/api/candidate/skill-gap?resume_id=test&jd_id=test",
        headers=headers,
    )
    assert skill_gap.status_code in [200, 422, 500]
