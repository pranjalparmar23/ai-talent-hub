"""Local embedding generation via sentence-transformers.

Uses all-MiniLM-L6-v2 (384-dim) — small, fast, runs on CPU, no API key needed.
This is a deliberate choice: no OpenAI/Azure dependency, everything local/free.
"""

import logging
import threading

from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


class EmbeddingService:
    """Lazy-loaded singleton wrapper around the sentence-transformers model."""

    _model: SentenceTransformer | None = None
    _lock = threading.Lock()

    @classmethod
    def _get_model(cls) -> SentenceTransformer:
        if cls._model is None:
            with cls._lock:
                if cls._model is None:
                    logger.info(f"Loading embedding model: {EMBEDDING_MODEL_NAME}")
                    cls._model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        return cls._model

    @classmethod
    def embed_text(cls, text: str) -> list[float]:
        if not text or not text.strip():
            raise ValueError("Cannot embed empty text")
        model = cls._get_model()
        vector = model.encode(text, convert_to_numpy=True)
        return vector.tolist()

    @classmethod
    def embed_batch(cls, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        cleaned = [t for t in texts if t and t.strip()]
        if not cleaned:
            raise ValueError("Cannot embed a batch of all-empty texts")
        model = cls._get_model()
        vectors = model.encode(cleaned, convert_to_numpy=True, batch_size=32)
        return [v.tolist() for v in vectors]

    @classmethod
    def embedding_dimension(cls) -> int:
        return EMBEDDING_DIM
