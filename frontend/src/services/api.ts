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

// ═════════════════════════════════════════════════════════════
// Auth
// ═════════════════════════════════════════════════════════════

export const authAPI = {
  register: (data: { email: string; password: string; role: string }) =>
    api.post("/auth/register", data),

  login: (email: string, password: string) =>
    api.post(
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
    api.post("/candidate/interview/start", { company, role }),

  respondToInterview: (sessionId: string, response: string) =>
    api.post(`/candidate/interview/${sessionId}/respond`, { response }),

  getInterviewFeedback: (sessionId: string) =>
    api.get(`/candidate/interview/${sessionId}/feedback`),
};

// ═════════════════════════════════════════════════════════════
// Recruiter (Phase 5 — kept for compatibility, updated later)
// ═════════════════════════════════════════════════════════════

export const recruiterAPI = {
  uploadJD: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.post("/recruiter/jd/upload", form);
  },

  rankCandidates: (jdId: string) =>
    api.get(`/recruiter/jd/${jdId}/rank-candidates`),

  getInterviewQuestions: (jdId: string, candidateId: string) =>
    api.get(`/recruiter/jd/${jdId}/candidate/${candidateId}/questions`),

  getRecommendation: (jdId: string, candidateId: string) =>
    api.get(`/recruiter/jd/${jdId}/candidate/${candidateId}/recommendation`),
};