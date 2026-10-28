"""Recruiter-facing API endpoints.

Every route requires a logged-in user with the `recruiter` role, and every JD
is ownership-checked in the service layer (someone else's JD is a 404).
Handlers stay thin: validate input, delegate to the service or the graph,
return a typed response.
"""

from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile, status

from app.database.schemas import (
    CandidateRankingResponse,
    HiringRecommendationResponse,
    JDSummary,
    JDUploadResponse,
    RankCandidatesRequest,
    RecruiterQuestionsResponse,
)
from app.graphs.recruiter_graph import RecruiterGraph
from app.middleware.auth import require_role
from app.services.recruiter_service import RecruiterService

router = APIRouter()


@lru_cache(maxsize=1)
def get_recruiter_graph() -> RecruiterGraph:
    """One shared graph (agents + LLM clients are stateless), built on first use."""
    return RecruiterGraph()


# ═════════════════════════════════════════════════════════════════════════════
# JOB DESCRIPTIONS
# ═════════════════════════════════════════════════════════════════════════════


@router.post(
    "/jd/upload",
    response_model=JDUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_jd(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role("recruiter")),
    recruiter_service: RecruiterService = Depends(RecruiterService),
):
    """Upload a JD (PDF or .txt). Extracts text, parses via LLM, persists."""
    result = await recruiter_service.upload_jd(file, current_user["id"])
    return JDUploadResponse(**result)


@router.get("/jds", response_model=list[JDSummary])
async def list_my_jds(
    current_user: dict = Depends(require_role("recruiter")),
    recruiter_service: RecruiterService = Depends(RecruiterService),
):
    """The recruiter's JDs, newest first (dashboard list)."""
    return await recruiter_service.jd_service.list_user_jds(current_user["id"])


# ═════════════════════════════════════════════════════════════════════════════
# RANKING / QUESTIONS / RECOMMENDATION
# ═════════════════════════════════════════════════════════════════════════════


@router.post("/jd/{jd_id}/rank-candidates", response_model=CandidateRankingResponse)
async def rank_candidates(
    jd_id: str,
    body: Optional[RankCandidatesRequest] = None,
    current_user: dict = Depends(require_role("recruiter")),
    graph: RecruiterGraph = Depends(get_recruiter_graph),
):
    """Score and rank every candidate against this JD, best first."""
    top_n = body.top_n if body else None
    result = await graph.run_full_pipeline(jd_id, current_user["id"], top_n)
    return CandidateRankingResponse(**result)


@router.get(
    "/jd/{jd_id}/questions/{candidate_id}",
    response_model=RecruiterQuestionsResponse,
)
async def get_interview_questions(
    jd_id: str,
    candidate_id: str,
    current_user: dict = Depends(require_role("recruiter")),
    graph: RecruiterGraph = Depends(get_recruiter_graph),
):
    """Interview questions tailored to this JD and this candidate's skill gaps."""
    result = await graph.generate_questions(jd_id, candidate_id, current_user["id"])
    return RecruiterQuestionsResponse(**result)


@router.get(
    "/jd/{jd_id}/recommendation/{candidate_id}",
    response_model=HiringRecommendationResponse,
)
async def get_hiring_recommendation(
    jd_id: str,
    candidate_id: str,
    current_user: dict = Depends(require_role("recruiter")),
    graph: RecruiterGraph = Depends(get_recruiter_graph),
):
    """HIRE / CONSIDER / REJECT with confidence and reasoning."""
    result = await graph.get_recommendation(jd_id, candidate_id, current_user["id"])
    return HiringRecommendationResponse(**result)
