from fastapi import APIRouter, Depends, UploadFile, File
from app.services.recruiter_service import RecruiterService
from app.graphs.recruiter_graph import RecruiterGraph
from app.middleware.auth import get_current_user

router = APIRouter()


@router.post("/jd/upload")
async def upload_jd(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    recruiter_service: RecruiterService = Depends(),
):
    return await recruiter_service.upload_jd(file, current_user["id"])


@router.post("/jd/{jd_id}/rank-candidates")
async def rank_candidates(jd_id: str, current_user=Depends(get_current_user)):
    graph = RecruiterGraph()
    return await graph.run_full_pipeline(jd_id, current_user["id"])


@router.get("/jd/{jd_id}/questions/{candidate_id}")
async def get_interview_questions(jd_id: str, candidate_id: str, current_user=Depends(get_current_user)):
    graph = RecruiterGraph()
    return await graph.generate_questions(jd_id, candidate_id)


@router.get("/jd/{jd_id}/recommendation/{candidate_id}")
async def get_hiring_recommendation(jd_id: str, candidate_id: str, current_user=Depends(get_current_user)):
    graph = RecruiterGraph()
    return await graph.get_recommendation(jd_id, candidate_id)
