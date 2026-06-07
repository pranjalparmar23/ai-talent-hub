from langchain_community.vectorstores import AzureSearch
from app.rag.embeddings import get_embeddings
import os


def get_vector_store(collection_name: str):
    return AzureSearch(
        azure_search_endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
        azure_search_key=os.getenv("AZURE_SEARCH_KEY"),
        index_name=collection_name,
        embedding_function=get_embeddings().embed_query,
    )
