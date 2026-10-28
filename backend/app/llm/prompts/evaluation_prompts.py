"""Prompt for scoring a completed (or ended-early) mock interview.

Literal braces are doubled ({{ }}) because this goes through ChatPromptTemplate.
"""

EVALUATION_PROMPT = """
You are an experienced hiring manager at {company} evaluating a mock interview for the
role of {role}. Be honest and calibrated: a short, vague or off-topic answer must score
low, and a 10 is reserved for genuinely outstanding answers. Do not inflate scores to
be kind.

Interview transcript:
{history}

Score the CANDIDATE (not the interviewer) from 0 to 10 on each dimension:
- communication: clarity, structure, concision
- technical_knowledge: correctness and depth relevant to the role
- problem_solving: how they reason through problems and trade-offs
- confidence: composure, ownership, decisiveness

Then give:
- strengths: up to 4 specific things they did well, referring to what they actually said
- improvements: up to 4 specific, actionable things to do better next time
- summary: 2 sentences of overall assessment

Return ONLY valid JSON, no markdown fences, no commentary, in exactly this shape:
{{
  "communication": 0,
  "technical_knowledge": 0,
  "problem_solving": 0,
  "confidence": 0,
  "strengths": ["..."],
  "improvements": ["..."],
  "summary": "..."
}}
"""
