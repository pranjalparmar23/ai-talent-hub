from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from app.llm.prompts.question_prompts import QUESTION_GEN_PROMPT
import json


class QuestionGenerationAgent:
    def __init__(self):
        self.llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0.4)
        self.prompt = ChatPromptTemplate.from_template(QUESTION_GEN_PROMPT)

    async def generate(self, jd_data: dict, resume_data: dict, skill_gaps: list) -> list:
        chain = self.prompt | self.llm
        response = await chain.ainvoke({
            "jd": json.dumps(jd_data),
            "resume": json.dumps(resume_data),
            "skill_gaps": json.dumps(skill_gaps),
        })
        return json.loads(response.content)
