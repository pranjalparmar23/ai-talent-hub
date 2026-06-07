# 🚀 AI Recruitment and Multi - Agent Interview Preparation Platform

AI Recruitment and Multi - Agent Interview Preparation Platform is a full-stack AI platform with two primary user flows:

- **Candidate Flow** — Upload your resume, receive an ATS score, discover skill gaps against a target job description, get a personalized weekly learning roadmap, and practice with an AI-powered mock interviewer.
- **Recruiter Flow** — Upload a job description, automatically analyze required skills, match and rank a candidate pool by fit score, generate targeted interview questions, and receive a hiring recommendation (HIRE / CONSIDER / REJECT).

Both flows are orchestrated by independent LangGraph state machines that coordinate specialized LLM agents, a RAG retrieval layer backed by Azure AI Search, and a PostgreSQL + Redis data layer.

---

## Features

### For Candidates
| Feature | Description |
|---|---|
| 📄 **Resume Parsing** | GPT-4o extracts structured data (skills, experience, education) from uploaded PDFs |
| 🎯 **ATS Scoring** | Keyword density analysis and match scoring against a target JD |
| 🔍 **Skill Gap Analysis** | Side-by-side comparison of candidate skills vs. JD requirements |
| 🗺️ **Learning Roadmap** | RAG-powered weekly learning plan with curated resources |
| 🤖 **Mock Interview Coach** | Conversational interview agent with company/role-specific questions |
| 📊 **Interview Feedback** | Multi-dimension evaluation: technical depth, communication, problem-solving |

### For Recruiters
| Feature | Description |
|---|---|
| 📋 **JD Analysis** | Extract structured requirements (skills, experience level, role type) from job descriptions |
| 🏆 **Candidate Matching** | Compute resume-to-JD match scores for a candidate pool |
| 📈 **Auto-Ranking** | Sort candidates by fit score with a breakdown per dimension |
| ❓ **Question Generation** | Generate targeted interview questions based on each candidate's skill gaps |
| ✅ **Hiring Recommendation** | HIRE / CONSIDER / REJECT decision with confidence score and reasoning |

---
# 🚀 AI Recruitment and Multi - Agent Interview Preparation Platform

AI Recruitment and Multi - Agent Interview Preparation Platform is a full-stack AI platform with two primary user flows:

- **Candidate Flow** — Upload your resume, receive an ATS score, discover skill gaps against a target job description, get a personalized weekly learning roadmap, and practice with an AI-powered mock interviewer.
- **Recruiter Flow** — Upload a job description, automatically analyze required skills, match and rank a candidate pool by fit score, generate targeted interview questions, and receive a hiring recommendation (HIRE / CONSIDER / REJECT).

Both flows are orchestrated by independent LangGraph state machines that coordinate specialized LLM agents, a RAG retrieval layer backed by Azure AI Search, and a PostgreSQL + Redis data layer.

---

## Features

### For Candidates
| Feature | Description |
|---|---|
| 📄 **Resume Parsing** | GPT-4o extracts structured data (skills, experience, education) from uploaded PDFs |
| 🎯 **ATS Scoring** | Keyword density analysis and match scoring against a target JD |
| 🔍 **Skill Gap Analysis** | Side-by-side comparison of candidate skills vs. JD requirements |
| 🗺️ **Learning Roadmap** | RAG-powered weekly learning plan with curated resources |
| 🤖 **Mock Interview Coach** | Conversational interview agent with company/role-specific questions |
| 📊 **Interview Feedback** | Multi-dimension evaluation: technical depth, communication, problem-solving |

### For Recruiters
| Feature | Description |
|---|---|
| 📋 **JD Analysis** | Extract structured requirements (skills, experience level, role type) from job descriptions |
| 🏆 **Candidate Matching** | Compute resume-to-JD match scores for a candidate pool |
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
                         │ HTTPS / REST
┌────────────────────────▼────────────────────────────────────────┐
│                    FastAPI Backend                               │
│    /api/auth   /api/candidate   /api/recruiter   /api/admin      │
│                   JWT Middleware · Rate Limiting                 │
└──────┬──────────────────┬──────────────────────┬────────────────┘
       │                  │                      │
┌──────▼──────┐  ┌────────▼──────────┐  ┌───────▼────────────────┐
│ CandidateGraph│  │  RecruiterGraph   │  │   Auth / Admin Services │
│  (LangGraph)  │  │   (LangGraph)    │  │                        │
│               │  │                  │  │  PostgreSQL (Azure)     │
│ parse_resume  │  │ analyze_jd       │  │  Redis (sessions)       │
│ ats_check     │  │ match_candidates │  │  Azure Blob Storage     │
│ skill_gap     │  │ rank_candidates  │  └────────────────────────┘
│ generate_     │  └──────────────────┘
│   roadmap     │
└──────┬────────┘
       │
┌──────▼───────────────────────────────────────────────────────┐
│                    Shared Agent Layer                         │
│  InterviewAgent · EvaluationAgent · MemoryAgent (Redis)      │
│  RetrieverAgent → Azure AI Search (8 vector collections)     │
│  OpenAI text-embedding-3-large · GPT-4o                      │
│  LangSmith tracing                                           │
└──────────────────────────────────────────────────────────────┘
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
| **Backend** | FastAPI 0.115, Python 3.12, Uvicorn |
| **AI Orchestration** | LangGraph 0.2, LangChain 0.3 |
| **LLMs** | OpenAI GPT-4o, Anthropic Claude (via LangChain) |
| **Embeddings** | OpenAI `text-embedding-3-large` |
| **Vector DB** | Azure AI Search (8 index collections) |
| **Primary DB** | PostgreSQL 16 on Azure (async via SQLAlchemy 2.0 + asyncpg) |
| **Cache / Sessions** | Redis 7 |
| **File Storage** | Azure Blob Storage |
| **Frontend** | React 18, TypeScript 5.5, Vite 5, Tailwind CSS 3 |
| **State Management** | Zustand 4, TanStack Query 5 |
| **Auth** | JWT (python-jose + bcrypt) |
| **Monitoring** | LangSmith |
| **CI/CD** | GitHub Actions → Azure Container Registry → Azure App Service / AKS |

---

## Project Structure

```
ai-talent-hub/
├── .github/
│   └── workflows/
│       ├── ci.yml            # Lint + test on every PR
│       └── deploy.yml        # Build → push to ACR → deploy
│
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app entry point
│   │   ├── api/              # Route handlers
│   │   │   ├── auth_routes.py
│   │   │   ├── candidate_routes.py
│   │   │   ├── recruiter_routes.py
│   │   │   └── admin_routes.py
│   │   ├── agents/
│   │   │   ├── candidate/    # ResumeParser, ATS, SkillGap, Roadmap, Interview
│   │   │   ├── recruiter/    # JDAnalyzer, Matching, Ranking, Questions, Recommendation
│   │   │   └── shared/       # Memory, Evaluation, Retriever
│   │   ├── graphs/
│   │   │   ├── candidate_graph.py   # LangGraph candidate state machine
│   │   │   ├── recruiter_graph.py   # LangGraph recruiter state machine
│   │   │   └── graph_state.py       # TalentHubState TypedDict
│   │   ├── rag/
│   │   │   ├── embeddings.py
│   │   │   ├── vector_store.py      # Azure AI Search wrapper
│   │   │   ├── retriever.py         # RAGRetriever with similarity search
│   │   │   └── chunking.py          # RecursiveCharacterTextSplitter
│   │   ├── services/
│   │   │   ├── auth_service.py
│   │   │   ├── resume_service.py
│   │   │   ├── interview_service.py
│   │   │   ├── recruiter_service.py
│   │   │   └── notification_service.py
│   │   ├── database/
│   │   │   ├── models.py     # SQLAlchemy ORM models
│   │   │   ├── schemas.py    # Pydantic request/response schemas
│   │   │   └── postgres.py   # Async DB connection
│   │   └── middleware/
│   │       ├── auth.py
│   │       ├── rate_limit.py
│   │       └── logging.py
│   ├── tests/
│   │   ├── unit/             # pytest unit tests (mocked LLM)
│   │   └── integration/      # pytest integration tests (real services)
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── auth/             # Login, Register pages
│   │   ├── candidate/        # Resume upload, ATS score, Skill gap, Interview chat
│   │   ├── recruiter/        # JD upload, Candidate rankings, Interview questions
│   │   ├── dashboard/        # Admin analytics
│   │   ├── services/
│   │   │   └── api.ts        # Axios client with JWT interceptors
│   │   ├── hooks/
│   │   ├── shared/
│   │   └── utils/
│   └── package.json
│
├── k8s/
│   ├── deployment.yaml
│   ├── service.yaml
│   └── ingress.yaml
│
└── docker/
    ├── Dockerfile
    └── docker-compose.yml
```

---
```
┌─────────────────────────────────────────────────────────────────┐
│                        React Frontend                           │
│          Vite · TypeScript · Tailwind · TanStack Query          │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTPS / REST
┌────────────────────────▼────────────────────────────────────────┐
│                    FastAPI Backend                               │
│    /api/auth   /api/candidate   /api/recruiter   /api/admin      │
│                   JWT Middleware · Rate Limiting                 │
└──────┬──────────────────┬──────────────────────┬────────────────┘
       │                  │                      │
┌──────▼──────┐  ┌────────▼──────────┐  ┌───────▼────────────────┐
│ CandidateGraph│  │  RecruiterGraph   │  │   Auth / Admin Services │
│  (LangGraph)  │  │   (LangGraph)    │  │                        │
│               │  │                  │  │  PostgreSQL (Azure)     │
│ parse_resume  │  │ analyze_jd       │  │  Redis (sessions)       │
│ ats_check     │  │ match_candidates │  │  Azure Blob Storage     │
│ skill_gap     │  │ rank_candidates  │  └────────────────────────┘
│ generate_     │  └──────────────────┘
│   roadmap     │
└──────┬────────┘
       │
┌──────▼───────────────────────────────────────────────────────┐
│                    Shared Agent Layer                         │
│  InterviewAgent · EvaluationAgent · MemoryAgent (Redis)      │
│  RetrieverAgent → Azure AI Search (8 vector collections)     │
│  OpenAI text-embedding-3-large · GPT-4o                      │
│  LangSmith tracing                                           │
└──────────────────────────────────────────────────────────────┘
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
| **Backend** | FastAPI 0.115, Python 3.12, Uvicorn |
| **AI Orchestration** | LangGraph 0.2, LangChain 0.3 |
| **LLMs** | OpenAI GPT-4o, Anthropic Claude (via LangChain) |
| **Embeddings** | OpenAI `text-embedding-3-large` |
| **Vector DB** | Azure AI Search (8 index collections) |
| **Primary DB** | PostgreSQL 16 on Azure (async via SQLAlchemy 2.0 + asyncpg) |
| **Cache / Sessions** | Redis 7 |
| **File Storage** | Azure Blob Storage |
| **Frontend** | React 18, TypeScript 5.5, Vite 5, Tailwind CSS 3 |
| **State Management** | Zustand 4, TanStack Query 5 |
| **Auth** | JWT (python-jose + bcrypt) |
| **Monitoring** | LangSmith |
| **CI/CD** | GitHub Actions → Azure Container Registry → Azure App Service / AKS |

---

## Project Structure

```
ai-talent-hub/
├── .github/
│   └── workflows/
│       ├── ci.yml            # Lint + test on every PR
│       └── deploy.yml        # Build → push to ACR → deploy
│
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app entry point
│   │   ├── api/              # Route handlers
│   │   │   ├── auth_routes.py
│   │   │   ├── candidate_routes.py
│   │   │   ├── recruiter_routes.py
│   │   │   └── admin_routes.py
│   │   ├── agents/
│   │   │   ├── candidate/    # ResumeParser, ATS, SkillGap, Roadmap, Interview
│   │   │   ├── recruiter/    # JDAnalyzer, Matching, Ranking, Questions, Recommendation
│   │   │   └── shared/       # Memory, Evaluation, Retriever
│   │   ├── graphs/
│   │   │   ├── candidate_graph.py   # LangGraph candidate state machine
│   │   │   ├── recruiter_graph.py   # LangGraph recruiter state machine
│   │   │   └── graph_state.py       # TalentHubState TypedDict
│   │   ├── rag/
│   │   │   ├── embeddings.py
│   │   │   ├── vector_store.py      # Azure AI Search wrapper
│   │   │   ├── retriever.py         # RAGRetriever with similarity search
│   │   │   └── chunking.py          # RecursiveCharacterTextSplitter
│   │   ├── services/
│   │   │   ├── auth_service.py
│   │   │   ├── resume_service.py
│   │   │   ├── interview_service.py
│   │   │   ├── recruiter_service.py
│   │   │   └── notification_service.py
│   │   ├── database/
│   │   │   ├── models.py     # SQLAlchemy ORM models
│   │   │   ├── schemas.py    # Pydantic request/response schemas
│   │   │   └── postgres.py   # Async DB connection
│   │   └── middleware/
│   │       ├── auth.py
│   │       ├── rate_limit.py
│   │       └── logging.py
│   ├── tests/
│   │   ├── unit/             # pytest unit tests (mocked LLM)
│   │   └── integration/      # pytest integration tests (real services)
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── auth/             # Login, Register pages
│   │   ├── candidate/        # Resume upload, ATS score, Skill gap, Interview chat
│   │   ├── recruiter/        # JD upload, Candidate rankings, Interview questions
│   │   ├── dashboard/        # Admin analytics
│   │   ├── services/
│   │   │   └── api.ts        # Axios client with JWT interceptors
│   │   ├── hooks/
│   │   ├── shared/
│   │   └── utils/
│   └── package.json
│
├── k8s/
│   ├── deployment.yaml
│   ├── service.yaml
│   └── ingress.yaml
│
└── docker/
    ├── Dockerfile
    └── docker-compose.yml
```

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Node.js 20+
- Python 3.12+
- Azure account (for PostgreSQL, Redis, Blob Storage, AI Search)
- OpenAI API key

### 1. Clone & Configure

### Prerequisites

- Docker & Docker Compose
- Node.js 20+
- Python 3.12+
- Azure account (for PostgreSQL, Redis, Blob Storage, AI Search)
- OpenAI API key

### 1. Clone & Configure

```bash
git clone https://github.com/your-org/ai-talent-hub.git
cd ai-talent-hub
git clone https://github.com/your-org/ai-talent-hub.git
cd ai-talent-hub
cp backend/.env.example backend/.env
# Edit backend/.env with your credentials (see Environment Variables below)
```
# Edit backend/.env with your credentials (see Environment Variables below)
```

### 2. Run with Docker (recommended)

```bash
docker-compose -f docker/docker-compose.yml up --build
```

Services started:
- Backend API: `http://localhost:8000`
- Frontend: `http://localhost:5173`
- API docs: `http://localhost:8000/api/docs`

### 3. Backend only (dev mode)

```bash
cd backend
pip install -r requirements.txt
alembic upgrade head          # Run DB migrations
### 2. Run with Docker (recommended)

```bash
docker-compose -f docker/docker-compose.yml up --build
```

Services started:
- Backend API: `http://localhost:8000`
- Frontend: `http://localhost:5173`
- API docs: `http://localhost:8000/api/docs`

### 3. Backend only (dev mode)

```bash
cd backend
pip install -r requirements.txt
alembic upgrade head          # Run DB migrations
uvicorn app.main:app --reload
```

### 4. Frontend only (dev mode)

```bash
cd frontend
npm install
npm run dev
```

---

## Environment Variables

Create `backend/.env` from the example file and populate:

```env
# Database
DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>:5432/talent_hub
REDIS_URL=redis://<host>:6379

# Auth
SECRET_KEY=your-32-char-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# OpenAI
OPENAI_API_KEY=sk-...

# Anthropic (optional fallback LLM)
ANTHROPIC_API_KEY=sk-ant-...

# Azure Storage
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;...
AZURE_STORAGE_CONTAINER_NAME=resumes

# Azure AI Search
AZURE_SEARCH_ENDPOINT=https://<resource>.search.windows.net
AZURE_SEARCH_API_KEY=...

# LangSmith (observability)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__...
LANGCHAIN_PROJECT=ai-talent-hub
```

---

## API Reference

Interactive docs available at `http://localhost:8000/api/docs` (Swagger UI) and `/api/redoc` (ReDoc).

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
| `POST` | `/api/candidate/resume/upload` | Upload PDF → parse → store in Blob |
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
| `ResumeParserAgent` | `agents/candidate/resume_parser.py` | GPT-4o structured JSON extraction from raw resume text |
| `ATSAgent` | `agents/candidate/ats_agent.py` | Keyword density scoring and ATS feedback |
| `SkillGapAgent` | `agents/candidate/skill_gap_agent.py` | Diff candidate skills against JD requirements |
| `RoadmapAgent` | `agents/candidate/roadmap_agent.py` | RAG-backed weekly learning plan generation |
| `InterviewAgent` | `agents/candidate/interview_agent.py` | Adaptive question generation and follow-ups |

### Recruiter Agents

| Agent | File | Responsibility |
|---|---|---|
| `JDAnalyzerAgent` | `agents/recruiter/jd_analyzer.py` | Extract skills, role type, experience requirements from JD |
| `MatchingAgent` | `agents/recruiter/matching_agent.py` | Compute resume–JD match scores |
| `RankingAgent` | `agents/recruiter/ranking_agent.py` | Sort and tier candidates by score |
| `QuestionGenerationAgent` | `agents/recruiter/question_agent.py` | Generate gap-based interview questions per candidate |
| `HiringRecommendationAgent` | `agents/recruiter/recommendation_agent.py` | Output HIRE / CONSIDER / REJECT with reasoning |

### Shared Agents

| Agent | File | Responsibility |
|---|---|---|
| `MemoryAgent` | `agents/shared/memory_agent.py` | Persist and retrieve Redis-backed chat history via LangChain |
| `EvaluationAgent` | `agents/shared/evaluation_agent.py` | Score interview responses across multiple dimensions |
| `RetrieverAgent` | `agents/shared/retriever_agent.py` | Interface to RAG pipeline for all agents |

## RAG Pipeline

The platform maintains **8 vector index collections** in Azure AI Search, seeded with curated content:

| Collection | Content |
|---|---|
| `interview_experiences` | 50+ real interview experiences by company/role |
| `learning_resources` | Roadmaps, tutorials, and course links (Docker, K8s, AWS, etc.) |
| `company_interviews` | LP questions, system design patterns for Amazon, Google, Meta |
| `dsa_notes` | Curated DSA explanations and solution approaches |
| `behavioral_questions` | STAR templates and sample responses |
| `resume_templates` | Industry-specific resume structures |
| `jd_patterns` | Common JD structures by role family |
| `skill_taxonomies` | Skill ontologies for gap computation |

**Embedding model:** `text-embedding-3-large` (3072 dimensions)

**Chunking strategy:** `RecursiveCharacterTextSplitter` — chunks overlap at sentence boundaries to preserve context.

**Retrieval:** `RAGRetriever` exposes `add_documents()` and `retrieve()` with configurable `top_k` and similarity threshold.

---
```

### 4. Frontend only (dev mode)

```bash
cd frontend
npm install
npm run dev
```

---

## Environment Variables

Create `backend/.env` from the example file and populate:

```env
# Database
DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>:5432/talent_hub
REDIS_URL=redis://<host>:6379

# Auth
SECRET_KEY=your-32-char-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# OpenAI
OPENAI_API_KEY=sk-...

# Anthropic (optional fallback LLM)
ANTHROPIC_API_KEY=sk-ant-...

# Azure Storage
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;...
AZURE_STORAGE_CONTAINER_NAME=resumes

# Azure AI Search
AZURE_SEARCH_ENDPOINT=https://<resource>.search.windows.net
AZURE_SEARCH_API_KEY=...

# LangSmith (observability)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__...
LANGCHAIN_PROJECT=ai-talent-hub
```

---

## API Reference

Interactive docs available at `http://localhost:8000/api/docs` (Swagger UI) and `/api/redoc` (ReDoc).

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
| `POST` | `/api/candidate/resume/upload` | Upload PDF → parse → store in Blob |
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
| `ResumeParserAgent` | `agents/candidate/resume_parser.py` | GPT-4o structured JSON extraction from raw resume text |
| `ATSAgent` | `agents/candidate/ats_agent.py` | Keyword density scoring and ATS feedback |
| `SkillGapAgent` | `agents/candidate/skill_gap_agent.py` | Diff candidate skills against JD requirements |
| `RoadmapAgent` | `agents/candidate/roadmap_agent.py` | RAG-backed weekly learning plan generation |
| `InterviewAgent` | `agents/candidate/interview_agent.py` | Adaptive question generation and follow-ups |

### Recruiter Agents

| Agent | File | Responsibility |
|---|---|---|
| `JDAnalyzerAgent` | `agents/recruiter/jd_analyzer.py` | Extract skills, role type, experience requirements from JD |
| `MatchingAgent` | `agents/recruiter/matching_agent.py` | Compute resume–JD match scores |
| `RankingAgent` | `agents/recruiter/ranking_agent.py` | Sort and tier candidates by score |
| `QuestionGenerationAgent` | `agents/recruiter/question_agent.py` | Generate gap-based interview questions per candidate |
| `HiringRecommendationAgent` | `agents/recruiter/recommendation_agent.py` | Output HIRE / CONSIDER / REJECT with reasoning |

### Shared Agents

| Agent | File | Responsibility |
|---|---|---|
| `MemoryAgent` | `agents/shared/memory_agent.py` | Persist and retrieve Redis-backed chat history via LangChain |
| `EvaluationAgent` | `agents/shared/evaluation_agent.py` | Score interview responses across multiple dimensions |
| `RetrieverAgent` | `agents/shared/retriever_agent.py` | Interface to RAG pipeline for all agents |

## RAG Pipeline

The platform maintains **8 vector index collections** in Azure AI Search, seeded with curated content:

| Collection | Content |
|---|---|
| `interview_experiences` | 50+ real interview experiences by company/role |
| `learning_resources` | Roadmaps, tutorials, and course links (Docker, K8s, AWS, etc.) |
| `company_interviews` | LP questions, system design patterns for Amazon, Google, Meta |
| `dsa_notes` | Curated DSA explanations and solution approaches |
| `behavioral_questions` | STAR templates and sample responses |
| `resume_templates` | Industry-specific resume structures |
| `jd_patterns` | Common JD structures by role family |
| `skill_taxonomies` | Skill ontologies for gap computation |

**Embedding model:** `text-embedding-3-large` (3072 dimensions)

**Chunking strategy:** `RecursiveCharacterTextSplitter` — chunks overlap at sentence boundaries to preserve context.

**Retrieval:** `RAGRetriever` exposes `add_documents()` and `retrieve()` with configurable `top_k` and similarity threshold.

---

## Project Phases

| # | Phase | Duration | Tasks | Description |
|---|---|---|---|---|
| 1 | **Foundation & Infrastructure** | Weeks 1–3 | 15 | Monorepo, CI pipeline, Azure provisioning (PostgreSQL, Redis, Blob, AI Search), Docker, SQLAlchemy models, Alembic migrations, JWT auth, React scaffold |
| 2 | **RAG Pipeline & Vector Store** | Weeks 4–5 | 10 | OpenAI embeddings, Azure AI Search wrapper, RAGRetriever, text chunking, seed all 8 vector collections, retrieval quality evaluation |
| 3 | **Candidate Flow – Core Agents** | Weeks 6–8 | 14 | ResumeParserAgent, ATSAgent, SkillGapAgent, RoadmapAgent, CandidateGraph (LangGraph), resume upload UI, ATS dashboard, skill gap chart, learning plan UI |
| 4 | **Interview Coach Module** | Weeks 9–10 | 10 | InterviewAgent, MemoryAgent (Redis), EvaluationAgent, interview chat UI, company/role selector, feedback radar chart |
| 5 | **Recruiter Flow** | Weeks 11–12 | 14 | JDAnalyzerAgent, MatchingAgent, RankingAgent, QuestionGenerationAgent, HiringRecommendationAgent, RecruiterGraph, JD upload UI, candidate rankings table, hiring recommendation cards |
| 6 | **Cloud Deployment & Production Hardening** | Weeks 13–15 | 15 | ACR + AKS/App Service deployment, GitHub Actions deploy pipeline, HTTPS/SSL, Kubernetes ingress, rate limiting, structured JSON logging, LangSmith dashboard, Locust load testing, OWASP security audit, admin dashboard, notification service |

**Total: 6 phases · 15 weeks · 78 tasks**

### Phase 1 — Foundation & Infrastructure *(Weeks 1–3)*

Sets up the entire development environment and baseline services. Nothing in later phases can proceed without this foundation.

Key deliverables: monorepo structure, GitHub Actions CI (ruff + black + pytest), Azure PostgreSQL and Redis, Docker Compose for local dev, Azure Blob Storage and AI Search provisioned with 8 index namespaces, LangSmith project, SQLAlchemy models (`User`, `Resume`, `JD`, `Session`), Alembic migration, JWT auth endpoints, Pydantic schemas, React + Vite + Tailwind scaffold, Axios API client with auth interceptors, Login/Register UI.

### Phase 2 — RAG Pipeline & Vector Store *(Weeks 4–5)*

Builds the knowledge retrieval backbone used by every downstream agent.

Key deliverables: `rag/embeddings.py` (text-embedding-3-large), `rag/vector_store.py` (Azure AI Search wrapper), `rag/retriever.py` (similarity search), `rag/chunking.py` (RecursiveCharacterTextSplitter), seeding of all 8 collections with 50+ interview experiences, learning resources, DSA notes, STAR templates, and company-specific interview patterns. Retrieval quality evaluation at precision@5.

### Phase 3 — Candidate Flow: Core Agents *(Weeks 6–8)*

Implements the full candidate analysis pipeline and its frontend dashboard.

Key deliverables: `ResumeParserAgent` (GPT-4o structured JSON), PDF extraction via pypdf, `POST /api/candidate/resume/upload`, `ATSAgent`, `SkillGapAgent`, `RoadmapAgent`, `CandidateGraph` wiring parse→ATS→gap→roadmap in LangGraph, `TalentHubState`, drag-and-drop resume upload UI, ATS score gauge dashboard, Recharts skill gap bar chart, weekly learning plan cards.

### Phase 4 — Interview Coach Module *(Weeks 9–10)*

Adds the conversational AI mock interviewer with memory and evaluation.

Key deliverables: `InterviewAgent` (session start + adaptive follow-up), `MemoryAgent` (LangChain + Redis chat history), `EvaluationAgent` (scores: technical, communication, problem-solving, culture fit), `InterviewService`, interview endpoints (start / respond / feedback), real-time interview chat UI with message bubbles, company/role selector dropdown, feedback radar chart.

### Phase 5 — Recruiter Flow *(Weeks 11–12)*

Builds the employer-side pipeline for ranking and recommending candidates.

Key deliverables: `JDAnalyzerAgent`, `MatchingAgent`, `RankingAgent`, `QuestionGenerationAgent`, `HiringRecommendationAgent` (HIRE / CONSIDER / REJECT + confidence %), `RecruiterGraph` (JD→match→rank), `POST /api/recruiter/jd/upload`, `POST /jd/:id/rank-candidates`, JD PDF upload UI, candidate rankings table with score breakdown, per-candidate interview question list, hiring recommendation card with badge and reasoning.

### Phase 6 — Cloud Deployment & Production Hardening *(Weeks 13–15)*

Takes the platform to production on Azure with full observability, security, and load testing.

Key deliverables: Azure Container Registry, AKS deployment (or App Service), Azure Static Web Apps for frontend, `deploy.yml` GitHub Actions workflow, HTTPS + SSL cert, Kubernetes ingress (nginx), rate limiting middleware, structured JSON logging → Azure Monitor, LangSmith dashboard for token cost and latency tracking, Locust load test (100 concurrent users), OWASP Top 10 security audit, E2E tests on staging, admin analytics dashboard, email notification service, final production launch checklist.

---
| # | Phase | Duration | Tasks | Description |
|---|---|---|---|---|
| 1 | **Foundation & Infrastructure** | Weeks 1–3 | 15 | Monorepo, CI pipeline, Azure provisioning (PostgreSQL, Redis, Blob, AI Search), Docker, SQLAlchemy models, Alembic migrations, JWT auth, React scaffold |
| 2 | **RAG Pipeline & Vector Store** | Weeks 4–5 | 10 | OpenAI embeddings, Azure AI Search wrapper, RAGRetriever, text chunking, seed all 8 vector collections, retrieval quality evaluation |
| 3 | **Candidate Flow – Core Agents** | Weeks 6–8 | 14 | ResumeParserAgent, ATSAgent, SkillGapAgent, RoadmapAgent, CandidateGraph (LangGraph), resume upload UI, ATS dashboard, skill gap chart, learning plan UI |
| 4 | **Interview Coach Module** | Weeks 9–10 | 10 | InterviewAgent, MemoryAgent (Redis), EvaluationAgent, interview chat UI, company/role selector, feedback radar chart |
| 5 | **Recruiter Flow** | Weeks 11–12 | 14 | JDAnalyzerAgent, MatchingAgent, RankingAgent, QuestionGenerationAgent, HiringRecommendationAgent, RecruiterGraph, JD upload UI, candidate rankings table, hiring recommendation cards |
| 6 | **Cloud Deployment & Production Hardening** | Weeks 13–15 | 15 | ACR + AKS/App Service deployment, GitHub Actions deploy pipeline, HTTPS/SSL, Kubernetes ingress, rate limiting, structured JSON logging, LangSmith dashboard, Locust load testing, OWASP security audit, admin dashboard, notification service |

**Total: 6 phases · 15 weeks · 78 tasks**

### Phase 1 — Foundation & Infrastructure *(Weeks 1–3)*

Sets up the entire development environment and baseline services. Nothing in later phases can proceed without this foundation.

Key deliverables: monorepo structure, GitHub Actions CI (ruff + black + pytest), Azure PostgreSQL and Redis, Docker Compose for local dev, Azure Blob Storage and AI Search provisioned with 8 index namespaces, LangSmith project, SQLAlchemy models (`User`, `Resume`, `JD`, `Session`), Alembic migration, JWT auth endpoints, Pydantic schemas, React + Vite + Tailwind scaffold, Axios API client with auth interceptors, Login/Register UI.

### Phase 2 — RAG Pipeline & Vector Store *(Weeks 4–5)*

Builds the knowledge retrieval backbone used by every downstream agent.

Key deliverables: `rag/embeddings.py` (text-embedding-3-large), `rag/vector_store.py` (Azure AI Search wrapper), `rag/retriever.py` (similarity search), `rag/chunking.py` (RecursiveCharacterTextSplitter), seeding of all 8 collections with 50+ interview experiences, learning resources, DSA notes, STAR templates, and company-specific interview patterns. Retrieval quality evaluation at precision@5.

### Phase 3 — Candidate Flow: Core Agents *(Weeks 6–8)*

Implements the full candidate analysis pipeline and its frontend dashboard.

Key deliverables: `ResumeParserAgent` (GPT-4o structured JSON), PDF extraction via pypdf, `POST /api/candidate/resume/upload`, `ATSAgent`, `SkillGapAgent`, `RoadmapAgent`, `CandidateGraph` wiring parse→ATS→gap→roadmap in LangGraph, `TalentHubState`, drag-and-drop resume upload UI, ATS score gauge dashboard, Recharts skill gap bar chart, weekly learning plan cards.

### Phase 4 — Interview Coach Module *(Weeks 9–10)*

Adds the conversational AI mock interviewer with memory and evaluation.

Key deliverables: `InterviewAgent` (session start + adaptive follow-up), `MemoryAgent` (LangChain + Redis chat history), `EvaluationAgent` (scores: technical, communication, problem-solving, culture fit), `InterviewService`, interview endpoints (start / respond / feedback), real-time interview chat UI with message bubbles, company/role selector dropdown, feedback radar chart.

### Phase 5 — Recruiter Flow *(Weeks 11–12)*

Builds the employer-side pipeline for ranking and recommending candidates.

Key deliverables: `JDAnalyzerAgent`, `MatchingAgent`, `RankingAgent`, `QuestionGenerationAgent`, `HiringRecommendationAgent` (HIRE / CONSIDER / REJECT + confidence %), `RecruiterGraph` (JD→match→rank), `POST /api/recruiter/jd/upload`, `POST /jd/:id/rank-candidates`, JD PDF upload UI, candidate rankings table with score breakdown, per-candidate interview question list, hiring recommendation card with badge and reasoning.

### Phase 6 — Cloud Deployment & Production Hardening *(Weeks 13–15)*

Takes the platform to production on Azure with full observability, security, and load testing.

Key deliverables: Azure Container Registry, AKS deployment (or App Service), Azure Static Web Apps for frontend, `deploy.yml` GitHub Actions workflow, HTTPS + SSL cert, Kubernetes ingress (nginx), rate limiting middleware, structured JSON logging → Azure Monitor, LangSmith dashboard for token cost and latency tracking, Locust load test (100 concurrent users), OWASP Top 10 security audit, E2E tests on staging, admin analytics dashboard, email notification service, final production launch checklist.

---

## Testing

```bash
cd backend

# Unit tests (fast, mocked LLM responses)
pytest tests/unit -v -m unit

# Integration tests (requires live DB + Redis)
pytest tests/integration -v -m integration

# Full suite with coverage report
pytest tests/ --cov=app --cov-report=html

# Coverage gate (CI enforces ≥70%)
pytest tests/ --cov=app --cov-fail-under=70
```

**Frontend tests:**

```bash
cd frontend
npm test          # vitest run (single pass)
npm run test:watch  # watch mode
```

### Test Structure

```
backend/tests/
├── unit/
│   ├── api/
│   │   └── test_auth.py               # Auth service unit tests
│   └── rag/
│       └── test_retriever.py          # RAGRetriever unit tests
└── integration/
    ├── test_candidate_flow.py         # Full candidate graph pipeline
    └── test_recruiter_pipeline.py     # Full recruiter graph pipeline
```

---

## CI/CD

### CI Pipeline (`.github/workflows/ci.yml`)

Triggered on every push to `main` / `develop` and all PRs to `main`.

```
lint  ──┐
         ├──→  build (Docker image)
test  ──┘
```

- **lint:** `ruff check` + `black --check` on `backend/app/`
- **test:** Spins up PostgreSQL 16 + Redis 7 service containers, runs unit and integration tests
- **build:** `docker build` to verify the image builds cleanly

### Deploy Pipeline (`.github/workflows/deploy.yml`)

Triggered on push to `main` after CI passes.

1. Build Docker image tagged with `github.sha`
2. Push to Azure Container Registry (ACR)
3. Deploy to Azure App Service or AKS

---

## Deployment

### Azure Infrastructure

| Service | Azure Resource |
|---|---|
| Backend API | Azure App Service (or AKS) |
| Frontend | Azure Static Web Apps |
| PostgreSQL | Azure Database for PostgreSQL – Flexible Server |
| Redis | Azure Cache for Redis |
| File Storage | Azure Blob Storage |
| Vector DB | Azure AI Search |
| Container Registry | Azure Container Registry (ACR) |
| Observability | Azure Monitor + Log Analytics + LangSmith |

```bash
# Local quality checks before pushing
cd backend
ruff check app/
black app/
pytest tests/unit -v
```