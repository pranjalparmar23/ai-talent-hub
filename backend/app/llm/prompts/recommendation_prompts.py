RECOMMENDATION_PROMPT = """
Make a hiring recommendation. Return ONLY valid JSON with:
recommendation (HIRE/CONSIDER/REJECT), confidence (0-100), reasoning (string).

Candidate: {candidate}
Match Score: {match_score}
Interview Score: {interview_score}
"""
