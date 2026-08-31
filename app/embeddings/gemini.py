"""Gemini embedding gateway.

Uses the same LiteLLM indirection as chat models. On the Vertex path there is
no key at all: Application Default Credentials authenticate the process, so
nothing secret passes through this class or reaches a caller. The Developer API
path remains supported for a deployment still configured with a key.

Text sent here is database-derived, so the cloud-data guard applies exactly as
it does to chat completions — there is no embeddings loophole.

Provider identity is recorded on every vector because a Vertex vector and a
Developer-API vector are not interchangeable even at the same dimension. Storing
which produced a vector is what lets incompatible rows be found and reindexed
rather than silently compared.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from app.embeddings.gateway import (
    EmbeddingError,
    EmbeddingProviderUnavailableError,
    EmbeddingVector,
)

logger = logging.getLogger(__name__)


class GeminiEmbeddingGateway:
    def __init__(
        self,
        *,
        model: str,
        dimension: int,
        timeout_seconds: float,
        api_key: str | None = None,
        vertex_project: str | None = None,
        vertex_location: str | None = None,
    ) -> None:
        self._model = model
        self._dimension = dimension
        self._api_key = api_key
        self._vertex_project = vertex_project
        self._vertex_location = vertex_location
        self._timeout_seconds = timeout_seconds

    @property
    def is_vertex(self) -> bool:
        return self._model.startswith("vertex_ai/")

    @property
    def provider(self) -> str:
        """Recorded with every vector, so incompatible ones stay distinguishable."""
        return "vertex_ai" if self.is_vertex else "gemini"

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def _auth_options(self) -> dict[str, Any]:
        """Credentials for the configured path.

        Vertex sends project and location and relies on ADC; the Developer API
        sends a key. Never both, so a stray key cannot silently take over a
        deployment that was configured for Vertex.
        """
        if self.is_vertex:
            return {
                "vertex_project": self._vertex_project,
                "vertex_location": self._vertex_location,
            }
        return {"api_key": self._api_key}

    async def embed(self, texts: list[str]) -> list[EmbeddingVector]:
        if not texts:
            return []
        # Vertex serves gemini-embedding-2 one input per request and silently
        # returns a single vector for a batch, so batching happens here rather
        # than trusting the provider to preserve arity.
        batches = [[text] for text in texts] if self.is_vertex else [texts]

        vectors: list[EmbeddingVector] = []
        for batch in batches:
            vectors.extend(await self._embed_batch(batch))
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[EmbeddingVector]:
        # Imported lazily: importing litellm at module scope runs its
        # load_dotenv(), which mutates os.environ for the whole process.
        from litellm import aembedding

        try:
            response = await aembedding(
                model=self._model,
                input=texts,
                # Gemini Embedding 2 is a Matryoshka model: it is trained so a
                # truncated prefix remains a valid embedding, which is why a
                # smaller dimension can be requested rather than post-truncated.
                dimensions=self._dimension,
                timeout=self._timeout_seconds,
                **self._auth_options(),
            )
        except Exception as exc:
            # The message may echo the request; never log it, and never let the
            # key reach the raised error.
            logger.warning(
                "embedding request failed provider=%s model=%s batch=%d",
                self.provider,
                self._model,
                len(texts),
            )
            raise EmbeddingProviderUnavailableError(
                "The configured embedding provider is unavailable."
            ) from exc

        data = cast(list[dict[str, Any]], response["data"])
        if len(data) != len(texts):
            raise EmbeddingError(
                f"Embedding provider returned {len(data)} vectors for "
                f"{len(texts)} inputs."
            )
        return [
            EmbeddingVector(
                provider=self.provider,
                model=self._model,
                dimension=self._dimension,
                values=tuple(float(value) for value in item["embedding"]),
            )
            for item in data
        ]
