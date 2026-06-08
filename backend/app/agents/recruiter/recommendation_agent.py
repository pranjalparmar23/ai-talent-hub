from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from app.llm.prompts.recommendation_prompts import RECOMMENDATION_PROMPT
import json


class HiringRecommendationAgent:
    def __init__(self):
        self.llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
        self.prompt = ChatPromptTemplate.from_template(RECOMMENDATION_PROMPT)

    async def recommend(self, candidate_data: dict, match_score: float, interview_score: float) -> dict:
        chain = self.prompt | self.llm
        response = await chain.ainvoke({
            "candidate": json.dumps(candidate_data),
            "match_score": match_score,
            "interview_score": interview_score,
        })
        return json.loads(response.content)
