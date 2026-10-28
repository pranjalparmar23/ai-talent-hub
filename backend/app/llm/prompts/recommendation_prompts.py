"""Prompt for explaining a hiring recommendation.

The HIRE / CONSIDER / REJECT verdict is decided in code before this prompt runs;
the model's only job is to explain it. Literal braces are doubled ({{ }}).
"""

RECOMMENDATION_PROMPT = """
You are a hiring manager writing a short justification for a recruiter.

The decision has ALREADY been made by the scoring system. Do not change it, do not
argue with it, and do not suggest a different outcome. Explain it using only the
evidence below.

Role: {role}
Decision: {verdict}

Evidence:
- Resume-to-job match score: {match_score} / 100
- Match breakdown: {breakdown}
- Required skills the candidate has: {matching_skills}
- Required skills the candidate is missing: {missing_skills}
- Mock interview score: {interview_score}

Candidate profile (name and contact details intentionally removed):
{candidate}

Write:
- "reasoning": 2-3 sentences explaining why this decision follows from the evidence.
  Mention the most decisive facts, with numbers where useful.
- "strengths": up to 4 short phrases, each grounded in the evidence.
- "risks": up to 4 short phrases about gaps or uncertainties a recruiter should probe.

Rules: never invent facts that are not in the evidence, and never refer to the candidate's
gender, age, ethnicity, nationality or any other personal characteristic.

Return ONLY valid JSON, no markdown fences, no commentary, in exactly this shape:
{{
  "reasoning": "...",
  "strengths": ["..."],
  "risks": ["..."]
}}
"""
