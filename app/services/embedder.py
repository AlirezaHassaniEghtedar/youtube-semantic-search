import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import settings

EMBEDDING_DIM = 384


class EmbedderService:
    def __init__(self) -> None:
        self._model: SentenceTransformer | None = None

    def load(self) -> None:
        self._model = SentenceTransformer(settings.EMBEDDING_MODEL)

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            raise RuntimeError("Embedder model not loaded")
        return self._model

    def embed_text(self, text: str) -> np.ndarray:
        vec = self.model.encode(text, convert_to_numpy=True, show_progress_bar=False)
        vec = vec.astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        vecs = self.model.encode(
            texts, convert_to_numpy=True, show_progress_bar=False, batch_size=32
        )
        vecs = vecs.astype(np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms = np.where(norms > 0, norms, 1.0)
        vecs = vecs / norms
        return vecs


def serialize_embedding(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def deserialize_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()
