"""Deterministic embedding gateway for tests.

Produces stable, normalized vectors from a hash of the input, so similarity is
reproducible without a network call. Related texts do not become similar by
magic — tests that need a specific ordering should seed the texts they compare.
"""

from __future__ import annotations

import hashlib
import math

from app.embeddings.gateway import EmbeddingVector


class FakeEmbeddingGateway:
    def __init__(
        self,
        *,
        provider: str = "fake",
        model: str = "fake-embedding",
        dimension: int = 768,
    ) -> None:
        self._provider = provider
        self._model = model
        self._dimension = dimension
        self.calls: list[list[str]] = []

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[EmbeddingVector]:
        self.calls.append(list(texts))
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> EmbeddingVector:
        # Expand a digest to the requested width, then L2-normalize so cosine
        # similarity behaves like it would with a real model.
        raw: list[float] = []
        counter = 0
        while len(raw) < self._dimension:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            raw.extend(byte / 255.0 - 0.5 for byte in digest)
            counter += 1
        values = raw[: self._dimension]
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return EmbeddingVector(
            provider=self._provider,
            model=self._model,
            dimension=self._dimension,
            values=tuple(value / norm for value in values),
        )
