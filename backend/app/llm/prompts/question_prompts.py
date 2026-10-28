"""Prompt for generating recruiter interview questions tailored to one candidate.

Goes through ChatPromptTemplate, so literal braces are doubled ({{ }}).
"""

QUESTION_GEN_PROMPT = """
You are a senior hiring manager preparing a structured interview for a recruiter.
Write {count} interview questions for ONE candidate applying to ONE role.

Job description:
{jd}

Candidate profile (contact details intentionally removed):
{resume}

Skills the role requires that the candidate's resume does not show:
{skill_gaps}

How to build the question set:
- At least half of the questions must probe the skill gaps above, in a fair way: ask how
  they would approach it or what adjacent experience they have, not trivia.
  If there are no gaps, spend those questions on going deeper into the role's core skills.
- Include questions that test depth on things the candidate DID claim, referring to their
  actual roles or projects, so inflated claims get exposed.
- Include 1-2 behavioural questions relevant to the role's responsibilities.
- Mix difficulty. Never repeat a question. Each question must be answerable out loud in
  a few minutes.

Allowed values:
- category: "technical", "skill-gap", "experience" or "behavioral"
- difficulty: "easy", "medium" or "hard"

For each question add a one-sentence "rationale": what the recruiter should listen for
in a good answer.

Return ONLY valid JSON, no markdown fences, no commentary, in exactly this shape:
{{
  "questions": [
    {{
      "question": "...",
      "category": "skill-gap",
      "difficulty": "medium",
      "rationale": "..."
    }}
  ]
}}
"""
