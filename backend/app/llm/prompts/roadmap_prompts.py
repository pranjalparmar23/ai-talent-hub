ROADMAP_PROMPT = """
Create a 4-8 week learning roadmap for a candidate targeting: {target_role}.
Skills to learn: {skill_gaps}
Available resources: {resources}
Return ONLY valid JSON with: weeks (list of {{week, topic, resources, tasks}}).
"""
