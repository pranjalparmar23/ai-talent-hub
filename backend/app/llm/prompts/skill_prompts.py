SKILL_GAP_PROMPT = """
Compare candidate skills vs required skills. Return ONLY valid JSON with:
missing_skills (list), matching_skills (list), gap_percentage (0-100).

Candidate Skills: {candidate_skills}
JD Required Skills: {jd_skills}
"""
