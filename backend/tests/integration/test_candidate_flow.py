"""Integration tests: the candidate pipeline against real Groq, ChromaDB and embeddings.

Unlike the unit tests, these hit the real world:
  - Real Groq API calls (GROQ_API_KEY from the environment)
  - Real ChromaDB HTTP client (`interview_knowledge` and `learning_knowledge`)
  - Real embedding model (sentence-transformers)

Purpose: catch what unit tests cannot: prompt regressions, API signature changes,
schema drift between agent output and downstream consumers, and agents that cite
pages that are not in the knowledge base.

The grounding tests read the real collections and never write to them. They skip
when a collection is empty (run `python -m app.ingest.run` first). The model's wording
varies, so they check invariants (every link and source is a stored page, sources and
notice agree) and not exact text.

Run from backend/, with ChromaDB up and the key exported:

    pytest tests/integration/test_candidate_flow.py -m integration --no-cov

Skipped by default (`pytest` alone does not run them) and skipped when GROQ_API_KEY
is not set. The knowledge-layer tests that need no key are in test_knowledge_layer.py.
"""

import os
import pytest

from app.agents.candidate.interview_agent import InterviewAgent
from app.agents.candidate.resume_parser import ResumeParserAgent
from app.agents.candidate.roadmap_agent import RoadmapAgent
from app.agents.recruiter.jd_analyzer import JDAnalyzerAgent
from app.graphs.candidate_graph import CandidateGraph
from tests.integration._helpers import grounding_problems, plan_links


# ── Skip guards ───────────────────────────────────────────────────────

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("GROQ_API_KEY"),
        reason="GROQ_API_KEY not set — integration tests need real Groq access",
    ),
]


# ── Test data ────────────────────────────────────────────────────────

SAMPLE_RESUME = """
AARAV MEHTA
aarav.mehta@example.com | +91-9876543210 | linkedin.com/in/aarav-mehta

SUMMARY
Backend engineer with 3 years of experience building Python APIs at a fintech.
Shipped services handling 2k RPS in production.

EXPERIENCE
Backend Engineer — PayCircle (2023-04 to Present)
- Designed FastAPI reconciliation service handling 2k RPS with p99 < 120ms
- Migrated sync workers to async Python (asyncio + asyncpg), cut compute by 40%
- Built PostgreSQL-backed ETL pipelines with Redis dedup for idempotency

Junior Backend Engineer — DataForge (2022-07 to 2023-03)
- Built REST endpoints in Flask for a multi-tenant analytics dashboard
- Containerized services with Docker and docker-compose

SKILLS
Python, FastAPI, Flask, SQLAlchemy, PostgreSQL, Redis, Docker, Git, pytest

EDUCATION
B.Tech Information Technology, NIT Warangal (2018-2022), CGPA 8.4
"""


SAMPLE_JD = """
Senior Backend Engineer — Lumen Cloud (Observability)
Remote India · Full-time · 5+ years experience required

The role:
We're hiring a Senior Backend Engineer to own our real-time ingestion pipeline
processing 8B+ events per day across three regions.

Requirements:
- 5+ years of backend engineering experience
- Strong Python proficiency, async patterns (FastAPI or similar)
- Deep production experience with Kubernetes on AWS EKS
- Hands-on Kafka experience (producers, consumers, partitioning)
- Strong PostgreSQL skills including query optimization
- Redis in production (caching, pub/sub)
- Terraform for infrastructure as code
- BS in Computer Science or equivalent

Nice to have:
- gRPC or Protobuf experience
- Rust in performance-critical paths
- Contributions to open-source infrastructure projects

Responsibilities:
- Design and build ingestion services at scale
- Mentor mid-level engineers
- Own Kubernetes deployment model across regions
- Contribute to on-call rotation
"""


# ── Individual agent smoke tests ─────────────────────────────────────


class TestResumeParser:
    """Verifies ResumeParserAgent against real Groq."""

    @pytest.mark.asyncio
    async def test_parses_realistic_resume(self):
        agent = ResumeParserAgent()
        parsed = await agent.parse(SAMPLE_RESUME)

        # Core fields present
        assert parsed.get("name") and "Aarav" in parsed["name"]
        assert parsed.get("email") == "aarav.mehta@example.com"

        # Skills extracted as individual items (not sentences)
        skills = parsed.get("skills", [])
        assert len(skills) >= 5
        # Each skill should be a short string, not a full sentence
        assert all(
            len(s.split()) <= 4 for s in skills
        ), f"Some skills look like sentences: {skills}"

        # Recognizes Python as a listed skill (case-insensitive)
        assert any("python" in s.lower() for s in skills)

        # Experience years is a reasonable integer
        years = parsed.get("experience_years")
        assert isinstance(years, (int, float))
        assert 2 <= years <= 5  # candidate says "3 years"

        # No parse error on a clean resume
        assert not parsed.get(
            "_parse_error"
        ), f"Parse error occurred: {parsed.get('_raw_output', '')}"


class TestJDAnalyzer:
    """Verifies JDAnalyzerAgent against real Groq."""

    @pytest.mark.asyncio
    async def test_parses_realistic_jd(self):
        agent = JDAnalyzerAgent()
        parsed = await agent.analyze(SAMPLE_JD)

        # Basic fields
        assert parsed.get("title"), "title should be extracted"

        # Skills split into required vs preferred
        required = parsed.get("skills_required", [])
        preferred = parsed.get("skills_preferred", [])
        assert len(required) >= 5, f"Expected 5+ required skills, got {required}"

        # Python, Kubernetes, PostgreSQL are all required
        required_lower = [s.lower() for s in required]
        assert any("python" in s for s in required_lower)
        assert any("kubernetes" in s for s in required_lower)

        # Nice-to-haves went to preferred
        preferred_lower = [s.lower() for s in preferred]
        # Rust or gRPC should be in preferred (both are in "Nice to have")
        assert any(
            "rust" in s or "grpc" in s or "protobuf" in s for s in preferred_lower
        ), f"Expected nice-to-haves in preferred, got {preferred}"

        # Experience years extracted
        assert parsed.get("experience_years_min") == 5

        # No parse error
        assert not parsed.get("_parse_error")


# ── Full pipeline through the graph ──────────────────────────────────


class TestCandidateGraph:
    """End-to-end: parse resume + JD, then run analysis pipeline."""

    @pytest.mark.asyncio
    async def test_ats_score_produces_valid_result(self):
        # Prep: parse resume and JD via real Groq
        resume = await ResumeParserAgent().parse(SAMPLE_RESUME)
        jd = await JDAnalyzerAgent().analyze(SAMPLE_JD)

        assert not resume.get("_parse_error")
        assert not jd.get("_parse_error")

        # Run ATS through the graph (as candidate_routes does)
        graph = CandidateGraph()
        result = await graph.run_ats_check(
            resume_data=resume,
            jd_data=jd,
            user_id="integration-test-user",
        )

        # Score in valid range
        assert isinstance(result["ats_score"], int)
        assert 0 <= result["ats_score"] <= 100

        # Aarav is a 3-year candidate applying for 5+ senior role with cloud gaps —
        # score should reflect partial match (roughly 30-70)
        assert (
            25 <= result["ats_score"] <= 75
        ), f"Unexpected score for partial-match scenario: {result['ats_score']}"

        # Response shape complete
        assert isinstance(result["matching_keywords"], list)
        assert isinstance(result["missing_keywords"], list)
        assert isinstance(result["recommendations"], list)
        assert result.get("summary")

        # Should identify Python as matching (both sides have it)
        matching_lower = [s.lower() for s in result["matching_keywords"]]
        assert any("python" in s for s in matching_lower)

        # Should identify Kubernetes as missing (JD requires, resume lacks)
        missing_lower = [s.lower() for s in result["missing_keywords"]]
        assert any("kubernetes" in s for s in missing_lower)

    @pytest.mark.asyncio
    async def test_skill_gap_produces_valid_result(self):
        resume = await ResumeParserAgent().parse(SAMPLE_RESUME)
        jd = await JDAnalyzerAgent().analyze(SAMPLE_JD)

        graph = CandidateGraph()
        result = await graph.run_skill_gap(
            resume_data=resume,
            jd_data=jd,
            user_id="integration-test-user",
        )

        # Shape guarantees
        assert isinstance(result["matching_skills"], list)
        assert isinstance(result["missing_skills"], list)
        assert isinstance(result["gap_percentage"], float)
        assert 0.0 <= result["gap_percentage"] <= 100.0

        # There ARE gaps — candidate is missing K8s, AWS, Kafka, Terraform
        assert (
            result["gap_percentage"] > 20.0
        ), "Expected meaningful gap for this partial-match scenario"

        # Fuzzy matching works — candidate has "PostgreSQL", JD asks "PostgreSQL"
        matching_lower = [s.lower() for s in result["matching_skills"]]
        assert any(
            "postgres" in s for s in matching_lower
        ), f"Expected PostgreSQL to match, matching_skills={result['matching_skills']}"

    @pytest.mark.asyncio
    async def test_learning_plan_produces_valid_result(self, real_learning_urls):
        """Full pipeline including RAG retrieval from ChromaDB."""
        resume = await ResumeParserAgent().parse(SAMPLE_RESUME)
        jd = await JDAnalyzerAgent().analyze(SAMPLE_JD)

        graph = CandidateGraph()
        result = await graph.run_learning_plan(
            resume_data=resume,
            jd_data=jd,
            user_id="integration-test-user",
        )

        # Shape complete
        assert result["target_role"]
        weeks = result.get("weeks", [])
        assert isinstance(weeks, list)
        assert (
            len(weeks) >= 2
        ), "Should produce at least a 2-week plan for multiple gaps"

        # Each week has required fields
        for week in weeks:
            assert isinstance(week.get("week"), int)
            assert week.get("topic")
            assert isinstance(week.get("tasks"), list)
            assert isinstance(week.get("resources"), list)

        # Grounding: every link is a stored page, every source is used by the plan,
        # and a plan with no sources says so in its notice.
        problems = grounding_problems(
            result, real_learning_urls, links=plan_links(result)
        )
        assert not problems, problems

        # No parse error on the plan itself
        assert not result.get("_parse_error")


# ── Grounding against the real knowledge base ────────────────────────


class TestRoadmapAgentGrounding:
    """RoadmapAgent with real retrieval and real Groq."""

    @pytest.mark.asyncio
    async def test_known_skills_give_links_that_are_stored_pages(
        self, real_learning_urls
    ):
        plan = await RoadmapAgent().generate(
            ["Docker", "Kubernetes"], "DevOps Engineer"
        )

        assert not plan.get("_parse_error"), plan.get("_raw_output", "")
        assert plan["weeks"], "a plan for two real skills should have weeks"
        problems = grounding_problems(plan, real_learning_urls, links=plan_links(plan))
        assert not problems, problems

    @pytest.mark.asyncio
    async def test_a_skill_the_knowledge_base_lacks_still_gives_an_honest_plan(
        self, real_learning_urls
    ):
        """Whatever the retrieval finds, the plan must not invent a link.

        Whether the nonsense skill matches anything depends on the model's scores, so
        this checks the rules (no invented link, notice when there are no sources)
        and not which of the two cases happened.
        """
        plan = await RoadmapAgent().generate(
            ["Underwater basket weaving", "Medieval falconry"], "Basket Engineer"
        )

        problems = grounding_problems(
            plan,
            real_learning_urls,
            links=plan_links(plan),
            has_content=bool(plan.get("weeks")),
        )
        assert not problems, problems

    @pytest.mark.asyncio
    async def test_no_gaps_means_no_plan_and_no_retrieval_claims(self):
        plan = await RoadmapAgent().generate([], "Backend Engineer")

        assert plan["weeks"] == [] and plan["total_weeks"] == 0
        assert not plan.get("sources")
        assert not plan.get("notice")


class TestInterviewAgentGrounding:
    """InterviewAgent with real retrieval and real Groq."""

    @pytest.mark.asyncio
    async def test_the_opening_question_cites_only_stored_pages(
        self, real_interview_urls
    ):
        result = await InterviewAgent().start_session(
            company="Google", role="Software Engineer"
        )

        assert result["session_started"] is True
        assert len(result["question"].strip()) > 20
        problems = grounding_problems(
            result, real_interview_urls, grounded=result["grounded"]
        )
        assert not problems, problems
        for source in result["sources"]:
            assert source["title"] and source["licence"] and source["attribution"]

    @pytest.mark.asyncio
    async def test_an_unknown_company_and_role_still_gives_a_consistent_answer(
        self, real_interview_urls
    ):
        result = await InterviewAgent().start_session(
            company="Zzyzx Quantum Llama Works", role="Chief Llama Officer"
        )

        assert result["question"].strip()
        problems = grounding_problems(
            result, real_interview_urls, grounded=result["grounded"]
        )
        assert not problems, problems
