from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from app.llm.prompts.matching_prompts import MATCHING_PROMPT
import json


class MatchingAgent:
    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0)
        self.prompt = ChatPromptTemplate.from_template(MATCHING_PROMPT)

    async def compute_match(self, resume_data: dict, jd_data: dict) -> dict:
        chain = self.prompt | self.llm
        response = await chain.ainvoke({
            "resume": json.dumps(resume_data),
            "jd": json.dumps(jd_data),
        })
        return json.loads(response.content)
