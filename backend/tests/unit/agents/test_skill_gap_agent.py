import pytest
from unittest.mock import AsyncMock, patch
from app.agents.candidate.skill_gap_agent import SkillGapAgent


@pytest.mark.asyncio
async def test_skill_gap_identifies_missing():
    agent = SkillGapAgent()
    mock_response = '{"missing_skills": ["Kubernetes", "AWS"], "matching_skills": ["Python"], "gap_percentage": 66.7}'
    with patch.object(agent.llm, "ainvoke", return_value=AsyncMock(content=mock_response)):
        result = await agent.analyze(["Python"], ["Python", "Kubernetes", "AWS"])
    assert "Kubernetes" in result["missing_skills"]
