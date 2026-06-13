"""Local embedding service using sentence-transformers.

Wraps the `all-MiniLM-L6-v2` model — a small, fast, 384-dim model that runs
on CPU. No API key, no network calls at runtime (only on first download).
"""
import logging
from typing import Optional
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


class EmbeddingService:
    """Singleton-style wrapper around sentence-transformers.

    The model is lazy-loaded on first call — loading takes ~2s and ~200MB RAM,
    so we only pay that cost when embeddings are actually needed.
    """

    _model: Optional[SentenceTransformer] = None

    @classmethod
    def get_model(cls) -> SentenceTransformer:
        """Lazy-init the embedding model (one per process)."""
        if cls._model is None:
            logger.info(f"Loading embedding model: {MODEL_NAME}")
            cls._model = SentenceTransformer(MODEL_NAME)
            logger.info(f"Model loaded — embedding dim: {EMBEDDING_DIM}")
        return cls._model

    @classmethod
    def embed_text(cls, text: str) -> list[float]:
        """Embed a single text string. Returns a 384-dim vector."""
        if not text or not text.strip():
            raise ValueError("Cannot embed empty text")
        model = cls.get_model()
        # convert_to_numpy=False returns a plain Python list which is what Chroma wants
        embedding = model.encode(text, convert_to_numpy=True).tolist()
        return embedding

    @classmethod
    def embed_batch(cls, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts at once — much faster than calling embed_text in a loop."""
        if not texts:
            return []
        # Filter out empty strings so the model doesn't choke
        clean_texts = [t for t in texts if t and t.strip()]
        if not clean_texts:
            raise ValueError("All input texts were empty")

        model = cls.get_model()
        embeddings = model.encode(
            clean_texts,
            convert_to_numpy=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return embeddings.tolist()

    @classmethod
    def embedding_dimension(cls) -> int:
        """Return the dimensionality of vectors this service produces."""
        return EMBEDDING_DIM