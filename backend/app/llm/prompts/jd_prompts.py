JD_ANALYSIS_PROMPT = """
Parse this job description. Return ONLY valid JSON with:
title, company, skills (list), experience_years (int), 
responsibilities (list), nice_to_have (list).

JD: {jd_text}
"""
