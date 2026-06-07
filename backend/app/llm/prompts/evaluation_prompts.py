EVALUATION_PROMPT = """
Evaluate this mock interview. Return ONLY valid JSON with:
communication (0-10), technical_knowledge (0-10), problem_solving (0-10),
confidence (0-10), overall (0-10), strengths (list), improvements (list).

Interview History: {history}
"""
