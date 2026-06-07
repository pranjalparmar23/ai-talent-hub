from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from app.llm.prompts.skill_prompts import SKILL_GAP_PROMPT
import json


class SkillGapAgent:
    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0)
        self.prompt = ChatPromptTemplate.from_template(SKILL_GAP_PROMPT)

    async def analyze(self, candidate_skills: list, jd_skills: list) -> dict:
        chain = self.prompt | self.llm
        response = await chain.ainvoke({
            "candidate_skills": json.dumps(candidate_skills),
            "jd_skills": json.dumps(jd_skills),
        })
        return json.loads(response.content)
