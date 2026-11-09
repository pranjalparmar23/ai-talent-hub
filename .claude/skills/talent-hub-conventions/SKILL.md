---
name: talent-hub-conventions
description: Conventions for the AI Talent Hub repo — backend layering (routes, services, agents, graphs, prompts), LLM and JSON-parsing rules, MCP gateway use, test and lint workflow, frontend layout and commit style. Use when writing, changing, testing or reviewing code in this repository.
---

# AI Talent Hub conventions

Follow what the code already does. When this file and the code disagree, the code wins; say so and propose updating this file.

## Stack

FastAPI + SQLAlchemy async + Alembic + PostgreSQL 16 + Redis 7. LangGraph + Groq (`openai/gpt-oss-120b`) + ChromaDB. React 18 + Vite + TypeScript + Tailwind. Backend Python 3.12.

## Backend layering (`backend/app/`)

Keep each layer thin and in its own place:

| Layer | Location | Rule |
|---|---|---|
| Routes | `api/*_routes.py` | Thin: validate input, call a service or graph, return a typed response. No DB queries or LLM calls in handlers. |
| Services | `services/*_service.py` | Business logic, DB access (`AsyncSessionLocal`), `HTTPException` for client errors. |
| Agents | `agents/{candidate,recruiter,shared}/` | One class per job. Own an LLM chain. Never raise on LLM failure (see below). |
| Graphs | `graphs/` | LangGraph orchestration. State is the `TalentHubState` TypedDict in `graph_state.py`. |
| Prompts | `llm/prompts/*_prompts.py` | One module per agent. Constants, not inline strings. |
| Schemas | `database/schemas.py` | Pydantic v2 request and response models. |
| DB models | `database/models.py` | SQLAlchemy models. UUID primary keys, `created_at` with `server_default=func.now()`. |
| Middleware | `middleware/` | Auth, logging, rate limiting. |
| RAG | `rag/` | Chunking, embeddings, vector store, retriever. |
| MCP | `mcp/` | The gateway. See "MCP" below. |

Details that are easy to get wrong:

- Routes get services with `Depends(ServiceClass)`, so a service's `__init__` must take **no arguments**.
- Auth: `Depends(get_current_user)` returns a dict with `id`, `email`, `role`. Role checks use `require_role("...")`.
- Owner checks return **404, not 403**, so existence is never leaked.
- A schema change needs an **Alembic revision** (`backend/alembic/versions/`). `init_db()` only creates missing tables for dev convenience.
- A new router goes in `app/main.py` with `app.include_router(..., prefix="/api/<name>", tags=["<name>"])`.

## Configuration and secrets

- Use `os.getenv(...)`. Do not introduce pydantic-settings.
- Local settings live in `backend/.env` (git-ignored). Every new variable gets a line in `backend/.env.example`, with a harmless placeholder.
- **Never** hard-code secrets or give secrets a real-looking default. Never commit `.env`.
- Every dependency in `backend/requirements.txt` is pinned with `==`. Add new ones pinned, and check the whole file still resolves.

## LLM and agents

- **Always** get a model from `get_llm()` in `app/llm/models.py`. Never construct a Groq client elsewhere. The model name comes from `GROQ_MODEL`, with `DEFAULT_MODEL` as the fallback.
- Temperature: `0` for parsing and scoring, `0.3–0.7` only for generative work. Set `max_tokens` explicitly for large structured outputs.
- Agent shape: `self.chain = ChatPromptTemplate.from_template(PROMPT) | get_llm(...)`, then `await self.chain.ainvoke({...})` and read `response.content`.
- **Agents never raise on LLM or parse failure.** They return a fallback dict with the real schema's keys plus `_parse_error: True` (and `_error_reason`), so pipelines keep moving.
- Parse model output with `parse_llm_json` (`app/utils/json_parser.py`). Do not call `json.loads` on raw model text.
- Validate and clamp what the model returns (score ranges, list fields that came back as strings).
- Trim inputs to what the prompt needs and cap size (resumes are capped at 50k characters).
- Prompt templates go through `ChatPromptTemplate`, so literal JSON braces are doubled (`{{ }}`). Prompts use explicit rubrics and a worked example.
- Groq retires models every few months. A `404 model_not_found` means update `DEFAULT_MODEL` and `GROQ_MODEL`; the single factory means one change fixes every agent.

## MCP (external reference data)

- Application code uses only `get_gateway()` from `app.mcp.gateway`. Never open MCP sessions or call external servers any other way.
- Always check `result.ok`. A failed call must degrade to "no reference data", never to an error for the user.
- Put server text in a prompt only through `result.as_prompt_context()`.
- New servers are added to `app/mcp/mcp_servers.yaml` with `enabled: false`, after vetting. Full rules are in `docs/architecture/mcp.md`.

## Data and privacy

- Do not log resume text, JD text, transcripts or MCP argument values. Log shapes, ids and counts.
- Uploads: PDF only, 5 MB cap, UUID-named files, stored under `UPLOAD_DIR`.

## Tests (`backend/tests/`)

- Unit tests mirror the app: `tests/unit/{agents,api,graphs,mcp,rag,services}/`. Each test folder needs an `__init__.py`.
- `pytest.ini` sets `asyncio_mode = auto`: write `async def test_...` with no asyncio marker.
- **Unit tests never call Groq or any network.** For agents, replace the chain wholesale with the fakes in `tests/unit/agents/_fakes.py`: `agent.chain = FakeChain(response=FakeMsg('{"x": 1}'))` (or `FakeChain(exc=Exception("..."))`). Patching `chain.ainvoke` does not work, because LangChain runnables are Pydantic models.
- API tests use the `client` fixture from `tests/conftest.py` (httpx `AsyncClient` on the real `app`).
- Test the failure paths too: fallbacks, bad model output, empty input, other users' resources returning 404.
- Run from `backend/`. A single file needs `--no-cov` (the repo enforces a 60% coverage floor on the whole run). Postgres must be running, because an autouse fixture creates the tables:
  `pytest tests/unit/agents/test_ats_agent.py --no-cov -v`
- `tests/integration/` needs a real database and is not part of CI. Add integration tests for flows that cross layers.

## Lint and format (CI blocks on both)

From `backend/`, **before every commit**:

```bash
black .
ruff check .
```

- Versions are pinned (black 24.8.0, ruff 0.6.9) and there is no config file, so defaults apply.
- Files must end with a newline. Black fixes this, so run it after pasting or generating files.
- Do not use names newer than Python 3.8 in code ruff checks (for example `BaseExceptionGroup`).

## Frontend (`frontend/src/`)

- Folders by role: `auth/`, `candidate/`, `recruiter/`, `shared/`, `services/`. API calls go through `services/api.ts` (axios), and its types mirror the backend Pydantic schemas.
- State and data: `@tanstack/react-query` for server data, `zustand` for client state, `react-hot-toast` for messages, Tailwind for styling.
- Before pushing, from `frontend/`: `npx tsc --noEmit` and `npm run lint`. Tests use vitest (`npm test`).

## Git

- Branches are `feature/phase<N>` (or `feature/<topic>`), merged to `main` by pull request.
- Conventional commits: `feat(scope):`, `fix(scope):`, `test(scope):`, `docs(scope):`, `chore(scope):`. Subject under about 72 characters; the body says **why**.
- Keep a commit to one purpose, and keep tests and the code they cover together.
- Never commit `.env`, API keys, uploaded files or `venv/`.

## Docs

- Architecture notes and decisions go in `docs/architecture/`, security material in `docs/security/`.
- The phase tracker is the source of truth for what is planned. Update it when a task is done.