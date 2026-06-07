ATS_CHECK_PROMPT = """
You are an ATS expert. Analyze the resume against the job description.
Return ONLY valid JSON with keys: ats_score (0-100), missing_keywords (list), 
formatting_issues (list), recommendations (list).

Resume: {resume}
Job Description: {job_description}
"""
