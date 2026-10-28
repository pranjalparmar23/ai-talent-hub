import axios from "axios";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000/api";

export const api = axios.create({ baseURL: API_BASE });

// ── Interceptors ────────────────────────────────────────────

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (res) => res,
  async (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

// ═════════════════════════════════════════════════════════════
// Types — mirror backend Pydantic schemas
// ═════════════════════════════════════════════════════════════

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  /** "candidate" | "recruiter" — returned by login so the UI can route by role. */
  role?: string | null;
}

export interface ResumeUploadResponse {
  resume_id: string;
  parsed_data: Record<string, unknown>;
  file_path: string;
  parse_error: boolean;
}

export interface ResumeSummary {
  resume_id: string;
  name: string;
  ats_score: number | null;
  created_at: string;
}

export interface ResumeDetail {
  resume_id: string;
  parsed_data: Record<string, unknown>;
  file_path: string;
  ats_score: number | null;
  created_at: string;
}

export interface JDCreateRequest {
  title: string;
  raw_text: string;
}

export interface JDUploadResponse {
  jd_id: string;
  parsed_data: Record<string, unknown>;
  parse_error: boolean;
}

export interface JDSummary {
  jd_id: string;
  title: string;
  created_at: string;
}

export interface ATSScoreResponse {
  resume_id: string;
  jd_id: string;
  ats_score: number;
  matching_keywords: string[];
  missing_keywords: string[];
  formatting_issues: string[];
  recommendations: string[];
  summary: string | null;
}

export interface SkillGapResponse {
  resume_id: string;
  jd_id: string;
  missing_skills: string[];
  matching_skills: string[];
  gap_percentage: number;
}

export interface RoadmapWeek {
  week: number;
  topic: string;
  goal?: string | null;
  tasks: string[];
  resources: { title: string; source?: string; type?: string }[];
  estimated_hours: number | null;
}

export interface LearningPlanResponse {
  resume_id: string;
  jd_id: string;
  target_role: string | null;
  weeks: RoadmapWeek[];
  summary: string | null;
}

// ── Interview (Phase 4) ─────────────────────────────────────

export interface InterviewMessage {
  role: "interviewer" | "candidate";
  content: string;
}

export interface InterviewStartResponse {
  session_id: string;
  question: string;
  question_number: number;
  max_questions: number;
}

export interface InterviewRespondResponse {
  question: string;
  session_complete: boolean;
  question_number: number;
  max_questions: number;
}

export interface InterviewSession {
  session_id: string;
  company: string;
  role: string;
  messages: InterviewMessage[];
  question_number: number;
  max_questions: number;
  session_complete: boolean;
  feedback_ready: boolean;
}

export interface InterviewFeedback {
  scores: Record<string, number>;
  overall_score: number;
  strengths: string[];
  improvements: string[];
  summary: string | null;
  parse_error: boolean;
}

// ── Recruiter (Phase 5) ─────────────────────────────────────

export interface RankCandidatesRequest {
  /** Return only the best N candidates (1-100). Omit for everyone. */
  top_n?: number;
}

/** rank and the scores are null when a candidate could not be scored. */
export interface CandidateRankingItem {
  rank: number | null;
  candidate_id: string;
  resume_id: string | null;
  name: string | null;
  match_score: number | null;
  skill_match: number | null;
  experience_match: number | null;
  semantic_match: number | null;
  semantic_available: boolean;
  matching_skills: string[];
  missing_skills: string[];
  matching_preferred: string[];
  summary: string | null;
  interview_score: number | null;
}

export interface CandidateRankingResponse {
  jd_id: string;
  jd_title: string | null;
  total_candidates: number;
  ranked_candidates: CandidateRankingItem[];
}

export interface RecruiterQuestion {
  question: string;
  category: string;
  difficulty: string;
  rationale: string | null;
}

export interface RecruiterQuestionsResponse {
  jd_id: string;
  candidate_id: string;
  questions: RecruiterQuestion[];
  parse_error: boolean;
}

export type HiringVerdict = "HIRE" | "CONSIDER" | "REJECT";

export interface HiringRecommendationResponse {
  jd_id: string;
  candidate_id: string;
  recommendation: HiringVerdict;
  confidence: number;
  combined_score: number | null;
  match_score: number | null;
  interview_score: number | null;
  flags: string[];
  reasoning: string;
  strengths: string[];
  risks: string[];
  reasoning_source: "llm" | "template";
}

// ═════════════════════════════════════════════════════════════
// Auth
// ═════════════════════════════════════════════════════════════

export const authAPI = {
  register: (data: { email: string; password: string; role: string }) =>
    api.post("/auth/register", data),

  login: (email: string, password: string) =>
    api.post<LoginResponse>(
      "/auth/login",
      new URLSearchParams({ username: email, password }),
      { headers: { "Content-Type": "application/x-www-form-urlencoded" } }
    ),

  logout: (token: string) => api.post("/auth/logout", { token }),

  refresh: (refreshToken: string) =>
    api.post("/auth/refresh", { refresh_token: refreshToken }),
};

// ═════════════════════════════════════════════════════════════
// Candidate
// ═════════════════════════════════════════════════════════════

export const candidateAPI = {
  // Resumes
  uploadResume: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.post<ResumeUploadResponse>("/candidate/resume/upload", form);
  },

  listResumes: () => api.get<ResumeSummary[]>("/candidate/resumes"),

  getResume: (resumeId: string) =>
    api.get<ResumeDetail>(`/candidate/resume/${resumeId}`),

  // JDs
  createJD: (data: JDCreateRequest) =>
    api.post<JDUploadResponse>("/candidate/jd", data),

  listJDs: () => api.get<JDSummary[]>("/candidate/jds"),

  // Analysis
  getATSScore: (resumeId: string, jdId: string) =>
    api.get<ATSScoreResponse>("/candidate/ats-score", {
      params: { resume_id: resumeId, jd_id: jdId },
    }),

  getSkillGap: (resumeId: string, jdId: string) =>
    api.get<SkillGapResponse>("/candidate/skill-gap", {
      params: { resume_id: resumeId, jd_id: jdId },
    }),

  getLearningPlan: (resumeId: string, jdId: string) =>
    api.get<LearningPlanResponse>("/candidate/learning-plan", {
      params: { resume_id: resumeId, jd_id: jdId },
    }),

  // Interview (Phase 4)
  startInterview: (company: string, role: string) =>
    api.post<InterviewStartResponse>("/candidate/interview/start", {
      company,
      role,
    }),

  getInterviewSession: (sessionId: string) =>
    api.get<InterviewSession>(`/candidate/interview/${sessionId}`),

  respondToInterview: (sessionId: string, response: string) =>
    api.post<InterviewRespondResponse>(
      `/candidate/interview/${sessionId}/respond`,
      { response }
    ),

  getInterviewFeedback: (sessionId: string) =>
    api.get<InterviewFeedback>(`/candidate/interview/${sessionId}/feedback`),
};

// ═════════════════════════════════════════════════════════════
// Recruiter (Phase 5) — every route needs the "recruiter" role
// ═════════════════════════════════════════════════════════════

export const recruiterAPI = {
  uploadJD: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.post<JDUploadResponse>("/recruiter/jd/upload", form);
  },

  listJDs: () => api.get<JDSummary[]>("/recruiter/jds"),

  // POST: it runs the matching + ranking pipeline (not a plain read).
  rankCandidates: (jdId: string, topN?: number) =>
    api.post<CandidateRankingResponse>(
      `/recruiter/jd/${jdId}/rank-candidates`,
      topN ? ({ top_n: topN } satisfies RankCandidatesRequest) : {}
    ),

  getQuestions: (jdId: string, candidateId: string) =>
    api.get<RecruiterQuestionsResponse>(
      `/recruiter/jd/${jdId}/questions/${candidateId}`
    ),

  getRecommendation: (jdId: string, candidateId: string) =>
    api.get<HiringRecommendationResponse>(
      `/recruiter/jd/${jdId}/recommendation/${candidateId}`
    ),
};