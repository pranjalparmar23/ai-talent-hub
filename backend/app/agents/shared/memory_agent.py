from langchain_community.chat_message_histories import RedisChatMessageHistory
from langchain_core.messages import HumanMessage, AIMessage
import os


class MemoryAgent:
    def __init__(self, session_id: str):
        self.history = RedisChatMessageHistory(
            session_id=session_id,
            url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        )

    async def add_user_message(self, message: str):
        self.history.add_user_message(message)

    async def add_ai_message(self, message: str):
        self.history.add_ai_message(message)

    async def get_history(self) -> list:
        return [
            {"role": "human" if isinstance(m, HumanMessage) else "ai", "content": m.content}
            for m in self.history.messages
        ]

    async def clear(self):
        self.history.clear()
