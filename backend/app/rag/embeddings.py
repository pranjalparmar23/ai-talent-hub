from langchain_openai import OpenAIEmbeddings
import os


def get_embeddings():
    return OpenAIEmbeddings(
        model="text-embedding-3-large",
        openai_api_key=os.getenv("OPENAI_API_KEY"),
    )
