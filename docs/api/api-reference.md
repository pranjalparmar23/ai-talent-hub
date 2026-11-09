# API Reference

Reference for the AI Talent Hub backend (FastAPI). It lists every HTTP endpoint,
who may call it, what it accepts and what it returns.

> **Keep this file current.** Any change that adds, removes or alters an endpoint
> (path, method, role, request body, response shape, status code or error message)
> must update this file **in the same commit**. See [Updating this file](#updating-this-file).

Last reviewed: 2026-11-09 (Phase 6)

---

## Contents

1. [Basics](#1-basics)
2. [Authentication](#2-authentication)
3. [Errors and limits](#3-errors-and-limits)
4. [System endpoints](#4-system-endpoints)
5. [Auth endpoints](#5-auth-endpoints)
6. [Candidate endpoints](#6-candidate-endpoints)
7. [Recruiter endpoints](#7-recruiter-endpoints)
8. [Admin endpoints (stubs)](#8-admin-endpoints-stubs)
9. [Health endpoints](#9-health-endpoints)
10. [Endpoint index](#10-endpoint-index)
11. [Changelog](#11-changelog)
12. [Updating this file](#updating-this-file)

---

## 1. Basics

| Item | Value |
|---|---|
| App | AI Talent Hub, version `1.0.0` |
| Local base URL | `http://localhost:8000` |
| Interactive docs (Swagger) | `/api/docs` |
| ReDoc | `/api/redoc` |
| OpenAPI JSON | `/openapi.json` |
| Content type | JSON, except the login form and file uploads |
| IDs | UUID strings |

The interactive docs are generated from the code and are always the source of truth for
exact schemas. This file explains behaviour the schema cannot show: roles, ownership,
error messages and limits.

**Route prefixes**

| Prefix | Area | Who can call it |
|---|---|---|
| `/api/auth` | Registration, login, tokens | Anyone (public) |
| `/api/candidate` | Resumes, JDs, scoring, learning plan, mock interviews | Any logged-in user |
| `/api/recruiter` | JD upload, candidate ranking, questions, recommendation | Role `recruiter` |
| `/api/admin` | User management, analytics (stubs) | Role `admin` |
| `/api/health` | Service health | Public |

**Middleware (applies to every request)**

- **CORS:** allowed origins come from `ALLOWED_ORIGINS` (default
  `http://localhost:5173,http://localhost:3000`). Credentials are allowed.
- **Rate limit:** 100 requests per 60 seconds per client IP, counted in memory per
  process. Over the limit returns `429 {"detail": "Rate limit exceeded"}`.
- **Request logging:** method, path, status and duration are written to the
  `ai_talent_hub` logger. Request bodies are not logged.

---

## 2. Authentication

Protected endpoints need a JWT in the header:

```
Authorization: Bearer <access_token>
```

**Flow**

1. `POST /api/auth/register` creates a user with a role.
2. `POST /api/auth/login` returns an `access_token` and a `refresh_token`.
3. Send the access token on every protected call.
4. When it expires, call `POST /api/auth/refresh` with the refresh token.

**Tokens**

| Item | Value |
|---|---|
| Algorithm | HS256 (`ALGORITHM` env) |
| Claims | `sub` (user id), `exp` |
| Access token lifetime | `ACCESS_TOKEN_EXPIRE_MINUTES`, default 30 |
| Refresh token lifetime | `REFRESH_TOKEN_EXPIRE_DAYS`, default 7 |
| Password hashing | bcrypt |

**Roles:** `candidate`, `recruiter`, `admin`.

- A missing, invalid or expired token returns `401 {"detail": "Invalid or expired token"}`
  with a `WWW-Authenticate: Bearer` header.
- A valid token with the wrong role returns `403 {"detail": "Requires role: <role>"}`.
- Candidate endpoints only check that you are logged in. They scope every query to your
  own user id.

**Known limitation:** logout is stateless. Tokens are not revoked and stay valid until
they expire. Token revocation and secret handling are planned for the security phase
(Phase 11).

---

## 3. Errors and limits

**Error format.** Errors use FastAPI's standard shape:

```json
{ "detail": "Resume not found" }
```

Request validation failures return `422` with `detail` as a list of field errors:

```json
{ "detail": [{ "loc": ["body", "email"], "msg": "value is not a valid email address", "type": "value_error" }] }
```

**Status codes used**

| Code | Meaning here |
|---|---|
| 200 / 201 | Success / created |
| 400 | Bad input (empty file, wrong file type on candidate upload, empty text, nothing to score yet) |
| 401 | Missing or invalid token, or bad login |
| 403 | Wrong role |
| 404 | Not found, **or not yours** (see below) |
| 409 | Conflict (email already registered, interview already complete) |
| 413 | Upload too large |
| 415 | Unsupported file type (recruiter JD upload) |
| 422 | Validation error, or a file with too little readable text |
| 429 | Rate limit exceeded |

**Ownership returns 404, not 403.** If a resource exists but belongs to someone else, the
API answers `404`, so one user cannot tell whether another user's id exists. An invalid
(non-UUID) id also returns `404`.

**Limits**

| Limit | Value | Where |
|---|---|---|
| Resume upload size | 5 MB (PDF only) | `POST /api/candidate/resume/upload` |
| JD upload size | 5 MB (PDF, `.txt`, `.md`) | `POST /api/recruiter/jd/upload` |
| Minimum text in an uploaded JD | 50 characters | `POST /api/recruiter/jd/upload` |
| Interview length | 6 questions | Mock interview |
| Candidate pool scanned when ranking | newest 500 candidates | `rank-candidates` |
| Rate limit | 100 requests / 60 s / IP | All routes |

---

## 4. System endpoints

### `GET /`

Public. Returns a pointer to the docs.

```json
{ "message": "AI Talent Hub API", "docs": "/api/docs" }
```

### `GET /health`

Public. Liveness check for the app itself. Does not touch the database or any
external service. For MCP status see [`GET /api/health/mcp`](#get-apihealthmcp).

```json
{ "status": "healthy", "version": "1.0.0" }
```

---

## 5. Auth endpoints

Prefix `/api/auth`. All public.

### `POST /api/auth/register`

Create a user. Status `201`.

Body (JSON):

| Field | Type | Rules |
|---|---|---|
| `email` | string | Valid email |
| `password` | string | At least 8 characters |
| `role` | string | `candidate`, `recruiter` or `admin`. Default `candidate` |

Response:

```json
{ "message": "User created", "id": "6f1c0c8e-..." }
```

Errors: `409 Email already registered`, `422` validation.

> Note: the endpoint accepts `admin` as a role from a public request. Restricting this is
> a Phase 11 item.

### `POST /api/auth/login`

Log in. The body is **form-encoded** (`application/x-www-form-urlencoded`), not JSON, to
match the OAuth2 password flow. Put the **email in the `username` field**.

| Field | Value |
|---|---|
| `username` | The user's email |
| `password` | The password |

Response:

```json
{
  "access_token": "<jwt>",
  "refresh_token": "<jwt>",
  "token_type": "bearer",
  "role": "candidate"
}
```

Errors: `401 Invalid credentials`.

### `POST /api/auth/refresh`

Exchange a refresh token for a new token pair.

Body (JSON): `{ "refresh_token": "<jwt>" }`

Response: the same shape as login, but `role` is not included.

Errors: `401 Invalid refresh token` or `401 Invalid or expired refresh token`.

### `POST /api/auth/logout`

Body (JSON): `{ "token": "<jwt>" }`

Response: `{ "message": "Logged out" }`

Stateless: the server does not revoke the token. The client must discard it.

---

## 6. Candidate endpoints

Prefix `/api/candidate`. Requires a valid token (any role). Everything is scoped to the
caller. Other users' resources return `404`.

### Resumes

#### `POST /api/candidate/resume/upload`

Upload and parse a resume. Status `201`. Body: `multipart/form-data`, a PDF file.

Response (`ResumeUploadResponse`):

| Field | Type | Notes |
|---|---|---|
| `resume_id` | string | |
| `parsed_data` | object | Structured resume (name, skills, experience, education, ...) |
| `file_path` | string | Stored file location on the server |
| `parse_error` | string or null | Set when the AI parse failed and a fallback was stored |

Errors:

| Code | Message |
|---|---|
| 400 | `Only PDF files are accepted` |
| 400 | `Uploaded file is empty` |
| 400 | `Could not extract text from PDF. Is it a scanned image? ...` |
| 400 | `Could not read PDF: ...` |
| 413 | `File exceeds N MB limit` (5 MB) |

#### `GET /api/candidate/resumes`

List your resumes. Returns `ResumeSummary[]`:

```json
[{ "resume_id": "...", "name": "Jane Doe", "ats_score": 78, "created_at": "2026-11-09T10:00:00Z" }]
```

#### `GET /api/candidate/resume/{resume_id}`

One resume. Returns `ResumeDetail`: `resume_id`, `parsed_data`, `file_path`,
`ats_score`, `created_at`. Error: `404 Resume not found`.

### Job descriptions (candidate side)

#### `POST /api/candidate/jd`

Save a job description you want to apply for. Status `201`.

Body (JSON):

| Field | Rules |
|---|---|
| `title` | 1 to 200 characters |
| `raw_text` | At least 20 characters |

Response (`JDUploadResponse`): `jd_id`, `parsed_data`, `parse_error`.

Errors: `400 JD text cannot be empty`, `422` validation.

#### `GET /api/candidate/jds`

List your saved JDs. Returns `JDSummary[]`: `jd_id`, `title`, `created_at`.

### Analysis (resume vs JD)

All three take the same query parameters and need a resume and a JD that both belong to
you.

| Parameter | Type |
|---|---|
| `resume_id` | string (UUID) |
| `jd_id` | string (UUID) |

#### `GET /api/candidate/ats-score`

Response (`ATSScoreResponse`):

| Field | Type |
|---|---|
| `resume_id`, `jd_id` | string |
| `ats_score` | number, 0 to 100 |
| `missing_keywords` | string[] |
| `matching_keywords` | string[] |
| `formatting_issues` | string[] |
| `recommendations` | string[] |
| `summary` | string |

#### `GET /api/candidate/skill-gap`

Response (`SkillGapResponse`): `resume_id`, `jd_id`, `missing_skills`,
`matching_skills`, `gap_percentage` (0 to 100).

#### `GET /api/candidate/learning-plan`

Response (`LearningPlanResponse`):

```json
{
  "resume_id": "...",
  "jd_id": "...",
  "target_role": "Data Analyst",
  "weeks": [
    {
      "week": 1,
      "topic": "SQL joins",
      "tasks": ["..."],
      "resources": [{ "title": "...", "url": "https://...", "type": "article" }],
      "estimated_hours": 6
    }
  ],
  "summary": "..."
}
```

### Mock interview

A session has at most 6 questions. The flow is: start, then respond repeatedly, then
fetch feedback.

#### `POST /api/candidate/interview/start`

Body (JSON): `company` and `role`, each 1 to 100 characters and not blank.

Response (`InterviewStartResponse`):

```json
{ "session_id": "...", "question": "Tell me about ...", "question_number": 1, "max_questions": 6 }
```

#### `GET /api/candidate/interview/{session_id}`

Full session state (`InterviewSessionResponse`): `session_id`, `company`, `role`,
`messages` (each `{role: "interviewer" | "candidate", content}`), `question_number`,
`max_questions`, `session_complete`, `feedback_ready`.

Error: `404 Interview session not found.` (also for another user's session or an
invalid id).

#### `POST /api/candidate/interview/{session_id}/respond`

Body (JSON): `{ "response": "<answer>" }`, 1 to 4000 characters.

Response (`InterviewRespondResponse`): `question` (the next question), `session_complete`,
`question_number`, `max_questions`.

Errors:

| Code | Message |
|---|---|
| 400 | `Response must not be empty.` |
| 404 | `Interview session not found.` |
| 409 | `This interview is already complete — view your feedback.` |

#### `GET /api/candidate/interview/{session_id}/feedback`

Response (`InterviewFeedbackResponse`): `scores` (object), `overall_score`,
`strengths`, `improvements`, `summary`, `parse_error`.

Feedback is generated once and cached on the session. Later calls return the same
result.

Errors: `400 Answer at least one question before requesting feedback.`,
`404 Interview session not found.`

---

## 7. Recruiter endpoints

Prefix `/api/recruiter`. Requires role `recruiter`. A recruiter only sees their own JDs.
Another recruiter's JD returns `404 JD not found`.

#### `POST /api/recruiter/jd/upload`

Upload a JD file. Status `201`. Body: `multipart/form-data`. Accepts PDF, `.txt` or `.md`
(the 415 message mentions only PDF and `.txt`).

Response (`JDUploadResponse`): `jd_id`, `parsed_data`, `parse_error`. The title is taken
from the analysed text.

Errors:

| Code | Message |
|---|---|
| 400 | `The uploaded file is empty` |
| 413 | `File is too large (limit N MB)` (5 MB) |
| 415 | `Unsupported file type. Upload a PDF or a .txt file.` |
| 422 | `Could not find enough text in this file. If it is a scanned PDF, upload a text-based copy instead.` (fewer than 50 characters) |
| 422 | `Could not read this PDF. It may be corrupted or password protected.` |

#### `GET /api/recruiter/jds`

List your JDs. Returns `JDSummary[]`: `jd_id`, `title`, `created_at`.

#### `POST /api/recruiter/jd/{jd_id}/rank-candidates`

Score and rank the candidate pool against a JD. The pool is the latest parsed resume of
each candidate user (newest 500). Users with no parsed resume are skipped.

Body (JSON, optional): `{ "top_n": 10 }`, where `top_n` is 1 to 100.

Response (`CandidateRankingResponse`):

```json
{
  "jd_id": "...",
  "jd_title": "Backend Engineer",
  "total_candidates": 42,
  "ranked_candidates": [
    {
      "rank": 1,
      "candidate_id": "...",
      "resume_id": "...",
      "name": "Jane Doe",
      "match_score": 84.5,
      "skill_match": 0.9,
      "experience_match": 0.8,
      "semantic_match": 0.85,
      "semantic_available": true,
      "matching_skills": ["python"],
      "missing_skills": ["kubernetes"],
      "matching_preferred": ["docker"],
      "summary": "...",
      "interview_score": 7.5
    }
  ]
}
```

`interview_score` (0 to 10) is the candidate's latest usable mock-interview score and is
`null` when they have none. `semantic_available` is `false` when the vector search was
unavailable and the score fell back to skills and experience only.

Errors: `404 JD not found`, `422 This JD could not be analysed. Please upload it again.`

#### `GET /api/recruiter/jd/{jd_id}/questions/{candidate_id}`

Interview questions tailored to one candidate and this JD.

Response (`RecruiterQuestionsResponse`): `jd_id`, `candidate_id`, `questions`
(each `{question, category, difficulty, rationale}`), `parse_error`.

Errors: `404 JD not found`, `404 Candidate not found`,
`422 This JD could not be analysed. Please upload it again.`

#### `GET /api/recruiter/jd/{jd_id}/recommendation/{candidate_id}`

A hiring recommendation for one candidate against this JD.

Response (`HiringRecommendationResponse`):

| Field | Type |
|---|---|
| `jd_id`, `candidate_id` | string |
| `recommendation` | `HIRE`, `CONSIDER` or `REJECT` |
| `confidence` | number, 0 to 100 |
| `combined_score` | number |
| `match_score` | number |
| `interview_score` | number or null |
| `flags` | string[] |
| `reasoning` | string |
| `strengths` | string[] |
| `risks` | string[] |
| `reasoning_source` | `llm` or `template` |

`reasoning_source` is `template` when the model was unavailable and a rule-based
explanation was used. The recommendation is decision support for a human, not an
automated decision.

Errors: `404 JD not found`, `404 Candidate not found`,
`422 This JD could not be analysed. Please upload it again.`

---

## 8. Admin endpoints (stubs)

Prefix `/api/admin`. Requires role `admin`.

> **These three endpoints are placeholders.** They return fixed values and do not read or
> change any data. Do not build features on them until they are implemented.

| Method and path | Current behaviour |
|---|---|
| `GET /api/admin/users` | Returns `{"users": []}` |
| `GET /api/admin/analytics` | Returns `{"analytics": {}}` |
| `DELETE /api/admin/users/{user_id}` | Returns `{"message": "User <id> deleted"}`. **Nothing is deleted.** |

---

## 9. Health endpoints

### `GET /api/health/mcp`

Public. Status of the MCP gateway and its configured servers. Always returns HTTP `200`;
read the `status` field instead. The result is cached for 10 seconds. It never includes
URLs, keys or tool results.

Response:

```json
{
  "status": "ok",
  "servers": {
    "fetch": { "status": "ok", "latency_ms": 120, "...": "per-server counts" }
  }
}
```

`status` values:

| Value | Meaning |
|---|---|
| `ok` | All enabled servers are healthy |
| `degraded` | At least one enabled server is failing |
| `no_servers_enabled` | The registry has no enabled servers |
| `misconfigured` | The registry file could not be loaded or is invalid |

See `docs/architecture/mcp.md` for the gateway design.

---

## 10. Endpoint index

| Method | Path | Auth | Section |
|---|---|---|---|
| GET | `/` | Public | [4](#4-system-endpoints) |
| GET | `/health` | Public | [4](#4-system-endpoints) |
| POST | `/api/auth/register` | Public | [5](#5-auth-endpoints) |
| POST | `/api/auth/login` | Public | [5](#5-auth-endpoints) |
| POST | `/api/auth/refresh` | Public | [5](#5-auth-endpoints) |
| POST | `/api/auth/logout` | Public | [5](#5-auth-endpoints) |
| POST | `/api/candidate/resume/upload` | Logged in | [6](#6-candidate-endpoints) |
| GET | `/api/candidate/resumes` | Logged in | [6](#6-candidate-endpoints) |
| GET | `/api/candidate/resume/{resume_id}` | Logged in | [6](#6-candidate-endpoints) |
| POST | `/api/candidate/jd` | Logged in | [6](#6-candidate-endpoints) |
| GET | `/api/candidate/jds` | Logged in | [6](#6-candidate-endpoints) |
| GET | `/api/candidate/ats-score` | Logged in | [6](#6-candidate-endpoints) |
| GET | `/api/candidate/skill-gap` | Logged in | [6](#6-candidate-endpoints) |
| GET | `/api/candidate/learning-plan` | Logged in | [6](#6-candidate-endpoints) |
| POST | `/api/candidate/interview/start` | Logged in | [6](#6-candidate-endpoints) |
| GET | `/api/candidate/interview/{session_id}` | Logged in | [6](#6-candidate-endpoints) |
| POST | `/api/candidate/interview/{session_id}/respond` | Logged in | [6](#6-candidate-endpoints) |
| GET | `/api/candidate/interview/{session_id}/feedback` | Logged in | [6](#6-candidate-endpoints) |
| POST | `/api/recruiter/jd/upload` | `recruiter` | [7](#7-recruiter-endpoints) |
| GET | `/api/recruiter/jds` | `recruiter` | [7](#7-recruiter-endpoints) |
| POST | `/api/recruiter/jd/{jd_id}/rank-candidates` | `recruiter` | [7](#7-recruiter-endpoints) |
| GET | `/api/recruiter/jd/{jd_id}/questions/{candidate_id}` | `recruiter` | [7](#7-recruiter-endpoints) |
| GET | `/api/recruiter/jd/{jd_id}/recommendation/{candidate_id}` | `recruiter` | [7](#7-recruiter-endpoints) |
| GET | `/api/admin/users` | `admin` (stub) | [8](#8-admin-endpoints-stubs) |
| GET | `/api/admin/analytics` | `admin` (stub) | [8](#8-admin-endpoints-stubs) |
| DELETE | `/api/admin/users/{user_id}` | `admin` (stub) | [8](#8-admin-endpoints-stubs) |
| GET | `/api/health/mcp` | Public | [9](#9-health-endpoints) |

Total: 26 endpoints (3 of them admin stubs).

---

## 11. Changelog

Newest first. Add one line per API change.

| Date | Phase | Change |
|---|---|---|
| 2026-11-09 | 6 | Added this reference. Documents all existing endpoints. |
| Phase 6 | 6 | Added `GET /api/health/mcp` (MCP gateway health). |
| Phase 5 | 5 | Added recruiter endpoints: JD upload, JD list, rank candidates, interview questions, hiring recommendation. |
| Phase 4 | 4 | Added mock interview endpoints (start, session, respond, feedback). |
| Phase 3 | 3 | Added candidate analysis endpoints (ATS score, skill gap, learning plan) and candidate JD endpoints. |
| Phase 2 | 2 | Added resume upload, list and detail. |
| Phase 1 | 1 | Added auth endpoints, admin stubs, `/` and `/health`. |

The phase for older rows is approximate. Check `git log` if you need exact dates.

---

## Updating this file

Do this whenever an endpoint changes:

1. **New endpoint:** add a section under the right prefix with method, path, role, request
   fields and limits, response fields, and every error message the code can raise. Add a
   row to the [Endpoint index](#10-endpoint-index) and update the total.
2. **Changed endpoint:** edit its section and add a changelog row describing what changed
   (especially anything that breaks existing clients).
3. **Removed endpoint:** delete its section and index row, and add a changelog row.
4. **Always:** update "Last reviewed" at the top and the changelog.
5. **Check against the code:** compare with the route file, the schema in
   `backend/app/models/schemas.py` and the service that raises the errors. Copy error
   messages exactly. If you are not sure of a value, check the code or `/api/docs`
   instead of guessing.
6. **Same commit:** the doc change goes in the same commit as the code change, so a
   reviewer sees both together.

Do not put real tokens, passwords, API keys or real user data in the examples.