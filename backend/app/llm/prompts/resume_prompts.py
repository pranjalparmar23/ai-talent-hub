RESUME_PARSE_PROMPT = """
You are an expert resume parser. Extract structured information from the resume below.
Return ONLY valid JSON with keys: name, email, phone, skills (list), experience (years), 
education (list), projects (list), certifications (list).

Resume:
{resume_text}
"""
