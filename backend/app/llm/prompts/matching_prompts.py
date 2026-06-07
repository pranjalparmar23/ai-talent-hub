MATCHING_PROMPT = """
Compute candidate-job match score. Return ONLY valid JSON with:
match_score (0-100), skill_match (0-100), experience_match (0-100),
project_relevance (0-100), summary (string).

Resume: {resume}
JD: {jd}
"""
