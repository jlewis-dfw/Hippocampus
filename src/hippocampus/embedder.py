"""bge-small-en-v1.5 embedder (HIPPO-E02-F2-S01): FastEmbed/ONNX, CPU only."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FastEmbedder:
    """Lazy wrapper; the model downloads on first use. Never touches the GPU."""

    def __init__(self, model_name: str = MODEL_NAME, cache_dir: str | None = None):
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                model_name=self._model_name,
                cache_dir=self._cache_dir,
                providers=["CPUExecutionProvider"],
            )
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = [[float(x) for x in v] for v in self._load().embed(list(texts))]
        for v in vectors:
            if len(v) != DIMENSIONS:
                raise ValueError(f"expected {DIMENSIONS}-dim vector, got {len(v)}")
        return vectors
