from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from app.llm.prompts.ats_prompts import ATS_CHECK_PROMPT
import json


class ATSAgent:
    def __init__(self):
        self.llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
        self.prompt = ChatPromptTemplate.from_template(ATS_CHECK_PROMPT)

    async def analyze(self, resume_data: dict, jd_data: dict) -> dict:
        chain = self.prompt | self.llm
        response = await chain.ainvoke({
            "resume": json.dumps(resume_data),
            "job_description": json.dumps(jd_data),
        })
        return json.loads(response.content)
