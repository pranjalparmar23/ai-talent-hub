"""Pydantic request/response schemas — one source of truth for API shapes."""

from datetime import datetime
from typing import Any, List, Literal, Optional
import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator


# ═════════════════════════════════════════════════════════════════════════════
# AUTH
# ═════════════════════════════════════════════════════════════════════════════


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
    role: Optional[str] = None  # set on login so the UI can route by role


class RefreshTokenRequest(BaseModel):
    refresh_token: str


# ═════════════════════════════════════════════════════════════════════════════
# RESUME
# ═════════════════════════════════════════════════════════════════════════════


class ExperienceItem(BaseModel):
    company: Optional[str] = None
    role: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    location: Optional[str] = None
    highlights: List[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    institution: Optional[str] = None
    degree: Optional[str] = None
    field: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    gpa: Optional[str] = None


class ProjectItem(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tech_stack: List[str] = Field(default_factory=list)
    link: Optional[str] = None


class ParsedResume(BaseModel):
    """The structured JSON we extract from a resume PDF."""

    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin: Optional[str] = None
    github: Optional[str] = None
    summary: Optional[str] = None
    skills: List[str] = Field(default_factory=list)
    experience_years: float = 0
    experience: List[ExperienceItem] = Field(default_factory=list)
    education: List[EducationItem] = Field(default_factory=list)
    projects: List[ProjectItem] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)

    # Populated when LLM parsing failed
    parse_error: bool = Field(default=False, alias="_parse_error")
    error_reason: Optional[str] = Field(default=None, alias="_error_reason")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class ResumeUploadResponse(BaseModel):
    resume_id: str
    parsed_data: dict
    file_path: str
    parse_error: bool = False


class ResumeSummary(BaseModel):
    """Used in list views — trimmed to essentials."""

    resume_id: str
    name: str
    ats_score: Optional[int] = None
    created_at: str


class ResumeDetail(BaseModel):
    resume_id: str
    parsed_data: dict
    file_path: str
    ats_score: Optional[int] = None
    created_at: str


# ═════════════════════════════════════════════════════════════════════════════
# JOB DESCRIPTION
# ═════════════════════════════════════════════════════════════════════════════


class JDCreateRequest(BaseModel):
    """Body for POST /api/candidate/jd — user pastes text or provides title + text."""

    title: str = Field(..., min_length=1, max_length=200)
    raw_text: str = Field(..., min_length=20)


class ParsedJD(BaseModel):
    """Structured JSON we extract from a JD."""

    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    role_level: Optional[str] = (
        None  # "junior" | "mid" | "senior" | "lead" | "principal"
    )
    employment_type: Optional[str] = None  # "full-time" | "contract" | "internship"
    experience_years_min: Optional[int] = None
    skills_required: List[str] = Field(default_factory=list)
    skills_preferred: List[str] = Field(default_factory=list)
    responsibilities: List[str] = Field(default_factory=list)
    qualifications: List[str] = Field(default_factory=list)

    parse_error: bool = Field(default=False, alias="_parse_error")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class JDUploadResponse(BaseModel):
    jd_id: str
    parsed_data: dict
    parse_error: bool = False


class JDSummary(BaseModel):
    jd_id: str
    title: str
    created_at: str


# ═════════════════════════════════════════════════════════════════════════════
# ATS SCORE / SKILL GAP / ROADMAP
# ═════════════════════════════════════════════════════════════════════════════


class ATSScoreResponse(BaseModel):
    resume_id: str
    jd_id: str
    ats_score: int = Field(..., ge=0, le=100)
    missing_keywords: List[str] = Field(default_factory=list)
    matching_keywords: List[str] = Field(default_factory=list)
    formatting_issues: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    summary: Optional[str] = None


class SkillGapResponse(BaseModel):
    resume_id: str
    jd_id: str
    missing_skills: List[str] = Field(default_factory=list)
    matching_skills: List[str] = Field(default_factory=list)
    gap_percentage: float = Field(..., ge=0, le=100)


class RoadmapWeek(BaseModel):
    week: int
    topic: str
    tasks: List[str] = Field(default_factory=list)
    resources: List[dict] = Field(default_factory=list)  # {title, url, type}
    estimated_hours: Optional[int] = None


class LearningPlanResponse(BaseModel):
    resume_id: str
    jd_id: str
    target_role: Optional[str] = None
    weeks: List[RoadmapWeek] = Field(default_factory=list)
    summary: Optional[str] = None


# ═════════════════════════════════════════════════════════════════════════════
# INTERVIEW (Phase 4 — placeholders that let routes typecheck now)
# ═════════════════════════════════════════════════════════════════════════════


class InterviewStartRequest(BaseModel):
    company: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=100)

    @field_validator("company", "role")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class InterviewStartResponse(BaseModel):
    session_id: str
    question: str
    question_number: int = 1
    max_questions: int = 6


class InterviewRespondRequest(BaseModel):
    response: str = Field(min_length=1, max_length=4000)


class InterviewRespondResponse(BaseModel):
    question: str
    session_complete: bool = False
    question_number: int = 1
    max_questions: int = 6


class InterviewMessage(BaseModel):
    role: Literal["interviewer", "candidate"]
    content: str


class InterviewSessionResponse(BaseModel):
    """Full session state — lets the chat UI restore after a page refresh."""

    session_id: str
    company: str
    role: str
    messages: List[InterviewMessage] = Field(default_factory=list)
    question_number: int = 1
    max_questions: int = 6
    session_complete: bool = False
    feedback_ready: bool = False


class InterviewFeedbackResponse(BaseModel):
    scores: dict
    overall_score: float
    strengths: List[str] = Field(default_factory=list)
    improvements: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    parse_error: bool = False


# ═════════════════════════════════════════════════════════════════════════════
# RECRUITER
# ═════════════════════════════════════════════════════════════════════════════


class RankCandidatesRequest(BaseModel):
    """Optional body for POST /api/recruiter/jd/{jd_id}/rank-candidates."""

    top_n: Optional[int] = Field(default=None, ge=1, le=100)


class CandidateRankingItem(BaseModel):
    """One scored candidate. `rank` and the scores are None when not scorable."""

    rank: Optional[int] = None
    candidate_id: str
    resume_id: Optional[str] = None
    name: Optional[str] = None
    match_score: Optional[float] = None
    skill_match: Optional[float] = None
    experience_match: Optional[float] = None
    semantic_match: Optional[float] = None
    semantic_available: bool = False
    matching_skills: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    matching_preferred: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    interview_score: Optional[float] = None


class CandidateRankingResponse(BaseModel):
    jd_id: str
    jd_title: Optional[str] = None
    total_candidates: int = 0
    ranked_candidates: List[CandidateRankingItem] = Field(default_factory=list)


class RecruiterQuestion(BaseModel):
    question: str
    category: str
    difficulty: str
    rationale: Optional[str] = None


class RecruiterQuestionsResponse(BaseModel):
    jd_id: str
    candidate_id: str
    questions: List[RecruiterQuestion] = Field(default_factory=list)
    parse_error: bool = False


class HiringRecommendationResponse(BaseModel):
    jd_id: str
    candidate_id: str
    recommendation: Literal["HIRE", "CONSIDER", "REJECT"]
    confidence: int = Field(ge=0, le=100)
    combined_score: Optional[float] = None
    match_score: Optional[float] = None
    interview_score: Optional[float] = None
    flags: List[str] = Field(default_factory=list)
    reasoning: str = ""
    strengths: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    reasoning_source: Literal["llm", "template"] = "template"


# ═════════════════════════════════════════════════════════════════════════════
# GENERIC
# ═════════════════════════════════════════════════════════════════════════════


class MessageResponse(BaseModel):
    message: str
    data: Optional[Any] = None
