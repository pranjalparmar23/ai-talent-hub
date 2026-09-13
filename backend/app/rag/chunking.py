"""Text chunking for RAG ingestion.

Splits long documents into overlapping chunks sized for embedding + retrieval.
Chunk size 500 / overlap 50 chosen in Phase 2 eval as the best precision/recall
tradeoff for our seed data.
"""
from langchain_text_splitters import RecursiveCharacterTextSplitter


class TextChunker:
    """Wraps LangChain's RecursiveCharacterTextSplitter with metadata propagation."""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def chunk_text(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []
        return self._splitter.split_text(text)

    def chunk_documents(
        self,
        documents: list[str],
        metadatas: list[dict] | None = None,
    ) -> tuple[list[str], list[dict]]:
        if metadatas is not None and len(documents) != len(metadatas):
            raise ValueError(
                f"Mismatched lengths: {len(documents)} documents vs {len(metadatas)} metadatas"
            )

        all_chunks: list[str] = []
        all_metas: list[dict] = []

        for i, doc in enumerate(documents):
            doc_chunks = self.chunk_text(doc)
            base_meta = dict(metadatas[i]) if metadatas else {}
            for j, chunk in enumerate(doc_chunks):
                chunk_meta = {**base_meta, "chunk_index": j}
                all_chunks.append(chunk)
                all_metas.append(chunk_meta)

        return all_chunks, all_metas