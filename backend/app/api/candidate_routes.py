from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from app.services.resume_service import ResumeService
from app.services.interview_service import InterviewService
from app.graphs.candidate_graph import CandidateGraph
from app.middleware.auth import get_current_user

router = APIRouter()


@router.post("/resume/upload")
async def upload_resume(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    resume_service: ResumeService = Depends(),
):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files accepted")
    return await resume_service.upload_and_parse(file, current_user["id"])


@router.get("/ats-score/{resume_id}")
async def get_ats_score(resume_id: str, jd_id: str, current_user=Depends(get_current_user)):
    graph = CandidateGraph()
    return await graph.run_ats_check(resume_id, jd_id, current_user["id"])


@router.get("/skill-gap")
async def get_skill_gap(resume_id: str, jd_id: str, current_user=Depends(get_current_user)):
    graph = CandidateGraph()
    return await graph.run_skill_gap(resume_id, jd_id, current_user["id"])


@router.get("/learning-plan")
async def get_learning_plan(resume_id: str, jd_id: str, current_user=Depends(get_current_user)):
    graph = CandidateGraph()
    return await graph.run_learning_plan(resume_id, jd_id, current_user["id"])


@router.post("/interview/start")
async def start_interview(
    company: str,
    role: str,
    current_user=Depends(get_current_user),
    interview_service: InterviewService = Depends(),
):
    return await interview_service.start_session(company, role, current_user["id"])


@router.post("/interview/{session_id}/respond")
async def respond_to_interview(
    session_id: str,
    response: str,
    current_user=Depends(get_current_user),
    interview_service: InterviewService = Depends(),
):
    return await interview_service.process_response(session_id, response)


@router.get("/interview/{session_id}/feedback")
async def get_interview_feedback(session_id: str, current_user=Depends(get_current_user)):
    interview_service = InterviewService()
    return await interview_service.generate_feedback(session_id)
