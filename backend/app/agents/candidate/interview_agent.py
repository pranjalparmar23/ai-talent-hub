from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from app.llm.prompts.interview_prompts import INTERVIEW_PROMPT, FOLLOWUP_PROMPT
from app.rag.retriever import RAGRetriever
import json


class InterviewAgent:
    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.5)
        self.retriever = RAGRetriever()

    async def start_session(self, company: str, role: str) -> dict:
        experiences = await self.retriever.retrieve(
            query=f"{company} {role} interview questions",
            collection="interview_experiences",
        )
        prompt = ChatPromptTemplate.from_template(INTERVIEW_PROMPT)
        chain = prompt | self.llm
        response = await chain.ainvoke({
            "company": company,
            "role": role,
            "experiences": json.dumps(experiences),
        })
        return {"question": response.content, "session_started": True}

    async def followup(self, conversation_history: list, candidate_response: str) -> str:
        prompt = ChatPromptTemplate.from_template(FOLLOWUP_PROMPT)
        chain = prompt | self.llm
        response = await chain.ainvoke({
            "history": json.dumps(conversation_history),
            "candidate_response": candidate_response,
        })
        return response.content
