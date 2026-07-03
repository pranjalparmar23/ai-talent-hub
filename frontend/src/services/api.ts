import axios from "axios";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000/api";

export const api = axios.create({ baseURL: API_BASE });

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
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

export const authAPI = {
  register: (data: object) => api.post("/auth/register", data),
  login: (email: string, password: string) =>
    api.post(
      "/auth/login",
      new URLSearchParams({ username: email, password }),
      { headers: { "Content-Type": "application/x-www-form-urlencoded" } }
    ),
  logout: () => api.post("/auth/logout"),
};

export const candidateAPI = {
  uploadResume: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.post("/candidate/resume/upload", form);
  },
  getATSScore: (resumeId: string, jdId: string) =>
    api.get(`/candidate/ats-score/${resumeId}?jd_id=${jdId}`),
  getSkillGap: (resumeId: string, jdId: string) =>
    api.get(`/candidate/skill-gap?resume_id=${resumeId}&jd_id=${jdId}`),
  getLearningPlan: (resumeId: string, jdId: string) =>
    api.get(`/candidate/learning-plan?resume_id=${resumeId}&jd_id=${jdId}`),
  startInterview: (company: string, role: string) =>
    api.post(`/candidate/interview/start?company=${company}&role=${role}`),
  respondToInterview: (sessionId: string, response: string) =>
    api.post(`/candidate/interview/${sessionId}/respond?response=${response}`),
  getInterviewFeedback: (sessionId: string) =>
    api.get(`/candidate/interview/${sessionId}/feedback`),
};

export const recruiterAPI = {
  uploadJD: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.post("/recruiter/jd/upload", form);
  },
  rankCandidates: (jdId: string) => api.post(`/recruiter/jd/${jdId}/rank-candidates`),
  getQuestions: (jdId: string, candidateId: string) =>
    api.get(`/recruiter/jd/${jdId}/questions/${candidateId}`),
  getRecommendation: (jdId: string, candidateId: string) =>
    api.get(`/recruiter/jd/${jdId}/recommendation/${candidateId}`),
};
