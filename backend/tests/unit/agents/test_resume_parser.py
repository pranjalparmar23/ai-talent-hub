import pytest
from unittest.mock import AsyncMock, patch
from app.agents.candidate.resume_parser import ResumeParserAgent


@pytest.mark.asyncio
async def test_parse_returns_structured_data():
    agent = ResumeParserAgent()
    mock_response = '{"name": "Jane", "skills": ["Python"], "experience": 2, "projects": [], "education": [], "certifications": []}'
    with patch.object(agent.llm, "ainvoke", return_value=AsyncMock(content=mock_response)):
        result = await agent.parse("Sample resume text")
    assert result["name"] == "Jane"
    assert "Python" in result["skills"]


@pytest.mark.asyncio
async def test_parse_handles_invalid_json():
    agent = ResumeParserAgent()
    with patch.object(agent.llm, "ainvoke", return_value=AsyncMock(content="not json")):
        result = await agent.parse("bad input")
    assert "error" in result
