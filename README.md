# # 🚀 AI Recruitment and Multi - Agent Interview Preparation Platform 

## Overview

AI Recruitment and Multi - Agent Interview Preparation Platform is a full-stack AI platform with two primary user flows:

**Candidate Flow** — Upload your resume, receive an ATS score, discover skill gaps against a target job description, get a personalized weekly learning roadmap, and practice with an AI-powered mock interviewer.

**Recruiter Flow** — Upload a job description, automatically analyze required skills, match and rank a candidate pool by fit score, generate targeted interview questions, and receive a hiring recommendation (HIRE / CONSIDER / REJECT).

Both flows are orchestrated by independent LangGraph state machines that coordinate specialized LLM agents, a RAG retrieval layer backed by ChromaDB, and a PostgreSQL + Redis data layer — all running locally with no paid cloud services required.

---

## Features

### For Candidates

| Feature | Description |
|---|---|
| 📄 **Resume Parsing** | Groq `llama-3.3-70b-versatile` extracts structured data (skills, experience, education) from uploaded PDFs |
| 🎯 **ATS Scoring** | Keyword density analysis and match scoring against a target JD |
| 🔍 **Skill Gap Analysis** | Side-by-side comparison of candidate skills vs. JD requirements |
| 🗺️ **Learning Roadmap** | RAG-powered weekly learning plan with curated resources |
| 🤖 **Mock Interview Coach** | Conversational interview agent with company/role-specific questions |
| 📊 **Interview Feedback** | Multi-dimension evaluation: technical depth, communication, problem-solving |

### For Recruiters

| Feature | Description |
|---|---|
| 📋 **JD Analysis** | Extract structured requirements (skills, experience level, role type) from job descriptions |
| 🏆 **Candidate Matching** | Compute resume-to-JD match scores using local embeddings |
| 📈 **Auto-Ranking** | Sort candidates by fit score with a breakdown per dimension |
| ❓ **Question Generation** | Generate targeted interview questions based on each candidate's skill gaps |
| ✅ **Hiring Recommendation** | HIRE / CONSIDER / REJECT decision with confidence score and reasoning |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        React Frontend                           │
│          Vite · TypeScript · Tailwind · TanStack Query          │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP / REST
┌────────────────────────▼────────────────────────────────────────┐
│                    FastAPI Backend                               │
│    /api/auth   /api/candidate   /api/recruiter   /api/admin      │
│             JWT Middleware · Rate Limiting · Logging             │
└──────┬──────────────────┬──────────────────────┬────────────────┘
       │                  │                      │
┌──────▼──────┐  ┌────────▼──────────┐  ┌───────▼────────────────┐
│CandidateGraph│  │  RecruiterGraph   │  │  Auth / Admin Services  │
│ (LangGraph)  │  │  (LangGraph)      │  │                        │
│              │  │                   │  │  PostgreSQL 16          │
│ parse_resume │  │ analyze_jd        │  │  Redis 7 (sessions)     │
│ ats_check    │  │ match_candidates  │  │  data/uploads/ (files)  │
│ skill_gap    │  │ rank_candidates   │  └────────────────────────┘
│ gen_roadmap  │  └───────────────────┘
└──────┬───────┘
       │
┌──────▼────────────────────────────────────────────────────────┐
│                    Shared Agent Layer                          │
│  InterviewAgent · EvaluationAgent · MemoryAgent (Redis)       │
│  RetrieverAgent → ChromaDB (5 vector collections)             │
│  Embeddings: sentence-transformers all-MiniLM-L6-v2 (local)   │
│  LLM: Groq llama-3.3-70b-versatile (free API)                 │
│  LangSmith tracing (optional)                                  │
└────────────────────────────────────────────────────────────────┘
```

### LangGraph: Candidate Flow

```
[parse_resume] → [ats_check] → [skill_gap_analysis] → [generate_roadmap] → END
```

### LangGraph: Recruiter Flow

```
[analyze_jd] → [match_candidates] → [rank_candidates] → END
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | FastAPI 0.115, Python 3.12, Uvicorn 0.30.6 |
| **AI Orchestration** | LangGraph 0.2.45, LangChain 0.3.7 |
| **LLM** | Groq `llama-3.3-70b-versatile` via `langchain-groq` (free) |
| **Embeddings** | `sentence-transformers all-MiniLM-L6-v2` — 384-dim, runs locally, no API key |
| **Vector DB** | ChromaDB 0.5.23 — 5 collections, HTTP client, Docker container |
| **Primary DB** | PostgreSQL 16 (Docker, async via SQLAlchemy 2.0 + asyncpg) |
| **Cache / Sessions** | Redis 7 (Docker) |
| **File Storage** | Local filesystem — `data/uploads/` (replaces Azure Blob) |
| **Frontend** | React 18, TypeScript 5.5, Vite 5, Tailwind CSS 3 |
| **State Management** | Zustand 4, TanStack Query 5 |
| **Auth** | JWT (python-jose + bcrypt 4.0.1) |
| **Monitoring** | LangSmith |
| **CI/CD** | GitHub Actions (ruff + black + pytest + Docker build) |
| **Deployment** | Docker Compose (local) |

---

## Project Structure

```
ai-talent-hub/
├── .github/
│   └── workflows/
│       ├── ci.yml              # Lint (ruff + black) + pytest on every PR
│       └── deploy.yml          # Build → push → deploy on merge to main
│
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI app, lifespan hooks, middleware stack
│   │   ├── api/
│   │   │   ├── auth_routes.py
│   │   │   ├── candidate_routes.py
│   │   │   ├── recruiter_routes.py
│   │   │   └── admin_routes.py
│   │   ├── agents/
│   │   │   ├── candidate/      # ResumeParser, ATS, SkillGap, Roadmap, Interview
│   │   │   ├── recruiter/      # JDAnalyzer, Matching, Ranking, Questions, Recommendation
│   │   │   └── shared/         # Memory, Evaluation, Retriever
│   │   ├── graphs/
│   │   │   ├── candidate_graph.py   # LangGraph candidate state machine
│   │   │   ├── recruiter_graph.py   # LangGraph recruiter state machine
│   │   │   └── graph_state.py       # TalentHubState TypedDict
│   │   ├── llm/
│   │   │   ├── models.py            # get_groq_llm() — ChatGroq wrapper
│   │   │   └── prompts/             # Per-agent prompt templates
│   │   │       ├── resume_prompts.py
│   │   │       ├── ats_prompts.py
│   │   │       ├── skill_prompts.py
│   │   │       ├── roadmap_prompts.py
│   │   │       ├── interview_prompts.py
│   │   │       ├── evaluation_prompts.py
│   │   │       ├── jd_prompts.py
│   │   │       ├── matching_prompts.py
│   │   │       ├── question_prompts.py
│   │   │       └── recommendation_prompts.py
│   │   ├── rag/
│   │   │   ├── collections.py       # COLLECTIONS registry — 5 ChromaDB namespaces
│   │   │   ├── embeddings.py        # EmbeddingService (sentence-transformers)
│   │   │   ├── vector_store.py      # VectorStore — ChromaDB HTTP client wrapper
│   │   │   ├── retriever.py         # RAGRetriever — add_documents + retrieve
│   │   │   └── chunking.py          # RecursiveCharacterTextSplitter
│   │   ├── services/
│   │   │   ├── auth_service.py
│   │   │   ├── resume_service.py
│   │   │   ├── interview_service.py
│   │   │   ├── recruiter_service.py
│   │   │   └── notification_service.py
│   │   ├── database/
│   │   │   ├── models.py            # User, Resume, JobDescription, InterviewSession
│   │   │   ├── schemas.py           # Pydantic request/response schemas
│   │   │   ├── postgres.py          # Async SQLAlchemy engine + init_db()
│   │   │   └── redis.py             # Redis client singleton
│   │   └── middleware/
│   │       ├── auth.py
│   │       ├── rate_limit.py
│   │       └── logging.py
│   ├── alembic/
│   │   └── versions/
│   │       └── c31ec2fe7c14_initial_tables.py
│   ├── tests/
│   │   ├── unit/
│   │   │   ├── api/test_auth.py         # 11 auth tests passing
│   │   │   └── rag/test_retriever.py
│   │   └── integration/
│   │       ├── test_candidate_flow.py
│   │       └── test_recruiter_pipeline.py
│   └── requirements.txt
│
├── data/                        # Seeded RAG content + uploaded files
│   ├── behavioral_questions/
│   ├── company_interviews/
│   ├── dsa_notes/
│   ├── interview_experiences/
│   ├── learning_resources/
│   └── resumes/                 # Candidate resume storage (local filesystem)
│
├── docs/
│   ├── api/
│   └── architecture/
│
├── frontend/
│   ├── src/
│   │   ├── auth/
│   │   ├── candidate/
│   │   ├── recruiter/
│   │   ├── dashboard/
│   │   ├── shared/
│   │   ├── hooks/
│   │   ├── services/
│   │   │   └── api.ts           # Axios client with JWT interceptors + 401 redirect
│   │   └── utils/
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   └── package.json
│
└── docker/
    ├── Dockerfile
    └── docker-compose.yml       # postgres · redis · chromadb · backend
```

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Node.js 20+
- Python 3.12+
- Groq API key — free at [console.groq.com](https://console.groq.com)

### 1. Clone & Configure

```bash
git clone https://github.com/pranjalparmar23/ai-talent-hub.git
cd ai-talent-hub
cp backend/.env.example backend/.env
# Set GROQ_API_KEY and SECRET_KEY — everything else has working defaults
```

### 2. Run with Docker (recommended)

```bash
docker-compose -f docker/docker-compose.yml up --build
```

Starts 4 services (PostgreSQL, Redis, ChromaDB, FastAPI backend) with health checks.

- Backend API: `http://localhost:8000`
- API docs: `http://localhost:8000/api/docs`
- ChromaDB UI: `http://localhost:8001`

### 3. Backend only (dev mode)

```bash
cd backend
source ../venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

### 4. Frontend (dev mode)

```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

---

## Environment Variables

```env
# ── App ───────────────────────────────────────────────────────
SECRET_KEY=your-32-char-secret-key-here
ENVIRONMENT=development
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000

# ── Groq LLM (free — get key at console.groq.com) ─────────────
GROQ_API_KEY=gsk_...

# ── Database ──────────────────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://postgres:root@localhost:5432/talent_hub
POSTGRES_USER=postgres
POSTGRES_PASSWORD=root
POSTGRES_DB=talent_hub

# ── Redis ─────────────────────────────────────────────────────
REDIS_URL=redis://localhost:6379

# ── ChromaDB ──────────────────────────────────────────────────
CHROMA_HOST=localhost
CHROMA_PORT=8001

# ── File Storage ──────────────────────────────────────────────
UPLOAD_DIR=data/uploads

# ── Auth ──────────────────────────────────────────────────────
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# ── LangSmith (optional) ──────────────────────────────────────
LANGCHAIN_TRACING_V2=false
LANGCHAIN_API_KEY=ls__...
LANGCHAIN_PROJECT=ai-talent-hub
```

> Inside Docker Compose, `DATABASE_URL`, `REDIS_URL`, `CHROMA_HOST`, and `CHROMA_PORT` are automatically overridden to use Docker service hostnames.

---

## API Reference

Interactive docs at `http://localhost:8000/api/docs` (Swagger UI) and `/api/redoc` (ReDoc).

### Auth

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/auth/register` | Create a new user account |
| `POST` | `/api/auth/login` | Obtain JWT access + refresh tokens |
| `POST` | `/api/auth/refresh` | Refresh expired access token |
| `POST` | `/api/auth/logout` | Invalidate refresh token |

### Candidate

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/candidate/resume/upload` | Upload PDF → parse → save to `data/uploads/` |
| `POST` | `/api/candidate/ats-check` | Run ATS score against a JD |
| `GET` | `/api/candidate/skill-gap` | Get missing skills vs. JD |
| `GET` | `/api/candidate/learning-plan` | Get RAG-generated weekly roadmap |
| `POST` | `/api/candidate/interview/start` | Start a mock interview session |
| `POST` | `/api/candidate/interview/respond` | Submit an answer and get next question |
| `GET` | `/api/candidate/interview/feedback` | Get full session evaluation |

### Recruiter

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/recruiter/jd/upload` | Upload and analyze a job description |
| `POST` | `/api/recruiter/jd/:id/rank-candidates` | Run full recruiter pipeline |
| `GET` | `/api/recruiter/jd/:id/candidates` | Get ranked candidate list |
| `GET` | `/api/recruiter/jd/:id/questions/:candidateId` | Get tailored interview questions |
| `GET` | `/api/recruiter/jd/:id/recommendation/:candidateId` | Get hiring recommendation |

---

## Multi-Agent System

### Candidate Agents

| Agent | File | Responsibility |
|---|---|---|
| `ResumeParserAgent` | `agents/candidate/resume_parser.py` | Groq structured JSON extraction from raw resume text |
| `ATSAgent` | `agents/candidate/ats_agent.py` | Keyword density scoring and ATS feedback |
| `SkillGapAgent` | `agents/candidate/skill_gap_agent.py` | Diff candidate skills against JD requirements |
| `RoadmapAgent` | `agents/candidate/roadmap_agent.py` | RAG-backed weekly learning plan generation |
| `InterviewAgent` | `agents/candidate/interview_agent.py` | Adaptive question generation and follow-ups |

### Recruiter Agents

| Agent | File | Responsibility |
|---|---|---|
| `JDAnalyzerAgent` | `agents/recruiter/jd_analyzer.py` | Extract skills, role type, experience requirements from JD |
| `MatchingAgent` | `agents/recruiter/matching_agent.py` | Compute resume–JD match scores using local embeddings |
| `RankingAgent` | `agents/recruiter/ranking_agent.py` | Sort and tier candidates by score — complete ✅ |
| `QuestionGenerationAgent` | `agents/recruiter/question_agent.py` | Generate gap-based interview questions per candidate |
| `HiringRecommendationAgent` | `agents/recruiter/recommendation_agent.py` | Output HIRE / CONSIDER / REJECT with reasoning |

### Shared Agents

| Agent | File | Responsibility |
|---|---|---|
| `MemoryAgent` | `agents/shared/memory_agent.py` | Persist and retrieve Redis-backed chat history |
| `EvaluationAgent` | `agents/shared/evaluation_agent.py` | Score interview responses across multiple dimensions |
| `RetrieverAgent` | `agents/shared/retriever_agent.py` | Interface to ChromaDB RAG pipeline for all agents |

### LLM Configuration

All agents share one Groq client — no OpenAI key needed:

```python
# app/llm/models.py
from langchain_groq import ChatGroq

def get_groq_llm(temperature: float = 0):
    return ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=temperature,
        groq_api_key=os.getenv("GROQ_API_KEY")
    )
```

---

## RAG Pipeline

The platform maintains **5 ChromaDB vector collections**, seeded from `data/`:

| Collection | Content |
|---|---|
| `interview_experiences` | 50+ real interview experiences by company/role |
| `learning_resources` | Roadmaps, tutorials, and course links (Docker, K8s, AWS, etc.) |
| `company_interviews` | LP questions, system design patterns for Amazon, Google, Meta |
| `dsa_notes` | Curated DSA explanations and solution approaches |
| `behavioral_questions` | STAR templates and sample responses |

**Embedding model:** `all-MiniLM-L6-v2` via `sentence-transformers` — 384-dim, runs fully locally, no API key, ~200MB RAM. Downloads automatically on first use.

**Chunking:** `RecursiveCharacterTextSplitter` with overlapping chunks to preserve sentence-boundary context.

**Retrieval:** `RAGRetriever` exposes `add_documents()` and `retrieve()` with configurable `top_k` and optional ChromaDB metadata filters (`where` dict).

**Storage:** ChromaDB runs as a Docker container (`ath_chromadb` on port `8001`). The backend connects via `chromadb.HttpClient` — `VectorStore` is a singleton that lazy-initialises the connection on first call.

> The original 8-collection Azure AI Search setup has been replaced with 5 ChromaDB collections in Docker. Azure Blob Storage has been replaced with local `data/uploads/` filesystem storage.

---

## Project Phases

| # | Phase | Duration | Status | Tasks | Description |
|---|---|---|---|---|---|
| 1 | **Foundation & Infrastructure** | Weeks 1–3 | ✅ Complete | 15 | Monorepo, CI pipeline, Docker (PostgreSQL · Redis · ChromaDB), local file storage, SQLAlchemy models, Alembic migrations, JWT auth, React scaffold |
| 2 | **RAG Pipeline & Vector Store** | Weeks 4–5 | ✅ Complete | 10 | Local sentence-transformers embeddings, ChromaDB wrapper, RAGRetriever, text chunking, seed all 5 collections, retrieval quality evaluation |
| 3 | **Candidate Flow – Core Agents** | Weeks 6–8 | 🔄 In Progress | 14 | ResumeParserAgent, ATSAgent, SkillGapAgent, RoadmapAgent, CandidateGraph (LangGraph), resume upload UI, ATS dashboard, skill gap chart, learning plan UI |
| 4 | **Interview Coach Module** | Weeks 9–10 | 🔄 In Progress | 10 | InterviewAgent, MemoryAgent (Redis), EvaluationAgent, interview chat UI, company/role selector, feedback radar chart |
| 5 | **Recruiter Flow** | Weeks 11–12 | 🔄 In Progress | 14 | JDAnalyzerAgent, MatchingAgent, RankingAgent ✅, QuestionGenerationAgent, HiringRecommendationAgent, RecruiterGraph, JD upload UI, candidate rankings table |
| 6 | **Deployment & Production Hardening** | Weeks 13–15 | 📋 To Do | 15 | Production Docker images, Render/Railway/Fly.io for backend, Vercel for frontend, HTTPS, managed Postgres (Neon/Supabase), managed Redis (Upstash), load testing, security audit |

**Total: 6 phases · 15 weeks · 78 tasks**

### Phase 1 — Foundation & Infrastructure ✅ *(Weeks 1–3)*

Fully complete. Sets up the entire development environment and all baseline services.

Delivered: Git monorepo with branching strategy, GitHub Actions CI (`ruff` + `black` + `pytest`), Docker Compose for local dev (PostgreSQL 16, Redis 7, ChromaDB 0.5.23, FastAPI backend), 5 ChromaDB collection namespaces configured, local `data/uploads/` file storage replacing Azure Blob, SQLAlchemy ORM models (`User`, `Resume`, `JobDescription`, `InterviewSession`) with FK cascades and indexes, Alembic initial migration (`alembic upgrade head` verified — 4 tables created), JWT auth endpoints (register / login / refresh / logout) with bcrypt 4.0.1, Pydantic schemas with password and role validators, React + Vite + TypeScript + Tailwind scaffold, Axios API client with auth header interceptors and 401 redirect, Login/Register stub pages, 11 auth unit tests passing.

### Phase 2 — RAG Pipeline & Vector Store ✅ *(Weeks 4–5)*

Fully complete. Builds the knowledge retrieval backbone used by every downstream agent.

Delivered: `EmbeddingService` using `all-MiniLM-L6-v2` (384-dim, fully local, no API key needed), `VectorStore` ChromaDB HTTP client wrapper with collection lifecycle management and batch document operations, `RAGRetriever` with `add_documents()` and `retrieve()`, `RecursiveCharacterTextSplitter` chunking module, all 5 ChromaDB collections seeded from `data/` directory, `test_retriever.py` unit tests, precision@5 evaluation over 20 query-answer pairs.

### Phase 3 — Candidate Flow: Core Agents 🔄 *(Weeks 6–8)*

In progress. `TalentHubState` TypedDict and `CandidateGraph` scaffold complete. All 5 candidate agents (ResumeParser, ATS, SkillGap, Roadmap, Interview) need full Groq-powered implementations. Frontend components for resume upload, ATS dashboard, skill gap chart, and learning plan UI are pending.

### Phase 4 — Interview Coach Module 🔄 *(Weeks 9–10)*

In progress. Interview API routes are wired. `MemoryAgent` Redis scaffold complete. `InterviewAgent`, `EvaluationAgent`, `InterviewService`, and all frontend components pending.

### Phase 5 — Recruiter Flow 🔄 *(Weeks 11–12)*

In progress. `RankingAgent` complete (pure Python sort). `RecruiterGraph` scaffold and JD upload route are done. `JDAnalyzerAgent`, `MatchingAgent`, `QuestionGenerationAgent`, and `HiringRecommendationAgent` pending full implementations.

### Phase 6 — Deployment & Production Hardening 📋 *(Weeks 13–15)*

Not yet started. Deployment targets free-tier platforms — no Azure required: backend on Render / Railway / Fly.io, frontend on Vercel, managed Postgres on Neon or Supabase, managed Redis on Upstash.

---

## Testing

```bash
cd backend

# Unit tests (fast, mocked LLM)
pytest tests/unit -v

# Integration tests (requires live DB + Redis + ChromaDB)
pytest tests/integration -v

# Full suite with HTML coverage report
pytest tests/ --cov=app --cov-report=html

# CI coverage gate (≥70%)
pytest tests/ --cov=app --cov-fail-under=70
```

**Frontend:**

```bash
cd frontend
npm test              # vitest single pass
npm run test:watch    # watch mode
```

### Test Structure

```
backend/tests/
├── unit/
│   ├── api/
│   │   └── test_auth.py          # 11 tests — register, login, refresh, logout, duplicates
│   └── rag/
│       └── test_retriever.py     # RAGRetriever add + retrieve unit tests
└── integration/
    ├── test_candidate_flow.py    # Full candidate graph pipeline
    └── test_recruiter_pipeline.py
```

---

## CI/CD

### CI Pipeline (`.github/workflows/ci.yml`)

Triggered on every push to `main` / `develop` and all PRs to `main`.

```
lint (ruff + black) ──┐
                       ├──→ build (Docker image verify)
test (pytest)         ──┘
```

PostgreSQL 16 and Redis 7 service containers are spun up automatically for integration tests.

### Deploy Pipeline (`.github/workflows/deploy.yml`)

Triggered on merge to `main` after CI passes.

1. Build production Docker image (multi-stage, slim)
2. Push to container registry
3. Deploy backend to Render / Railway / Fly.io
4. Deploy frontend to Vercel / Netlify

---

## Deployment

### Local (Docker Compose)

```bash
docker-compose -f docker/docker-compose.yml up --build
```

All 4 services start with health checks and automatic container restart.

### Production

| Service | Platform |
|---|---|
| Backend API | Render / Railway / Fly.io |
| Frontend | Vercel / Netlify / Cloudflare Pages |
| PostgreSQL | Neon / Supabase / Railway (free tier) |
| Redis | Upstash / Redis Cloud (free tier) |
| ChromaDB | Self-hosted on same backend VPS |

---
