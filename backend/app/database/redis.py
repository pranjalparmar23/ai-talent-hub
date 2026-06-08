import os
import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Singleton client — one connection pool shared by all requests
redis_client = redis.from_url(
    REDIS_URL,
    decode_responses=True,
    encoding="utf-8",
)


async def get_redis():
    """FastAPI dependency for Redis client."""
    return redis_client


async def close_redis():
    """Cleanup on app shutdown."""
    await redis_client.aclose()