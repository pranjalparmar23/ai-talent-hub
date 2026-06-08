from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List, Any
from datetime import datetime
import uuid


# ── Auth ─────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: str = "candidate"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("candidate", "recruiter", "admin"):
            raise ValueError("role must be candidate, recruiter, or admin")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


# ── Resume ───────────────────────────────────────────────────

class ResumeUploadResponse(BaseModel):
    resume_id: str
    parsed_data: dict
    blob_url: str


class ATSScoreResponse(BaseModel):
    ats_score: int
    feedback: dict


class SkillGapResponse(BaseModel):
    missing_skills: List[str]
    matching_skills: List[str]
    gap_percentage: float


class LearningPlanResponse(BaseModel):
    weeks: List[dict]
    resources: List[dict]


# ── Interview ────────────────────────────────────────────────

class InterviewStartResponse(BaseModel):
    session_id: str
    question: str


class InterviewRespondResponse(BaseModel):
    question: str


class InterviewFeedbackResponse(BaseModel):
    scores: dict
    overall_score: float
    strengths: List[str]
    improvements: List[str]


# ── Recruiter ────────────────────────────────────────────────

class JDUploadResponse(BaseModel):
    jd_id: str
    parsed_data: dict


class CandidateRankingResponse(BaseModel):
    ranked_candidates: List[dict]


# ── Generic ──────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str
    data: Optional[Any] = None