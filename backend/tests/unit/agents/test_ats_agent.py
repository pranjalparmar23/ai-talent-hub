import pytest
from unittest.mock import AsyncMock, patch
from app.agents.candidate.ats_agent import ATSAgent


@pytest.mark.asyncio
async def test_ats_score_in_range():
    agent = ATSAgent()
    mock_response = '{"ats_score": 78, "missing_keywords": ["Docker"], "formatting_issues": [], "recommendations": []}'
    with patch.object(agent.llm, "ainvoke", return_value=AsyncMock(content=mock_response)):
        result = await agent.analyze({"skills": ["Python"]}, {"skills": ["Python", "Docker"]})
    assert 0 <= result["ats_score"] <= 100
    assert "missing_keywords" in result
