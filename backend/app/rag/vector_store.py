from langchain_chroma import Chroma
from app.rag.embeddings import get_embeddings
from app.rag.embeddings import get_embeddings
import os


def get_vector_store(collection_name: str):
    return Chroma(
        collection_name=collection_name,
        embedding_function=get_embeddings(),
        persist_directory="./chroma_db"
    )
