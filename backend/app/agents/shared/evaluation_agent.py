from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from app.llm.prompts.evaluation_prompts import EVALUATION_PROMPT
import json


class EvaluationAgent:
    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0)
        self.prompt = ChatPromptTemplate.from_template(EVALUATION_PROMPT)

    async def evaluate_interview(self, conversation_history: list) -> dict:
        chain = self.prompt | self.llm
        response = await chain.ainvoke({"history": json.dumps(conversation_history)})
        return json.loads(response.content)
