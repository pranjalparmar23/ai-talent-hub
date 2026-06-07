import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_new_user(client: AsyncClient):
    response = await client.post("/api/auth/register", json={
        "email": "test@example.com",
        "password": "SecurePass123",
        "role": "candidate",
    })
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_login_valid_credentials(client: AsyncClient):
    await client.post("/api/auth/register", json={
        "email": "login@test.com",
        "password": "SecurePass123",
    })
    response = await client.post("/api/auth/login", data={
        "username": "login@test.com",
        "password": "SecurePass123",
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data


@pytest.mark.asyncio
async def test_login_invalid_credentials(client: AsyncClient):
    response = await client.post("/api/auth/login", data={
        "username": "wrong@test.com",
        "password": "WrongPassword",
    })
    assert response.status_code in [401, 422]
