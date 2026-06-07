from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
import os


def get_openai_llm(temperature: float = 0):
    return ChatOpenAI(model="gpt-4o", temperature=temperature, openai_api_key=os.getenv("OPENAI_API_KEY"))


def get_claude_llm(temperature: float = 0):
    return ChatAnthropic(model="claude-sonnet-4-20250514", temperature=temperature,
                         anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"))
