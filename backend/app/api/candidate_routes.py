"""Candidate-facing API endpoints.

All routes require JWT auth via get_current_user (returns dict with id/email/role).
Route handlers stay thin — they validate input, load DB rows, delegate to services
or graphs, and return typed responses.
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.database.schemas import (
    ATSScoreResponse,
    InterviewFeedbackResponse,
    InterviewRespondRequest,
    InterviewRespondResponse,
    InterviewStartRequest,
    InterviewStartResponse,
    JDCreateRequest,
    JDSummary,
    JDUploadResponse,
    LearningPlanResponse,
    ResumeDetail,
    ResumeSummary,
    ResumeUploadResponse,
    SkillGapResponse,
)
from app.graphs.candidate_graph import CandidateGraph
from app.middleware.auth import get_current_user
from app.services.jd_service import JDService
from app.services.resume_service import ResumeService
from app.services.interview_service import InterviewService

router = APIRouter()


# ═════════════════════════════════════════════════════════════════════════════
# RESUME
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/resume/upload", response_model=ResumeUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    resume_service: ResumeService = Depends(ResumeService),
):
    """Upload a PDF resume. Extracts text, parses via LLM, persists to Postgres."""
    result = await resume_service.upload_and_parse(file, current_user["id"])
    return ResumeUploadResponse(**result)


@router.get("/resumes", response_model=list[ResumeSummary])
async def list_my_resumes(
    current_user: dict = Depends(get_current_user),
    resume_service: ResumeService = Depends(ResumeService),
):
    """List all resumes for the current user (dashboard view)."""
    return await resume_service.list_user_resumes(current_user["id"])


@router.get("/resume/{resume_id}", response_model=ResumeDetail)
async def get_my_resume(
    resume_id: str,
    current_user: dict = Depends(get_current_user),
    resume_service: ResumeService = Depends(ResumeService),
):
    """Get one resume's parsed data + metadata. Enforces owner-only access."""
    return await resume_service.get_resume(resume_id, current_user["id"])


# ═════════════════════════════════════════════════════════════════════════════
# JOB DESCRIPTION (target JDs — for candidates comparing themselves against a role)
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/jd", response_model=JDUploadResponse, status_code=status.HTTP_201_CREATED)
async def create_target_jd(
    body: JDCreateRequest,
    current_user: dict = Depends(get_current_user),
    jd_service: JDService = Depends(JDService),
):
    """Create a target JD from pasted text. Parsed via LLM and persisted."""
    return await jd_service.create_from_text(
        title=body.title,
        raw_text=body.raw_text,
        user_id=current_user["id"],
    )


@router.get("/jds", response_model=list[JDSummary])
async def list_my_jds(
    current_user: dict = Depends(get_current_user),
    jd_service: JDService = Depends(JDService),
):
    """List all JDs the current user has saved."""
    return await jd_service.list_user_jds(current_user["id"])


# ═════════════════════════════════════════════════════════════════════════════
# ATS SCORE / SKILL GAP / ROADMAP
# All three require an existing resume + JD in the DB.
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/ats-score", response_model=ATSScoreResponse)
async def get_ats_score(
    resume_id: str,
    jd_id: str,
    current_user: dict = Depends(get_current_user),
    resume_service: ResumeService = Depends(ResumeService),
    jd_service: JDService = Depends(JDService),
):
    """Compute ATS score for a specific resume vs JD pair."""
    resume = await resume_service.get_resume(resume_id, current_user["id"])
    jd = await jd_service.get_jd(jd_id, current_user["id"])

    graph = CandidateGraph()
    result = await graph.run_ats_check(
        resume_data=resume["parsed_data"] or {},
        jd_data=jd["parsed_data"] or {},
        user_id=current_user["id"],
    )
    return ATSScoreResponse(resume_id=resume_id, jd_id=jd_id, **result)


@router.get("/skill-gap", response_model=SkillGapResponse)
async def get_skill_gap(
    resume_id: str,
    jd_id: str,
    current_user: dict = Depends(get_current_user),
    resume_service: ResumeService = Depends(ResumeService),
    jd_service: JDService = Depends(JDService),
):
    """Identify missing vs matching skills between resume and JD."""
    resume = await resume_service.get_resume(resume_id, current_user["id"])
    jd = await jd_service.get_jd(jd_id, current_user["id"])

    graph = CandidateGraph()
    result = await graph.run_skill_gap(
        resume_data=resume["parsed_data"] or {},
        jd_data=jd["parsed_data"] or {},
        user_id=current_user["id"],
    )
    return SkillGapResponse(resume_id=resume_id, jd_id=jd_id, **result)


@router.get("/learning-plan", response_model=LearningPlanResponse)
async def get_learning_plan(
    resume_id: str,
    jd_id: str,
    current_user: dict = Depends(get_current_user),
    resume_service: ResumeService = Depends(ResumeService),
    jd_service: JDService = Depends(JDService),
):
    """Generate a personalized learning roadmap based on skill gaps."""
    resume = await resume_service.get_resume(resume_id, current_user["id"])
    jd = await jd_service.get_jd(jd_id, current_user["id"])

    graph = CandidateGraph()
    result = await graph.run_learning_plan(
        resume_data=resume["parsed_data"] or {},
        jd_data=jd["parsed_data"] or {},
        user_id=current_user["id"],
    )
    return LearningPlanResponse(resume_id=resume_id, jd_id=jd_id, **result)


# ═════════════════════════════════════════════════════════════════════════════
# INTERVIEW (Phase 4 — routes stay unchanged, agent wiring comes later)
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/interview/start", response_model=InterviewStartResponse)
async def start_interview(
    body: InterviewStartRequest,
    current_user: dict = Depends(get_current_user),
    interview_service: InterviewService = Depends(InterviewService),
):
    return await interview_service.start_session(body.company, body.role, current_user["id"])


@router.post("/interview/{session_id}/respond", response_model=InterviewRespondResponse)
async def respond_to_interview(
    session_id: str,
    body: InterviewRespondRequest,
    current_user: dict = Depends(get_current_user),
    interview_service: InterviewService = Depends(InterviewService),
):
    return await interview_service.process_response(session_id, body.response)


@router.get("/interview/{session_id}/feedback", response_model=InterviewFeedbackResponse)
async def get_interview_feedback(
    session_id: str,
    current_user: dict = Depends(get_current_user),
    interview_service: InterviewService = Depends(InterviewService),
):
    return await interview_service.generate_feedback(session_id)