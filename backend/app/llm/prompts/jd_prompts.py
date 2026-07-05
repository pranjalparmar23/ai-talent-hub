"""Job Description parsing prompt — extracts structured JSON from raw JD text.

Design mirrors the resume prompt: schema + example + rules. This is what turns
copy-pasted job listings into the structured `ParsedJD` shape that ATSAgent,
SkillGapAgent, and RoadmapAgent all consume.
"""

JD_PARSE_PROMPT = """You are an expert job description parser. Extract structured information from the job description below into JSON.

## Output Schema

Return a JSON object with exactly these keys (use null or empty list when unavailable):

{{
  "title": string,
  "company": string or null,
  "location": string or null,
  "role_level": string or null,
  "employment_type": string or null,
  "experience_years_min": integer or null,
  "skills_required": [string, ...],
  "skills_preferred": [string, ...],
  "responsibilities": [string, ...],
  "qualifications": [string, ...]
}}

## Field Guides

- **role_level** — one of: "junior", "mid", "senior", "lead", "principal", "staff".
  Infer from title keywords ("Junior" → junior, "Senior" → senior, "Staff Engineer" → staff).
  If no seniority signal, use null.

- **employment_type** — one of: "full-time", "part-time", "contract", "internship", "temporary".
  Default to "full-time" if the JD says nothing but describes a real job.

- **experience_years_min** — the *minimum* required years, not maximum.
  "3-5 years" → 3.  "5+ years" → 5.  "entry level" → 0.  Unclear → null.

- **skills_required** — technical skills, tools, frameworks, languages that are explicitly required
  (words like "must have", "required", "essential", or listed in a "Requirements" section).
  Extract as short strings: ["Python", "Kubernetes", "PostgreSQL"] not ["3 years of Python"].

- **skills_preferred** — same as required but in "nice to have", "preferred", "bonus" contexts.
  If the JD doesn't distinguish, put everything in `skills_required` and leave preferred empty.

- **responsibilities** — one bullet per string, keep original wording, no rewriting.
  Focus on what the person will *do* (design systems, mentor juniors, own X domain).

- **qualifications** — degree requirements, years of experience clauses, industry background.
  Focus on what the person *needs* (5+ years experience, BS in CS, worked at scale, etc.).

## Rules

1. **Skills are always short strings**, never sentences or requirements.
   Good: ["Python", "AWS", "Terraform"]
   Bad: ["Strong Python skills required", "5+ years of AWS experience"]

2. **Never invent skills** the JD doesn't mention. If a JD says "backend engineer" without
   specifying languages, `skills_required` might be empty — that's fine.

3. **Distinguish required vs preferred carefully**. If a JD uses "must have" or "required"
   → required. "Nice to have", "bonus", "plus" → preferred.

4. **Deduplicate**. If the JD mentions "Python" three times in different sentences, list it once.

## Example

Input JD:
Senior Backend Engineer — Acme Corp
San Francisco, CA (Hybrid) · Full-time
About the role:
We're hiring a Senior Backend Engineer to own our payments infrastructure.
You'll design distributed systems handling $10M+ in daily transactions.
Requirements:

5+ years of backend engineering experience
Strong proficiency in Python and Go
Deep experience with PostgreSQL and Redis
Production experience with Kubernetes on AWS
BS in Computer Science or equivalent

Nice to have:

Experience with Kafka or other event streaming
Exposure to fintech or payments domain
Contributions to open-source projects

Responsibilities:

Design and build payments microservices
Mentor mid-level engineers
Own database schema evolution and migrations
Partner with Product to define technical roadmap


Expected output:
{{
  "title": "Senior Backend Engineer",
  "company": "Acme Corp",
  "location": "San Francisco, CA (Hybrid)",
  "role_level": "senior",
  "employment_type": "full-time",
  "experience_years_min": 5,
  "skills_required": ["Python", "Go", "PostgreSQL", "Redis", "Kubernetes", "AWS"],
  "skills_preferred": ["Kafka"],
  "responsibilities": [
    "Design and build payments microservices",
    "Mentor mid-level engineers",
    "Own database schema evolution and migrations",
    "Partner with Product to define technical roadmap"
  ],
  "qualifications": [
    "5+ years of backend engineering experience",
    "BS in Computer Science or equivalent",
    "Exposure to fintech or payments domain",
    "Contributions to open-source projects"
  ]
}}

## Actual Job Description

{jd_text}

## Your JSON Output
"""