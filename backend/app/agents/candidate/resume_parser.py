from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from app.llm.prompts.resume_prompts import RESUME_PARSE_PROMPT
import json


class ResumeParserAgent:
    def __init__(self):
        self.llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
        self.prompt = ChatPromptTemplate.from_template(RESUME_PARSE_PROMPT)

    async def parse(self, raw_text: str) -> dict:
        chain = self.prompt | self.llm
        response = await chain.ainvoke({"resume_text": raw_text})
        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            return {"error": "Failed to parse resume", "raw": response.content}
