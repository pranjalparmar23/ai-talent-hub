from pydantic import BaseModel, EmailStr
from typing import Optional, List
import uuid


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: str = "candidate"


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class ResumeUploadResponse(BaseModel):
    resume_id: str
    parsed_data: dict
    blob_url: str


class SkillGapResponse(BaseModel):
    missing_skills: List[str]
    matching_skills: List[str]
    gap_percentage: float


class LearningPlanResponse(BaseModel):
    weeks: List[dict]
    resources: List[dict]


class JDUploadResponse(BaseModel):
    jd_id: str
    parsed_data: dict


class CandidateRankingResponse(BaseModel):
    ranked_candidates: List[dict]
