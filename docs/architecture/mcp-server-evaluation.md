# Should Talent Hub expose an MCP server?

A decision record. **Nothing is built.**

**Tracker:** Phase 6, task 14. **Date:** 2026-11-09. **Owner:** Dev Lead.
**Related:** `docs/architecture/mcp.md` (we are an MCP *client* today), `docs/architecture/tracing.md`, `docs/mcp/vetting-checklist.md`.

## The question

Today Talent Hub *calls* other people's MCP servers through the gateway. The idea here is the reverse: publish our own **read-only** MCP server so a recruiter's own AI assistant could ask Talent Hub things like "rank the candidates for my Data Analyst job" without opening our web app.

## Decision

**Not now.** We record the design below and revisit after Phase 11 (security hardening), or earlier if the triggers at the end are met.

Reasons in one paragraph: what recruiters would want (rankings) is not a plain read in our system, it carries personal data about candidates and text that candidates wrote, and a public MCP server needs an OAuth 2.1 setup that Talent Hub does not have. Each of those is solvable, but together they are a feature, not a spike, and no recruiter has asked for it yet.

## What exists today (checked in the repo)

| Fact | Where |
|---|---|
| Recruiter routes: upload a JD, list my JDs, `rank-candidates`, interview questions, hiring recommendation. | `app/api/recruiter_routes.py` |
| Every route requires the `recruiter` role. Another recruiter's JD is a 404. | `require_role`, service-layer ownership checks |
| Login is a username/password token endpoint that issues a JWT with a `role`. It is not an OAuth 2.1 authorization server. | `app/middleware/auth.py` |
| `rank-candidates` runs the recruiter graph on demand ("score and rank every candidate against this JD"). It calls the LLM. Whether results are stored is **not** established here. | `app/graphs/recruiter_graph.py` |
| A ranked candidate carries `candidate_id`, `name`, scores, matching and missing skills, a free-text `summary`, and `interview_score`. | `CandidateRankingItem` in `app/database/schemas.py` |

## What the MCP spec requires of a server others can connect to

From the authorization section of the 2026-07-28 revision (the newest dated revision I found; confirm before building) and its security best practices:

- Authorization is optional in the protocol, but HTTP servers **should** follow the spec. A server that returns candidate data must.
- The MCP server acts as an **OAuth 2.1 resource server**. It **must** publish Protected Resource Metadata (RFC 9728) so clients can find an authorization server.
- The server **must** check that every access token was issued **for it** (audience binding, RFC 8707), and **must not** accept or pass on any other token (no "token passthrough").
- Invalid tokens get HTTP 401; insufficient scope gets 403 with a `WWW-Authenticate` challenge naming the scopes. Scopes should be minimal, not one broad "all access" scope.
- MCP has no sessions. If a tool hands back a handle (for example an id), possession of it must never count as authentication, and handles must be bound to the verified user.
- The authorization server must support PKCE and discovery metadata, and clients register through Client ID Metadata Documents or dynamic registration.

Sources: [Authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization), [Security best practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices). Read 2026-11-09.

## Options

| Option | What it means | Verdict |
|---|---|---|
| **A. Nothing** | Recruiters use the web app. | Fine today. |
| **B. Build the server now** | Add an authorization server, a `/mcp` endpoint and tools. | Too much unsettled: see "Why not now". |
| **C. Decide, prepare, revisit** | Keep services clean, write down the tool design and preconditions, review at the triggers. | **Chosen.** |
| **D. Skip MCP, offer an API key** | Give recruiters a scoped API key for the existing REST API. | A possible simpler path. Weigh at the revisit. |

## Why not now

1. **Auth is a project of its own.** We would need an authorization server (ours or a hosted identity provider) that does OAuth 2.1 with PKCE, metadata discovery and client registration, plus audience-bound tokens and scopes. Today's JWT login does none of that. The `SECRET_KEY` default in `middleware/auth.py` is already queued for Phase 11, and exposing a new door before that is fixed would be backwards.
2. **"Read-only" is not true for rankings.** The ranking endpoint computes on demand with the LLM. An MCP tool that calls it would spend our Groq quota on every call, so a client in a loop is a cost problem (OWASP LLM10, unbounded consumption). A genuinely read-only tool needs stored results to read, and that is a product change.
3. **Personal data leaves our stack.** A ranking contains candidate names, ids, skills and a summary. Through MCP it lands in the recruiter's own AI assistant, which we do not control, outside the Groq-only data path and outside our tracing and logging rules (risk R11). Candidates would need clear notice that their data can be shared that way, and employment-related automated scoring may carry legal obligations. I have **not** checked those rules here, so this needs a proper review.
4. **Resume text becomes a prompt-injection path.** Fields such as `summary` and the skill lists come from resumes that candidates write. Handed to a recruiter's assistant, they can contain instructions aimed at that assistant (OWASP LLM01). We would need strict output schemas, no free text from resumes, or clear marking of it as untrusted data.
5. **No demand yet.** Nobody has asked for it.

## Design to use if we build it

Tools (all read-only, all scoped to the signed-in recruiter, ownership checked by the existing services):

| Tool | Returns | Notes |
|---|---|---|
| `list_my_jobs` | id, title, created date | From `JDSummary` |
| `get_ranking(jd_id, top_n)` | Stored ranking: rank, candidate_id, scores, matching and missing skills | **Stored results only.** `top_n` capped. No names unless justified, no free-text summary |
| `get_candidate_match(jd_id, candidate_id)` | The scores for one pair | Same fields as above |

Rules:

- Token audience is our MCP URL. Reject all other tokens. Never forward a client's token to anything else.
- Minimal scopes, for example `jobs:read` and `rankings:read`, with step-up for anything more. No wildcard scope.
- Reuse the service layer so a JD that is not yours is a 404, exactly as today.
- Structured fields only. Anything that originated in a resume is omitted or labelled as untrusted.
- Per-token rate limit and a result-size cap; no LLM work behind a tool call.
- Audit log with hashed user ids; no personal data in logs or traces; no tool results in traces.
- A feature flag (off by default) and a kill switch.
- HTTPS only, deployed behind its own route and limits.
- Tests with a fake MCP client, like the gateway tests, covering expired token, wrong audience, insufficient scope, another recruiter's JD, oversize results.
- Build it on the official `mcp` SDK, as with `docker/mcp-demo`. Check the v2 changes first (see the vetting log).

## Preconditions before building

1. Phase 11 hardening done: real `SECRET_KEY` handling, rate limits, log and trace privacy review.
2. Rankings are stored, so tools can read instead of recompute.
3. Candidate notice and consent wording agreed, and the legal question in item 3 above answered.
4. A decision on the authorization server: build it in the app or use a hosted identity provider.
5. A written answer to "what is the smallest data set a recruiter's assistant needs?"

## Triggers to revisit early

- A real recruiter asks to use Talent Hub from their own AI assistant.
- We choose a hosted identity provider for other reasons.
- Option D (a scoped API key) turns out to be what recruiters actually want.

**Next review:** after Phase 11, and no later than 2027-05-09.