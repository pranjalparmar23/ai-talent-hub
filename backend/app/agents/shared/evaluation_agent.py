from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from app.llm.prompts.evaluation_prompts import EVALUATION_PROMPT
import json


class EvaluationAgent:
    def __init__(self):
        self.llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
        self.prompt = ChatPromptTemplate.from_template(EVALUATION_PROMPT)

    async def evaluate_interview(self, conversation_history: list) -> dict:
        chain = self.prompt | self.llm
        response = await chain.ainvoke({"history": json.dumps(conversation_history)})
        return json.loads(response.content)
