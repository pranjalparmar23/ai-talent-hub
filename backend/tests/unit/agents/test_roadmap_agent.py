"""Unit tests for RoadmapAgent.

Roadmap has the most complex output (nested weeks + resources). Tests focus on:
  - Correct parsing of nested schema
  - RAG retriever is mocked (no ChromaDB dependency)
  - Malformed weeks are cleaned up by _normalize
  - Edge case: no skill gaps = no LLM call, empty plan
"""
from unittest.mock import patch

import pytest

from app.agents.candidate.roadmap_agent import RoadmapAgent
from app.rag.retriever import RetrievedDocument
from tests.unit.agents._fakes import FakeChain, FakeMsg


@pytest.fixture
def agent():
    return RoadmapAgent()


@pytest.fixture
def sample_docs():
    """Fake RAG output — three retrieved chunks."""
    return [
        RetrievedDocument(
            id="doc1",
            document="Kubernetes is a container orchestration platform...",
            metadata={"topic": "kubernetes", "source_file": "kubernetes.json"},
            similarity=0.82,
        ),
        RetrievedDocument(
            id="doc2",
            document="AWS EC2 provides compute capacity in the cloud...",
            metadata={"topic": "aws", "source_file": "aws_cloud.json"},
            similarity=0.71,
        ),
        RetrievedDocument(
            id="doc3",
            document="Terraform is an IaC tool for provisioning infrastructure...",
            metadata={"topic": "terraform", "source_file": "hands-on tutorial"},
            similarity=0.45,
        ),
    ]


@pytest.fixture
def full_llm_response():
    return '''
    {
        "target_role": "Senior Backend Engineer",
        "total_weeks": 4,
        "weeks": [
            {
                "week": 1,
                "topic": "Kubernetes Basics",
                "goal": "Deploy first pod",
                "tasks": ["Complete official tutorial", "Deploy nginx to Minikube"],
                "resources": [{"title": "K8s tutorial", "source": "kubernetes.json", "type": "documentation"}],
                "estimated_hours": 12
            },
            {
                "week": 2,
                "topic": "AWS Basics",
                "goal": "Deploy an EC2 instance",
                "tasks": ["Create account", "Launch EC2"],
                "resources": [{"title": "AWS overview", "source": "aws_cloud.json", "type": "course"}],
                "estimated_hours": 10
            }
        ],
        "summary": "4-week plan covering K8s and AWS fundamentals."
    }
    '''


# ── Happy path ────────────────────────────────────────────────────────

class TestHappyPath:
    @pytest.mark.asyncio
    async def test_generates_full_plan(self, agent, sample_docs, full_llm_response):
        with patch.object(agent.retriever, "retrieve", return_value=sample_docs):
            agent.chain = FakeChain(response=FakeMsg(full_llm_response))

            result = await agent.generate(
                skill_gaps=["Kubernetes", "AWS"],
                target_role="Senior Backend Engineer",
            )

        assert result["target_role"] == "Senior Backend Engineer"
        assert result["total_weeks"] == 2  # normalized to actual count
        assert len(result["weeks"]) == 2
        assert result["weeks"][0]["topic"] == "Kubernetes Basics"
        assert result["weeks"][0]["estimated_hours"] == 12
        assert not result.get("_parse_error")


# ── RAG integration ──────────────────────────────────────────────────

class TestRAGIntegration:
    @pytest.mark.asyncio
    async def test_retrieval_called_once_per_skill(self, agent, sample_docs, full_llm_response):
        with patch.object(agent.retriever, "retrieve", return_value=sample_docs) as mock_retrieve:
            agent.chain = FakeChain(response=FakeMsg(full_llm_response))
            await agent.generate(
                skill_gaps=["Kubernetes", "AWS", "Terraform"],
                target_role="Backend Engineer",
            )

        # Three skills → three retrieval calls
        assert mock_retrieve.call_count == 3

    @pytest.mark.asyncio
    async def test_low_similarity_docs_filtered(self, agent, full_llm_response):
        """Docs below similarity threshold get dropped as noise."""
        low_sim_docs = [
            RetrievedDocument(
                id="noise1",
                document="Something barely related",
                metadata={},
                similarity=0.15,  # below 0.30 threshold
            ),
        ]

        with patch.object(agent.retriever, "retrieve", return_value=low_sim_docs):
            agent.chain = FakeChain(response=FakeMsg(full_llm_response))
            fake = agent.chain
            await agent.generate(skill_gaps=["Kubernetes"], target_role="Backend")

        # LLM should have been called with an empty resources list
        import json
        called_resources = json.loads(fake.calls[0]["resources"])
        assert called_resources == []

    @pytest.mark.asyncio
    async def test_retrieval_failure_degrades_gracefully(self, agent, full_llm_response):
        """ChromaDB down should not crash the roadmap generation."""
        with patch.object(agent.retriever, "retrieve", side_effect=Exception("ChromaDB down")):
            agent.chain = FakeChain(response=FakeMsg(full_llm_response))

            result = await agent.generate(
                skill_gaps=["Kubernetes"],
                target_role="Backend Engineer",
            )

        # Should still get a plan back — just without grounded resources
        assert result["total_weeks"] == 2
        assert not result.get("_parse_error")


# ── Edge cases ───────────────────────────────────────────────────────

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_no_skill_gaps_returns_empty_plan_no_llm(self, agent):
        """No gaps = no plan needed. Should short-circuit before LLM call."""
        result = await agent.generate(skill_gaps=[], target_role="Backend Engineer")

        assert result["total_weeks"] == 0
        assert result["weeks"] == []
        assert "No skill gaps" in result["summary"]

    @pytest.mark.asyncio
    async def test_empty_target_role_gets_default(self, agent, sample_docs, full_llm_response):
        with patch.object(agent.retriever, "retrieve", return_value=sample_docs):
            agent.chain = FakeChain(response=FakeMsg(full_llm_response))
            result = await agent.generate(skill_gaps=["Python"], target_role="")

        # Should not crash — default fills in
        assert result.get("target_role") == "target role"  # fallback when input is empty


# ── Normalization ────────────────────────────────────────────────────

class TestNormalization:
    @pytest.mark.asyncio
    async def test_malformed_weeks_cleaned(self, agent, sample_docs):
        """LLM sometimes returns weeks with missing fields."""
        messy = '''
        {
            "target_role": "Backend",
            "total_weeks": 2,
            "weeks": [
                {"week": 1, "topic": "K8s", "tasks": null, "resources": "not a list", "estimated_hours": "12"},
                {"topic": "", "tasks": ["Learn X"], "estimated_hours": 999}
            ],
            "summary": "Plan"
        }
        '''
        with patch.object(agent.retriever, "retrieve", return_value=sample_docs):
            agent.chain = FakeChain(response=FakeMsg(messy))
            result = await agent.generate(skill_gaps=["K8s"], target_role="Backend")

        # Both weeks kept but cleaned
        assert len(result["weeks"]) == 2
        # Null tasks became empty list
        assert result["weeks"][0]["tasks"] == []
        # Non-list resources became empty
        assert result["weeks"][0]["resources"] == []
        # String hours coerced
        assert result["weeks"][0]["estimated_hours"] == 12
        # Empty topic filled in
        assert result["weeks"][1]["topic"] == "Week 2"
        # Absurd hours capped at 40
        assert result["weeks"][1]["estimated_hours"] == 40


# ── Fallback ─────────────────────────────────────────────────────────

class TestFallback:
    @pytest.mark.asyncio
    async def test_malformed_llm_response_returns_fallback(self, agent, sample_docs):
        with patch.object(agent.retriever, "retrieve", return_value=sample_docs):
            agent.chain = FakeChain(response=FakeMsg("cannot plan"))

            result = await agent.generate(
                skill_gaps=["Kubernetes"],
                target_role="Backend Engineer",
            )

        assert result.get("_parse_error") is True
        assert result["target_role"] == "Backend Engineer"
        assert isinstance(result["weeks"], list)

    @pytest.mark.asyncio
    async def test_llm_api_error_returns_fallback(self, agent, sample_docs):
        with patch.object(agent.retriever, "retrieve", return_value=sample_docs):
            agent.chain = FakeChain(exc=Exception("Rate limit"))

            result = await agent.generate(
                skill_gaps=["Kubernetes"],
                target_role="Backend Engineer",
            )

        assert result.get("_parse_error") is True
        assert "Rate limit" in result.get("_error_reason", "")