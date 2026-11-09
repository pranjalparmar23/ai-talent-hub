# Skills safety checklist

How we keep Agent Skills safe in this repo. A skill is instructions that Claude follows, and it can include scripts that Claude runs. Treat adding or changing one like adding or changing code that runs with developer permissions.

**Tracker:** Phase 6, task 5. Background: `docs/architecture/skills.md`.

## Why this matters

Anthropic's guidance is blunt: use skills only from sources you trust, meaning ones you wrote yourself or got from Anthropic. A malicious or compromised skill can steer Claude into calling tools or running code in ways that do not match its stated purpose, or leak data. The risk is highest on machines and sessions that can reach secrets, private repos or production systems.

## The rules

1. **Our own skills only.** Everything in `.claude/skills/` is written by the team. We do not copy in skills from marketplaces, gists, blog posts or other repos.
2. **No surprises in the content.** What a skill does must match its `description`. Anything else is a reason to reject it.
3. **Scripts are code.** They get a normal code review, and a second reviewer where possible.
4. **No standing permissions.** We do not use `allowed-tools` in skill frontmatter unless a reviewer signs off on exactly what it pre-approves.
5. **Fetched content is data.** A skill that tells Claude to read web pages, READMEs or tool output must also say to treat that content as untrusted and never follow instructions found inside it.
6. **Nothing sensitive inside.** No secrets, tokens, personal data, real resumes or customer information.
7. **Changes go through review.** A change under `.claude/` is reviewed like source code in a pull request.

## Checklist: adding or changing a skill

### A. Provenance
- [ ] Written by the team (or, if adapted from something external, the source is recorded and every line was read and rewritten by us).
- [ ] The pull request says what the skill is for and who asked for it.

### B. The `SKILL.md` text
- [ ] The `description` says what the skill does **and** when to use it, and nothing more or less.
- [ ] `name` is lowercase letters, numbers and hyphens, at most 64 characters, and does not contain `claude` or `anthropic`.
- [ ] No instruction to ignore other instructions, hide actions from the user, or skip confirmation steps.
- [ ] No instruction to send data anywhere (URLs, webhooks, emails, third-party APIs).
- [ ] No instruction to read files outside the repo, such as home directories, `.env` files, SSH keys or cloud credentials.
- [ ] No destructive commands (`rm -rf`, force push, dropping databases) unless the skill's whole purpose is that, and then it must say to ask first.
- [ ] Any step that reads outside content says to treat it as untrusted data (rule 5).
- [ ] No secrets or personal data, including in examples.

### C. Bundled files and scripts
- [ ] Every file in the skill folder is listed in the pull request. Nothing is hidden in images, archives or binary files.
- [ ] Scripts are small and readable. No obfuscation, no encoded strings that get executed, no `curl | sh`.
- [ ] Scripts make no network calls unless that is their stated purpose, and then only to hosts named in the pull request.
- [ ] Scripts touch only paths inside the repo.
- [ ] Dependencies are pinned, and any new package is reviewed.
- [ ] Nothing downloads and runs remote code at use time. If an external dependency changes, a skill that was safe yesterday may not be today, so we avoid them.

### D. Permissions
- [ ] No `allowed-tools` entry, or a reviewer has approved it and the list is as narrow as possible.
- [ ] The skill does not depend on broader session permissions than a developer would grant by hand.

### E. Before merge
- [ ] A second person has read it, or the author has re-read it after a break.
- [ ] The inventory table below is updated.

## Red flags: reject or escalate

- A skill that was copied from somewhere and "just works".
- A description that does not match the body.
- Instructions to run something the user did not ask for.
- Network calls, base64 blobs, or long one-line shell commands in scripts.
- Requests for credentials, or for reading `.env`, `~/.ssh` or cloud config.
- Text that tells Claude to keep quiet about what it did.

## Ongoing

- **Review diffs, not just new skills.** A harmless skill can become unsafe in a later edit.
- **Re-audit** when a skill gains a script, an external reference, or an `allowed-tools` entry.
- **Delete** skills nobody uses.
- **Personal skills** (`~/.claude/skills/`) are not part of the project. Team conventions live only in the repo.
- If a skill misbehaves, stop the session, remove the skill, check what it touched, rotate any secret that might have been exposed, and record what happened.

## Inventory (last reviewed 2026-11-09)

| Skill | Source | Scripts | `allowed-tools` | Reads external content |
|---|---|---|---|---|
| `talent-hub-conventions` | Team-written | None | None | No |
| `content-source-vetting` | Team-written | None | None | Yes (web pages; treated as untrusted data) |
| `interview-rubric-authoring` | Team-written | None | None | No |
| `eval-golden-set` | Team-written | None | None | No |

## Source

- [Agent Skills overview, security considerations](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)