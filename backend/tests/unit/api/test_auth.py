import uuid
import pytest
from httpx import AsyncClient


def _unique_email(prefix: str = "user") -> str:
    """Generate a unique email per test so DB collisions can't happen."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


@pytest.mark.asyncio
async def test_register_new_user(client: AsyncClient):
    response = await client.post(
        "/api/auth/register",
        json={"email": _unique_email("new"), "password": "SecurePass123", "role": "candidate"},
    )
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["message"] == "User created"


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    email = _unique_email("dup")
    payload = {"email": email, "password": "SecurePass123", "role": "candidate"}

    first = await client.post("/api/auth/register", json=payload)
    assert first.status_code == 201

    second = await client.post("/api/auth/register", json=payload)
    assert second.status_code == 409
    assert "already registered" in second.json()["detail"].lower()


@pytest.mark.asyncio
async def test_register_invalid_role(client: AsyncClient):
    response = await client.post(
        "/api/auth/register",
        json={"email": _unique_email("role"), "password": "SecurePass123", "role": "superadmin"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_weak_password(client: AsyncClient):
    response = await client.post(
        "/api/auth/register",
        json={"email": _unique_email("weak"), "password": "short", "role": "candidate"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_valid_credentials(client: AsyncClient):
    email = _unique_email("login")
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "SecurePass123", "role": "candidate"},
    )
    response = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "SecurePass123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    email = _unique_email("wrongpw")
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "SecurePass123", "role": "candidate"},
    )
    response = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "TotallyWrongPassword"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient):
    response = await client.post(
        "/api/auth/login",
        data={"username": _unique_email("ghost"), "password": "AnyPassword123"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_flow(client: AsyncClient):
    email = _unique_email("refresh")
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "SecurePass123", "role": "candidate"},
    )
    login_resp = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "SecurePass123"},
    )
    refresh_token = login_resp.json()["refresh_token"]

    response = await client.post(
        "/api/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


@pytest.mark.asyncio
async def test_refresh_invalid_token(client: AsyncClient):
    response = await client.post(
        "/api/auth/refresh", json={"refresh_token": "not-a-real-token"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_requires_auth(client: AsyncClient):
    response = await client.get("/api/candidate/skill-gap?resume_id=x&jd_id=y")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_with_valid_token(client: AsyncClient):
    """With a valid token, auth middleware passes the request through (no 401)."""
    email = _unique_email("protected")
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "SecurePass123", "role": "candidate"},
    )
    login_resp = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "SecurePass123"},
    )
    token = login_resp.json()["access_token"]

    response = await client.post(
        "/api/auth/logout",
        json={"token": "anything"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code != 401