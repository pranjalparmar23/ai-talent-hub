# AI Talent Hub

An AI-powered recruitment and career development platform built with FastAPI, LangGraph, LangChain, and React.

## Architecture

- **Backend**: FastAPI + LangGraph multi-agent system
- **Frontend**: React + TypeScript + Tailwind CSS
- **Database**: PostgreSQL (Azure) + Redis (sessions)
- **Vector DB**: Azure AI Search
- **Storage**: Azure Blob Storage
- **LLM**: OpenAI GPT-4o / Claude via LangChain
- **Monitoring**: LangSmith
- **CI/CD**: GitHub Actions → Azure

## Quick Start

```bash
# Clone and setup
cp backend/.env.example backend/.env
# Fill in your API keys in backend/.env

# Run with Docker
docker-compose -f docker/docker-compose.yml up

# Backend only (dev)
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend (dev)
cd frontend && npm install && npm run dev
```

## Project Phases

See `docs/architecture/PHASES.md` for full phase breakdown.

## Testing

```bash
cd backend
pytest tests/unit -v           # Unit tests
pytest tests/integration -v    # Integration tests
pytest tests/ --cov=app        # With coverage
```

## API Docs

Available at `http://localhost:8000/api/docs` when running locally.
