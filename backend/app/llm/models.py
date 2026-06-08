import os

from langchain_groq import ChatGroq


def get_groq_llm(temperature: float = 0):
    return ChatGroq(
        model="llama-3.3-70b-versatile
",
        temperature=temperature,
        groq_api_key=os.getenv("GROQ_API_KEY")
    )

