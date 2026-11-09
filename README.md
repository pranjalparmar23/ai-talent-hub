# AI Talent Hub

An AI platform that helps people get hired and helps companies hire.

- **Candidates** upload a resume, see how well it fits a job, get a learning plan, and practise with an AI interviewer.
- **Recruiters** upload a job description, see candidates ranked by fit, and get interview questions and a hiring recommendation.

---

## What you can do

### As a candidate

- **Upload your resume** (PDF) and have it read automatically
- **Get an ATS score** showing how well your resume matches a job description
- **See your skill gaps** compared with a job
- **Get a weekly learning plan** to close those gaps
- **Practise a mock interview** for a company and role, then get feedback on your answers

### As a recruiter

- **Upload a job description** (PDF or text)
- **Rank candidates** by how well they match the job
- **Get interview questions** tailored to each candidate
- **Get a hiring recommendation**: HIRE, CONSIDER or REJECT, with the reasons

The scores and recommendations help a person decide. They don't make the decision.

---

## Built with

- **Backend:** Python, FastAPI
- **AI:** Groq (free API key) and LangGraph
- **Data:** PostgreSQL, Redis, ChromaDB
- **Frontend:** React, TypeScript, Tailwind
- **Runs with:** Docker Compose

---

## Run it

You need Docker, Python 3.12, Node.js 20, and a free API key from [console.groq.com](https://console.groq.com).

### 1. Get the code

```bash
git clone https://github.com/pranjalparmar23/ai-talent-hub.git
cd ai-talent-hub
```

### 2. Set up your settings (`.env`)

The app reads its settings from a file called `backend/.env`. The repo includes a template, `backend/.env.example`, with safe defaults. Follow these steps:

1. **Copy the template:**
   ```bash
   cp backend/.env.example backend/.env
   ```
2. **Open `backend/.env`** in any text editor.
3. **Set `SECRET_KEY`.** This signs login tokens. Generate a random value:
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
   Paste the output after `SECRET_KEY=`.
4. **Set `GROQ_API_KEY`.** Create a free key in the [Groq console](https://console.groq.com) and paste it after `GROQ_API_KEY=`.
5. **Leave everything else as it is.** The database, Redis, ChromaDB and file settings already match the Docker setup.
6. **Save the file.**

`backend/.env` stays on your machine. Never commit it or share it.

Settings you can skip for now:

| Setting | When you need it |
|---|---|
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` | Only if you want to track AI calls in LangSmith. It is off by default. See [Tracing](docs/architecture/tracing.md) |
| `MCP_REGISTRY_PATH`, `MCP_DEMO_URL` | Only when working with the MCP tool servers |
| `ALLOWED_ORIGINS` | Only if your frontend runs on a different address than http://localhost:5173 |

### 3. Start the backend and databases

```bash
docker compose -f docker/docker-compose.yml up --build
```

Once it's running:
- The API is at http://localhost:8000
- The interactive API docs are at http://localhost:8000/api/docs

Then run this once to set up the search database:

```bash
docker compose -f docker/docker-compose.yml exec backend python scripts/init_chroma.py
```

### 4. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

---

## Run the tests

The tests need a separate test database. Create it once:

```bash
docker exec -it ath_postgres psql -U postgres -c "CREATE DATABASE talent_hub_test;"
```

Then, from the `backend` folder:

```bash
black --check .
ruff check .
pytest tests/unit
```

GitHub runs these checks automatically on every pull request.

---

## Folder tree

```
ai-talent-hub/
├── .claude/skills/          Helper skills used while developing
├── .github/workflows/       ci.yml: the automatic checks on every pull request
│
├── backend/
│   ├── app/
│   │   ├── main.py          Starts the app, sets up middleware and routes
│   │   ├── api/             The endpoints: auth, candidate, recruiter, admin, health
│   │   ├── services/        The business logic behind each endpoint
│   │   ├── agents/          The AI agents
│   │   │   ├── candidate/   Resume parsing, ATS score, skill gap, learning plan, interviewer
│   │   │   ├── recruiter/   JD analysis, matching, ranking, questions, recommendation
│   │   │   └── shared/      Chat memory, interview scoring, search helper
│   │   ├── graphs/          LangGraph workflows for the candidate and recruiter flows
│   │   ├── llm/             The Groq connection and the prompt templates
│   │   ├── rag/             Embeddings and search over the ChromaDB collections
│   │   ├── mcp/             Safe gateway to outside tool servers
│   │   ├── observability/   LangSmith tracing rules
│   │   ├── middleware/      Login check, rate limit, request logging
│   │   ├── database/        Database tables, data shapes, Postgres and Redis connections
│   │   └── utils/           Small shared helpers
│   ├── alembic/             Database migrations
│   ├── scripts/             Setup and evaluation scripts
│   ├── tests/               unit/ and integration/ tests
│   ├── .env.example         Settings template (copy to .env)
│   └── requirements.txt     Python packages
│
├── frontend/                The React app, with its dev server and styling setup
│   └── src/
│       ├── auth/            Login and registration pages
│       ├── candidate/       Candidate pages: dashboard, resume upload, analysis, mock interview and feedback
│       ├── recruiter/       Recruiter pages: dashboard, candidate rankings, candidate detail
│       ├── shared/          Shared parts, such as the login guard and error handling
│       └── services/        The client that talks to the backend API
│
├── data/
│   ├── eval/                Test questions used to check search quality
│   └── uploads/             Uploaded resumes (stored locally)
│
├── docker/
│   ├── Dockerfile           How the backend image is built
│   ├── docker-compose.yml   Starts Postgres, Redis, ChromaDB and the backend
│   └── mcp-demo/            A small demo tool server for testing
│
├── docs/
│   ├── api/                 api-reference.md
│   ├── architecture/        mcp.md, skills.md, tracing.md, mcp-server-evaluation.md
│   ├── mcp/                 vetting-checklist.md, vetting-log.md
│   └── security/            skills-safety-checklist.md
│
├── LICENSE                  MIT license
└── README.md                This file
```

---

## Learn more

| Read this | To learn about |
|---|---|
| [API reference](docs/api/api-reference.md) | Every endpoint and what it does |
| [MCP gateway](docs/architecture/mcp.md) | How the app connects to outside tools safely |
| [Tracing](docs/architecture/tracing.md) | How AI calls are tracked with LangSmith |
| [Skills](docs/architecture/skills.md) | The helper skills used while developing |

All documents are in the [`docs`](docs) folder.

---

## Roadmap

| Phase | What | Status |
|---|---|---|
| 1 | Foundation and infrastructure | Done |
| 2 | RAG pipeline and vector store | Done |
| 3 | Candidate flow: core agents | Done |
| 4 | Interview coach module | Done |
| 5 | Recruiter flow | Done |
| 6 | Skills and MCP foundation | Done |
| 7 | Better knowledge for the AI, using outside sources | Planned |
| 8 | Voice interviews | Planned |
| 9 | Interviews and learning for all kinds of roles | Planned |
| 10 | Testing the quality of AI answers | Planned |
| 11 | Security hardening | Planned |
| 12 | Monitoring and alerts | Planned |
| 13 | Interface polish | Planned |
| 14 | Putting it online | Planned |

---

## License

Released under the [MIT License](LICENSE).