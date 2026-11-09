# Agent Skills in AI Talent Hub

What Agent Skills are, where they live, and the decision on where Talent Hub uses them.

**Tracker:** Phase 6, task 1 (learn the basics), task 3 (decide where Skills apply). Related: task 2 (first skill), task 4 (three dev skills), task 5 (safety checklist).

## 1. What a Skill is

A Skill is a folder with a `SKILL.md` file: YAML frontmatter plus Markdown instructions, and optionally extra files and scripts. Claude reads it when the task matches, instead of us pasting the same instructions into every prompt.

```
.claude/skills/talent-hub-conventions/
  SKILL.md            # required: frontmatter + instructions
  references/...      # optional: longer notes, read only when needed
  scripts/...         # optional: helper scripts, run via bash
```

```yaml
---
name: talent-hub-conventions
description: Project conventions for AI Talent Hub (FastAPI, SQLAlchemy async, LangGraph, tests, commits). Use when writing or reviewing code in this repo.
---
```

| Frontmatter field | Rules |
|---|---|
| `name` | Max 64 characters. Lowercase letters, numbers and hyphens only. Cannot contain `claude` or `anthropic`. No XML tags. |
| `description` | Required, max 1,024 characters, no XML tags. It must say **what** the skill does and **when** to use it, because it is the only thing Claude sees before deciding to load the skill. |

## 2. How loading works (progressive disclosure)

| Level | Loaded | Cost | What |
|---|---|---|---|
| 1. Metadata | Always, at startup | About 100 tokens per skill | `name` and `description` |
| 2. Instructions | When the skill matches the task | Under 5k tokens | The `SKILL.md` body |
| 3. Resources | Only when the instructions point to them | Nothing until read | Extra Markdown, templates, scripts |

Consequences for how we write skills:

- Many skills can be installed cheaply, so **one skill per job** beats one giant skill.
- The description decides everything. A vague description means the skill never triggers.
- Keep `SKILL.md` short and push detail into files it links to. Scripts run through bash, and only their output (not their code) enters the context.

## 3. Where Skills live and how they are used

| Location | Scope |
|---|---|
| `.claude/skills/<name>/SKILL.md` in the repo | **Project skills.** Shared through git with everyone who clones the repo. This is where ours go. |
| `~/.claude/skills/` | Personal skills, available across all of one person's projects. Not for team conventions. |

- **Claude invokes them itself** when a request matches the description, and you can also run one directly by typing `/<skill-name>`.
- Frontmatter can pre-approve tools for a skill (`allowed-tools`). We do **not** use this by default; a skill should not widen what Claude may do.
- Custom skills do not sync across surfaces. A skill in the repo is available to Claude Code working in the repo, not automatically in claude.ai or the API.
- The older `.claude/commands/` files still work, but skills are the recommended form.

## 4. Decision: where Skills apply in Talent Hub

**Decision: Skills are for the development workflow only. In-app agents do not use them.**

| | Dev workflow (Claude Code working on this repo) | In-app agents (resume parser, interview coach, ATS, etc.) |
|---|---|---|
| Skills? | **Yes** | **No** |
| Runs on | Claude, through Claude Code | Groq `openai/gpt-oss-120b`, through LangChain and LangGraph |
| How instructions are delivered | `SKILL.md` discovered from `.claude/skills/` | Prompt templates and node code in the repo |
| External knowledge | Files in the repo and docs | RAG (ChromaDB) and, for enrichment, the MCP gateway |

Why:

1. **Skills are a feature of Claude's tooling.** They work by Claude finding and reading files with its file and shell tools. Our in-app agents are fixed LangGraph steps calling a Groq model; they have no skill discovery and no shell.
2. **Determinism.** The product's behaviour must be reproducible and testable (Phase 10 evals). Instructions that load only "when the model decides they are relevant" are the wrong mechanism for that. In-app behaviour stays in versioned prompts and code.
3. **Attack surface.** Loading instruction files into a runtime agent's context is a new prompt-injection path. We already have a controlled path for outside content (the MCP gateway, with its untrusted-data wrapping). We do not add a second one.
4. **Real value is on the dev side.** Repeated engineering jobs (conventions, vetting a data source, writing a rubric, building a golden eval set) are where a reusable skill pays off.

**What would change this decision:** if we ever run an agent on Claude through the Agent SDK, that agent could use skills, and this document should be revisited. Until then, no `skills` option and no skill loading in `backend/`.

## 5. Skill, CLAUDE.md, docs or prompt?

| Content | Where it goes |
|---|---|
| Procedures for a repeated dev job, loaded only when relevant | **Skill** (`.claude/skills/`) |
| Short facts needed on every task | A `CLAUDE.md` at the repo root (none exists yet; optional) |
| Explanations and decisions for humans | `docs/` |
| Instructions an in-app agent follows at runtime | Prompt templates in `backend/app/` |
| Outside reference data for an agent | RAG, or MCP through the gateway |

## 6. Our skills

| Skill | Purpose | Task |
|---|---|---|
| `talent-hub-conventions` | Repo layout, async and test patterns, lint and commit rules | 2 |
| `content-source-vetting` | Licence, terms of service and trust checks before ingesting a source or enabling an MCP server | 4 |
| `interview-rubric-authoring` | How to write a scoring rubric per interview type | 4 |
| `eval-golden-set` | How to build and maintain a golden evaluation set | 4 |

Rules for writing them:

- The description states what it does and when to use it, with the words a developer would actually say.
- Keep the `SKILL.md` body short (well under 500 lines); put detail in files it links to, one level deep.
- No secrets, no real user data, no resume text in a skill.
- Scripts only when needed, kept small, and reviewed like code.
- Names are kebab-case and never contain `claude` or `anthropic`.

## 7. Safety

Anthropic's guidance: use skills only from sources you trust, meaning ones you wrote or got from Anthropic. A malicious skill can steer Claude into running tools or code that do not match its stated purpose.

For this repo:

- Only skills written by the team live in `.claude/skills/`. No third-party skills are copied in.
- Treat any change under `.claude/` like code in review: read the instructions and every script.
- The full checklist is in `docs/security/skills-safety-checklist.md` (Phase 6, task 5).

## 8. Key takeaways (task 1)

- A skill is a folder with `SKILL.md`; the frontmatter has two required fields, `name` and `description`.
- Only the description is always loaded, so it must say what the skill does and when to use it.
- Instructions load on demand; bundled files and scripts load only when read or run.
- Project skills live in `.claude/skills/` and travel with the repo.
- Skills are for how we build the product, not for what the product's agents run.

## Sources

- [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- [Agent skills in the Agent SDK](https://code.claude.com/docs/en/agent-sdk/skills)
- [Extend Claude with skills (Claude Code)](https://code.claude.com/docs/en/skills)