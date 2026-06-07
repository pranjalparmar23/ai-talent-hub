from app.agents.candidate.interview_agent import InterviewAgent
from app.agents.shared.evaluation_agent import EvaluationAgent
from app.agents.shared.memory_agent import MemoryAgent
from app.database.postgres import AsyncSessionLocal
from app.database.models import InterviewSession
import uuid


class InterviewService:
    def __init__(self):
        self.interviewer = InterviewAgent()
        self.evaluator = EvaluationAgent()

    async def start_session(self, company: str, role: str, user_id: str) -> dict:
        session_id = str(uuid.uuid4())
        result = await self.interviewer.start_session(company, role)
        async with AsyncSessionLocal() as db:
            session = InterviewSession(id=session_id, user_id=user_id, company=company, role=role, history=[])
            db.add(session)
            await db.commit()
        memory = MemoryAgent(session_id)
        await memory.add_ai_message(result["question"])
        return {"session_id": session_id, "question": result["question"]}

    async def process_response(self, session_id: str, response: str) -> dict:
        memory = MemoryAgent(session_id)
        await memory.add_user_message(response)
        history = await memory.get_history()
        followup = await self.interviewer.followup(history, response)
        await memory.add_ai_message(followup)
        return {"question": followup}

    async def generate_feedback(self, session_id: str) -> dict:
        memory = MemoryAgent(session_id)
        history = await memory.get_history()
        return await self.evaluator.evaluate_interview(history)
