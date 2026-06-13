"""Text chunking utility.

Long documents (resumes, JDs, interview write-ups) need to be split into
smaller chunks before embedding. Two reasons:
  1. The embedding model has a context limit (~512 tokens for MiniLM)
  2. Smaller chunks = more precise retrieval (you find the relevant
     paragraph, not the whole document)
"""
import logging
from typing import Optional
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# Tuned for MiniLM's 512-token limit (~2000 chars at avg English density)
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50


class TextChunker:
    """Splits long text into overlapping chunks suitable for embedding."""

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ):
        """
        Args:
            chunk_size: Max characters per chunk. Default 500.
            chunk_overlap: Characters shared between consecutive chunks. Default 50.
                Overlap prevents splitting a key sentence right at a boundary.
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            # Try splitting on paragraph → sentence → word, in that order.
            # Each fallback is rougher; we only split mid-word if nothing else fits.
            separators=["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ", ""],
        )

    def chunk_text(self, text: str) -> list[str]:
        """Split a single document into chunks. Returns a list of strings."""
        if not text or not text.strip():
            return []
        chunks = self._splitter.split_text(text)
        logger.debug(f"Split {len(text)} chars into {len(chunks)} chunks")
        return chunks

    def chunk_documents(
        self,
        documents: list[str],
        metadatas: Optional[list[dict]] = None,
    ) -> tuple[list[str], list[dict]]:
        """Chunk a list of documents, propagating metadata to each chunk.

        Args:
            documents: List of document strings.
            metadatas: Optional list of metadata dicts (one per document).
                Each chunk inherits its parent's metadata plus `chunk_index`.

        Returns:
            (all_chunks, all_metadatas) — flat lists, same length, aligned.
        """
        if metadatas is not None and len(metadatas) != len(documents):
            raise ValueError(
                f"Mismatched lengths: {len(documents)} documents, {len(metadatas)} metadatas"
            )

        all_chunks: list[str] = []
        all_metadatas: list[dict] = []

        for i, doc in enumerate(documents):
            chunks = self.chunk_text(doc)
            base_meta = metadatas[i] if metadatas else {}
            for chunk_idx, chunk in enumerate(chunks):
                all_chunks.append(chunk)
                all_metadatas.append({**base_meta, "chunk_index": chunk_idx})

        return all_chunks, all_metadatas