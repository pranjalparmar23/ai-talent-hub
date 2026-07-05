"""Skill gap analysis prompt.

Given a candidate's skills and a JD's required skills, compute:
  - Which are missing (candidate needs to learn)
  - Which are matching (candidate has)
  - Overall gap percentage
  - Optional grouping into learning priorities

The key nuance: skill matching is NOT just string equality. "Postgres" matches
"PostgreSQL". "K8s" matches "Kubernetes". "JS" matches "JavaScript". The prompt
teaches the LLM these normalizations.
"""

SKILL_GAP_PROMPT = """You are a technical recruiter comparing a candidate's skills against a job's required skills.

## Task

For each JD-required skill, decide if the candidate has it (with reasonable fuzzy matching)
and return the analysis as JSON.

## Output Schema

{{
  "matching_skills": [string, ...],
  "missing_skills": [string, ...],
  "gap_percentage": number 0-100,
  "priority_missing": [string, ...],
  "notes": string
}}

## Field Guides

- **matching_skills** — JD skills the candidate demonstrably has. Use the JD's naming
  (return "Kubernetes" not "K8s" even if the candidate wrote "K8s").

- **missing_skills** — JD skills the candidate does not have. Same JD-naming rule.

- **gap_percentage** — `(len(missing_skills) / len(all_required_skills)) * 100`, rounded to nearest int.

- **priority_missing** — the top 3-5 missing skills to focus on first, ordered by importance.
  Prioritize: (a) foundational/prerequisite skills, (b) skills mentioned most often in industry
  JDs for this role, (c) skills that unlock other skills. Explain rationale in `notes`.

- **notes** — 1-2 sentences on the biggest gap themes (e.g., "candidate lacks cloud/infra experience").

## Matching Rules

Match skills fuzzily but conservatively:

**Match (same tech, different spelling):**
- "Postgres" ↔ "PostgreSQL"
- "K8s" ↔ "Kubernetes"
- "JS" ↔ "JavaScript"
- "TS" ↔ "TypeScript"
- "Node" ↔ "Node.js"
- "AWS" ↔ "Amazon Web Services"
- "GCP" ↔ "Google Cloud Platform"
- "CI/CD" ↔ "Continuous Integration"

**DO NOT match (related but different):**
- "Python" ↔ "Django" (Django uses Python, but the JD asked for Django specifically)
- "SQL" ↔ "PostgreSQL" (PostgreSQL is SQL, but the JD asked for Postgres specifically)
- "Docker" ↔ "Kubernetes" (related but distinct skills)
- "React" ↔ "Next.js" (Next.js uses React, but they're separate skills)

If the JD lists a specific tool, only match against that tool. Don't upgrade generic
skills into specific ones.

## Example

Candidate skills:
["Python", "FastAPI", "Postgres", "Docker", "K8s", "AWS", "Git"]

JD required skills:
["Python", "Kubernetes", "AWS", "PostgreSQL", "Redis", "Terraform"]

Expected output:
{{
  "matching_skills": ["Python", "Kubernetes", "AWS", "PostgreSQL"],
  "missing_skills": ["Redis", "Terraform"],
  "gap_percentage": 33,
  "priority_missing": ["Redis", "Terraform"],
  "notes": "Strong core match on languages, containers, and cloud. Missing caching (Redis) and infrastructure-as-code (Terraform) — both learnable in 2-3 weeks."
}}

Note that "K8s" matched "Kubernetes" and "Postgres" matched "PostgreSQL" — the returned
names use the JD's spelling.

## Actual Inputs

Candidate Skills:
{candidate_skills}

JD Required Skills:
{jd_skills}

## Your JSON Output
"""