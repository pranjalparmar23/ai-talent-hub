import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_upload_resume_requires_auth(client: AsyncClient):
    response = await client.post("/api/candidate/resume/upload")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_upload_non_pdf_rejected(client: AsyncClient, auth_headers: dict):
    response = await client.post(
        "/api/candidate/resume/upload",
        files={"file": ("resume.txt", b"not a pdf", "text/plain")},
        headers=auth_headers,
    )
    assert response.status_code == 400
