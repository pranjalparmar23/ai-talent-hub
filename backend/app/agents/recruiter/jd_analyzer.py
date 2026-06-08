from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from app.llm.prompts.jd_prompts import JD_ANALYSIS_PROMPT
import json


class JDAnalyzerAgent:
    def __init__(self):
        self.llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
        self.prompt = ChatPromptTemplate.from_template(JD_ANALYSIS_PROMPT)

    async def analyze(self, jd_text: str) -> dict:
        chain = self.prompt | self.llm
        response = await chain.ainvoke({"jd_text": jd_text})
        return json.loads(response.content)
