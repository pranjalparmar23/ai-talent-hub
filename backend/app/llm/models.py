"""Central LLM factory. Every agent MUST get its LLM from here — one source of truth
for model choice, temperature defaults, and API key handling.
"""

import os
from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()

DEFAULT_MODEL = "openai/gpt-oss-120b"


def get_llm(
    temperature: float = 0, model: str | None = None, max_tokens: int | None = None
) -> ChatGroq:
    """Return a configured Groq chat model.

    Args:
        temperature: 0 for deterministic tasks (parsing, scoring), 0.3-0.7 for creative
                     tasks (roadmap generation, question generation).
        model: Override the model name. Defaults to GROQ_MODEL env var or the default.
        max_tokens: Cap on output tokens. None uses the provider default — set this
                    explicitly for prompts producing large structured JSON (roadmap).
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set — check backend/.env")

    return ChatGroq(
        model=model or os.getenv("GROQ_MODEL", DEFAULT_MODEL),
        temperature=temperature,
        api_key=api_key,
        max_tokens=max_tokens,
    )


# Kept as a compatibility alias in case anything still imports the old name.
# Delete this once all agents are refactored.
def get_groq_llm(temperature: float = 0) -> ChatGroq:
    return get_llm(temperature)
