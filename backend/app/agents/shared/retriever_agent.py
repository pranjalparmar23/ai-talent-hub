from app.rag.retriever import RAGRetriever


class RetrieverAgent:
    def __init__(self):
        self.retriever = RAGRetriever()

    async def retrieve(self, query: str, collection: str, top_k: int = 5) -> list:
        return await self.retriever.retrieve(query=query, collection=collection, top_k=top_k)
