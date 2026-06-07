"""Seed script to populate vector DB with sample data."""
import asyncio
from app.rag.retriever import RAGRetriever


async def seed():
    retriever = RAGRetriever()
    learning_resources = [
        {"content": "Docker Fundamentals: Containers, images, and Dockerfile basics...", "metadata": {"topic": "Docker", "level": "beginner"}},
        {"content": "Kubernetes Core Concepts: Pods, Services, Deployments...", "metadata": {"topic": "Kubernetes", "level": "intermediate"}},
        {"content": "AWS EC2 and S3 Fundamentals for backend developers...", "metadata": {"topic": "AWS", "level": "beginner"}},
    ]
    await retriever.add_documents(learning_resources, "learning_resources")
    print("Seeded learning_resources collection")


if __name__ == "__main__":
    asyncio.run(seed())
