from typing import TypedDict, Optional, List


class TalentHubState(TypedDict, total=False):
    user_id: str
    resume_data: dict
    jd_data: dict
    raw_resume_text: str
    raw_jd_text: str
    ats_score: int
    ats_feedback: dict
    skill_gap: List[str]
    learning_plan: dict
    interview_history: List[dict]
    interview_feedback: dict
    jd_id: str
    jd_title: str
    top_n: Optional[int]
    candidates: List[dict]
    candidate_scores: List[dict]
    ranked_candidates: List[dict]
    interview_questions: List[dict]
    hiring_recommendation: dict
    error: Optional[str]
