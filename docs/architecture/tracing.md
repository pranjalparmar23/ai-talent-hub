# Tracing conventions (LangSmith)

How AI Talent Hub uses LangSmith: which projects exist, how runs are named and tagged, what is allowed to appear in a trace, and how we stay inside the free plan.

**Tracker:** Phase 6, task 15 (needed from Phase 7). **Related:** `docs/architecture/mcp.md`, `.claude/skills/eval-golden-set/SKILL.md`.
**Status:** policy now. The helper in `backend/app/observability/tracing.py` exists but is **not wired into any agent until Phase 7**.

## What tracing is for

- Seeing why an agent answered the way it did (prompt, model, latency, errors).
- Comparing runs before and after a prompt, model or retrieval change (Phase 7 and Phase 10 evals).
- Watching cost and failure rates over time.

It is **not** a store for user content. Traces are a copy of what the model saw, so they are personal data whenever the input was.

## Facts this design relies on

| Fact | Source |
|---|---|
| Developer plan: $0, one seat, up to 5k base traces a month, 14-day base retention. Usage beyond that is pay-as-you-go on paid billing. | [LangChain pricing](https://www.langchain.com/pricing) (read 2026-11-09) |
| With **no payment method** on file the account is capped at 5,000 traces a month; beyond it the API returns HTTP 429. Adding a payment method raises the hourly limits, and the page implies, but does not state, that the monthly cap then no longer applies. | [Usage and billing](https://docs.langchain.com/langsmith/usage-and-billing) |
| We can set a monthly **usage** limit (not a spend limit) under Settings > Usage and Billing. Limits are approximate. | same |
| Retention is set per tracing project. Online evaluators, automation rules and explicit feedback can upgrade traces to 180-day extended retention, which costs extra. Datasets are kept indefinitely. | same |
| `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS` hide all inputs/outputs before data leaves the process. A `Client(anonymizer=...)` can mask patterns. **Metadata is not covered** by either. Regex masking is not exhaustive. | [Masking guide](https://docs.langchain.com/langsmith/mask-inputs-outputs.md) |
| `LANGSMITH_TRACING_SAMPLING_RATE` (0 to 1) logs a share of traces. It is probabilistic. | [Sampling guide](https://docs.langchain.com/langsmith/sample-traces) |
| `tracing_context(enabled=...)` turns tracing on or off for one block of code and overrides the environment. | [Conditional tracing](https://docs.langchain.com/langsmith/conditional-tracing) |
| Checked in our venv (`langsmith 0.1.143`): env names are read as `LANGSMITH_*` first, then `LANGCHAIN_*`; tracing is on when `TRACING_V2` or `TRACING` is `true`; the hide-inputs, hide-outputs and sampling variables exist; `Client(anonymizer=...)` and `tracing_context` exist; **`hide_metadata` does not**. | our installed package |
| `langchain-core 0.3.17` requires `langsmith>=0.1.125,<0.2.0`, so we cannot upgrade `langsmith` alone to get newer features. | our installed package |

Every number above can change. Confirm the plan numbers on LangSmith's **Usage and Billing** page after setup.

## Environments and projects

`APP_ENV` already exists (`development`, `production`). LangSmith now groups traces by **agent** and **environment**. We have one agent, `AI Talent Hub` (ID `talent-hub`). Each environment traces into its own project, named `<agent ID>-<environment>` and set with `LANGCHAIN_PROJECT` in that environment's `.env`. Confirmed on 2026-11-09 for development: `LANGCHAIN_PROJECT=talent-hub-development` appears under the Development environment with the pinned `langsmith 0.1.143`. The staging and production names follow LangSmith's naming rule but have not been tested. LangSmith has no CI environment, so `staging` is the reserved name for CI and eval runs.

| Environment | Project | Tracing | Content in traces | Data allowed |
|---|---|---|---|---|
| development (`APP_ENV=development`) | `talent-hub-development` | On, sample rate 1.0 | Inputs and outputs visible, anonymizer on | Synthetic data or your own; never another person's resume or transcript |
| CI | `talent-hub-staging` (reserved) | **Off** (`LANGCHAIN_TRACING_V2=false`, already set in `ci.yml`) | n/a | Synthetic only |
| Eval runs (Phase 10) | `talent-hub-staging` | On, on demand only | Visible | Golden-set data only (synthetic) |
| production (`APP_ENV=production`) | `talent-hub-production` | On, sample rate starts at 0.1 | **Hidden** (inputs and outputs) | Real users |

Notes:
- Older names (`talent-ai` in `.env.example`, `ai-talent-hub` in the README) are replaced by the `talent-hub-<environment>` scheme (`development`, `staging`, `production`).
- The monthly cap is for the account, not per project, so dev, eval and prod share the same 5,000 traces.
- **Production traces carry shape, not content:** which agent ran, which model, how long, how many tokens, whether it failed, tags and hashed ids. That is what monitoring needs, and it means a trace can never leak a resume. Turning content on in production for debugging requires a deliberate, time-boxed decision recorded in the pull request.

## Run names, tags and metadata

**Run names** use `<area>.<name>`, lowercase, no user data:

| Kind | Examples |
|---|---|
| Agents | `candidate.resume_parser`, `candidate.ats`, `candidate.skill_gap`, `candidate.roadmap`, `candidate.interview`, `recruiter.jd_analyzer`, `recruiter.question`, `recruiter.recommendation`, `shared.evaluation` |
| Graphs | `graph.<name>` (for example `graph.interview`) |
| MCP calls | `mcp.<server>.<tool>` |
| RAG | `rag.retrieve.<collection>` |

**Tags** are a short, fixed vocabulary (low cardinality, never user input):

| Tag | Values |
|---|---|
| `area:` | `candidate`, `recruiter`, `shared` |
| `agent:` | the agent name |
| `env:` | `dev`, `ci`, `prod` |
| `prompt:` | the prompt or rubric version (for example `rubric-v1`) |
| `model:` | the model id |
| `role_family:` | `technical`, `non_technical` (for eval slices) |
| `eval` | present on runs started by an eval script |

**Metadata** (key and value pairs) is limited to: `user_hash`, `request_id`, `app_version`, `prompt_version`, `rubric_version`, `model`, `collection`, `top_k`, `mcp_server`, `mcp_tool`, `mcp_ok`, `mcp_error_code`, `duration_ms`.

Because the installed SDK cannot mask metadata, **nothing else goes in tags or metadata**: no names, emails, resume or JD text, transcript text, free-text user input, or MCP result text.

## Hashed user ids

A trace may identify a user only as `user_hash`: an HMAC-SHA256 of the user id with a secret key, shortened to 16 hex characters. The secret is `TRACE_HASH_SECRET` in `.env`. It lets us group a user's runs while debugging without storing an id someone could look up. If the secret is missing, the helper uses `anon` rather than an unkeyed hash. Rotate the secret if it leaks.

## Privacy rules (risk R11)

1. **No real personal data in dev, CI or eval traces.** Synthetic or your own data only.
2. **Production hides inputs and outputs** (`LANGSMITH_HIDE_INPUTS=true`, `LANGSMITH_HIDE_OUTPUTS=true`).
3. **Redaction is a second layer.** Where content is visible (dev), the anonymizer masks emails, phone numbers, long digit runs, API-key shapes and URLs with tokens. It is not exhaustive, so rule 1 still applies.
4. **Voice (Phase 8):** audio and transcripts are never traced. Wrap speech handling in `tracing_context(enabled=False)`.
5. **MCP results are not traced as content.** Record the server, tool, success, error code and duration as metadata. This also respects server terms: Brave's search plans do not allow retaining output (see `docs/mcp/vetting-log.md`), and a LangSmith trace is retention. The gateway cache question in that log still applies separately.
6. **Never trace secrets.** Tool results and prompts must not contain keys. The gateway already scrubs secrets from error text.
7. **Delete on request.** Production traces hold only hashed ids and no content, but if a user asks for deletion, find runs by `user_hash` and delete them.

## Sampling and the trace budget

- 5,000 traces a month is about 160 a day, shared by dev, eval and prod.
- **Dev:** rate 1.0 (low volume).
- **Prod:** start at 0.1 and adjust after a week of real usage on the Usage page. A trace is one root run with its child runs (confirm in the Usage page how it is counted).
- **Eval runs:** on demand only, never scheduled until the budget is known. A 20-case set costs about 20 traces per run.
- **Online evaluators and automation rules:** not enabled before Phase 10. They can upgrade traces to extended retention, which costs extra.
- **Keep no payment method on file** until the Phase 12 decision. Then, going over the cap means traces stop (HTTP 429) instead of a bill. Tracing failures must never break a request: LangSmith errors are logged and ignored.
- Set the monthly **usage limit** below 5,000 as an early warning.

## Per-request control

- Turn tracing off for one block: `with tracing_context(enabled=False): ...`.
- Turn it on with labels: `tracing_context(enabled=True, project_name=..., tags=[...], metadata={...})`.
- A `tracing_context` block overrides the environment variable inside its scope; nested blocks use the innermost.
- Calls into chains pass labels through the standard config: `chain.ainvoke(data, config={"run_name": ..., "tags": [...], "metadata": {...}})`. `build_run_config()` in `tracing.py` produces exactly that dict.

## What Phase 7 wires

1. Each agent passes `config=build_run_config(...)` when it calls its chain (one line per agent).
2. Graphs set `run_name="graph.<name>"`.
3. The gateway adds MCP metadata (rule 5) to the parent run.
4. Production sets the hide-inputs and hide-outputs variables; development builds a `Client` with the anonymizer from `tracing.py`.
Verified 2026-11-09: with `@traceable(client=build_client())`, an email in the input was masked in LangSmith. This is still unverified for LangChain's automatic tracer.

## Manual setup (you do this in LangSmith)

- [ ] Create or open your LangSmith account (Developer plan, one seat).
- [ ] Create one agent: name `AI Talent Hub`, ID `talent-hub` (the ID cannot be changed). Development is confirmed working. Check the Production environment view when production tracing is first turned on.
- [ ] Create an API key. **Regenerate any key that has been pasted into a chat or committed.** Keep keys in each environment's `.env` only.
- [ ] Set `LANGCHAIN_PROJECT` per environment as in the table above.
- [ ] Under Settings > Usage and Billing, set the monthly trace usage limit below 5,000. **Do not add a payment method.**
- [ ] Run one dev request, confirm a trace appears in `talent-hub-development`, and read the Usage page to see how it was counted.
- [ ] Update tracker row 19 with the confirmed limits.

## Open decisions

- Which production monitoring tool sits beside LangSmith (tracker question D12). LangSmith covers traces, not uptime or infrastructure metrics.
- Whether to move to newer LangChain and LangSmith versions later, which would add `hide_metadata`.
- Whether any production debugging window with visible content is ever acceptable.