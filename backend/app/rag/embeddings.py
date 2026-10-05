"""HuggingFace sentence-transformer embeddings — MODULE 18.

Uses sentence-transformers/all-MiniLM-L6-v2 by default (configurable).
Falls back to a fixed zero-vector mock when the model can't be loaded
(CI / environments without the HuggingFace package).
"""

from typing import List
from backend.app.core.config import settings
from backend.app.core.logging import logger


class EmbeddingProvider:
    """Lazy-loading sentence-transformer embedding provider."""

    def __init__(self) -> None:
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(settings.EMBEDDING_MODEL)
            logger.info("Embedding model loaded.", extra={"model": settings.EMBEDDING_MODEL})
        except Exception as exc:
            logger.warning(
                "Embedding model unavailable — using mock zero vectors.",
                extra={"error": str(exc)},
            )
            self._model = None
        return self._model

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Return list of embedding vectors, one per text."""
        model = self._load()
        if model is None:
            # Mock: 384-dim zero vector (matches all-MiniLM-L6-v2 dim)
            return [[0.0] * 384 for _ in texts]
        return model.encode(texts, show_progress_bar=False).tolist()

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]


embedding_provider = EmbeddingProvider()
