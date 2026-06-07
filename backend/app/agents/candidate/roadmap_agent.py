from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from app.llm.prompts.roadmap_prompts import ROADMAP_PROMPT
from app.rag.retriever import RAGRetriever
import json


class RoadmapAgent:
    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.3)
        self.prompt = ChatPromptTemplate.from_template(ROADMAP_PROMPT)
        self.retriever = RAGRetriever()

    async def generate(self, skill_gaps: list, target_role: str) -> dict:
        resources = await self.retriever.retrieve(
            query=f"learning roadmap for {', '.join(skill_gaps)}",
            collection="learning_resources",
        )
        chain = self.prompt | self.llm
        response = await chain.ainvoke({
            "skill_gaps": json.dumps(skill_gaps),
            "target_role": target_role,
            "resources": json.dumps(resources),
        })
        return json.loads(response.content)
