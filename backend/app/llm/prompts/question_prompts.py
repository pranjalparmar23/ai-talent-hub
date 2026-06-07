QUESTION_GEN_PROMPT = """
Generate 10 targeted interview questions for a candidate with skill gaps.
Return ONLY valid JSON: list of {{question, category, difficulty}}.

JD: {jd}
Resume: {resume}
Skill Gaps: {skill_gaps}
"""
