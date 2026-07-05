"""Resume parsing prompt — extracts structured JSON from raw resume text.

Design choices:
- Explicit JSON schema in the prompt (models follow schemas better than prose instructions)
- One worked example — few-shot beats zero-shot for structured extraction
- Explicit rules for edge cases (missing sections, unusual formatting, dates)
- No 'return only JSON' at the end — modern instruct-tuned models handle this if the
  system prompt is clear from the start
"""

RESUME_PARSE_PROMPT = """You are an expert resume parser. Extract structured information from the resume below into JSON.

## Output Schema

Return a JSON object with exactly these keys (use null or empty list when unavailable):

{{
  "name": string,
  "email": string or null,
  "phone": string or null,
  "location": string or null,
  "linkedin": string or null,
  "github": string or null,
  "summary": string or null,
  "skills": [string, ...],
  "experience_years": number,
  "experience": [
    {{
      "company": string,
      "role": string,
      "start_date": string or null,
      "end_date": string or null,
      "location": string or null,
      "highlights": [string, ...]
    }}
  ],
  "education": [
    {{
      "institution": string,
      "degree": string,
      "field": string or null,
      "start_date": string or null,
      "end_date": string or null,
      "gpa": string or null
    }}
  ],
  "projects": [
    {{
      "name": string,
      "description": string,
      "tech_stack": [string, ...],
      "link": string or null
    }}
  ],
  "certifications": [string, ...]
}}

## Rules

1. **Skills** — extract *individual* skills as short strings, not sentences.
   Good: ["Python", "FastAPI", "PostgreSQL", "Docker"]
   Bad: ["3+ years of Python experience with FastAPI framework"]

2. **experience_years** — sum all professional (non-internship, non-project) years.
   If unclear, estimate from the earliest work start date to today. Round to nearest integer.

3. **Dates** — use "YYYY-MM" or "YYYY" format. Use "Present" for current roles.

4. **Highlights** — one bullet point per string, keep original wording, no rewriting.

5. **Never invent data.** If a section isn't in the resume, use `null` or `[]`.

6. **Contact info** — only extract if actually present. Don't guess phone country codes.

## Example

Input resume:
JANE DOE
jane.doe@example.com | +1-555-0100 | linkedin.com/in/janedoe
SUMMARY
Backend engineer with 4 years experience building scalable APIs.
EXPERIENCE
Senior Backend Engineer — Acme Corp, San Francisco (2023-01 to Present)

Led migration of monolith to microservices, reduced deploy time by 60%
Built rate limiting layer serving 10k RPS

Backend Engineer — Startup Inc, Remote (2021-06 to 2022-12)

Owned Python API for user authentication

SKILLS
Python, Go, PostgreSQL, Redis, Kubernetes, AWS
EDUCATION
BS Computer Science, State University (2017-2021), GPA: 3.8

Expected output:
{{
  "name": "Jane Doe",
  "email": "jane.doe@example.com",
  "phone": "+1-555-0100",
  "location": null,
  "linkedin": "linkedin.com/in/janedoe",
  "github": null,
  "summary": "Backend engineer with 4 years experience building scalable APIs.",
  "skills": ["Python", "Go", "PostgreSQL", "Redis", "Kubernetes", "AWS"],
  "experience_years": 4,
  "experience": [
    {{
      "company": "Acme Corp",
      "role": "Senior Backend Engineer",
      "start_date": "2023-01",
      "end_date": "Present",
      "location": "San Francisco",
      "highlights": [
        "Led migration of monolith to microservices, reduced deploy time by 60%",
        "Built rate limiting layer serving 10k RPS"
      ]
    }},
    {{
      "company": "Startup Inc",
      "role": "Backend Engineer",
      "start_date": "2021-06",
      "end_date": "2022-12",
      "location": "Remote",
      "highlights": ["Owned Python API for user authentication"]
    }}
  ],
  "education": [
    {{
      "institution": "State University",
      "degree": "BS",
      "field": "Computer Science",
      "start_date": "2017",
      "end_date": "2021",
      "gpa": "3.8"
    }}
  ],
  "projects": [],
  "certifications": []
}}

## Actual Resume

{resume_text}

## Your JSON Output
"""