from app.rag.vector_store import get_vector_store


class RAGRetriever:
    async def retrieve(self, query: str, collection: str, top_k: int = 5) -> list:
        store = get_vector_store(collection)
        docs = store.similarity_search(query, k=top_k)
        return [{"content": d.page_content, "metadata": d.metadata} for d in docs]

    async def add_documents(self, documents: list, collection: str):
        store = get_vector_store(collection)
        store.add_texts(
            texts=[d["content"] for d in documents],
            metadatas=[d.get("metadata", {}) for d in documents],
        )
