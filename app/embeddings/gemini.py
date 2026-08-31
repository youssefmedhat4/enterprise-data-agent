"""Gemini embedding gateway.

Uses the same LiteLLM indirection as chat models, so the API key stays in
settings and never reaches a caller. Text sent here is database-derived, so the
cloud-data guard applies exactly as it does to chat completions — there is no
embeddings loophole.
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
        api_key: str,
        timeout_seconds: float,
    ) -> None:
        self._model = model
        self._dimension = dimension
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    @property
    def provider(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[EmbeddingVector]:
        if not texts:
            return []
        # Imported lazily: importing litellm at module scope runs its
        # load_dotenv(), which mutates os.environ for the whole process.
        from litellm import aembedding

        try:
            response = await aembedding(
                model=self._model,
                input=texts,
                api_key=self._api_key,
                # Gemini Embedding 2 is a Matryoshka model: it is trained so a
                # truncated prefix remains a valid embedding, which is why a
                # smaller dimension can be requested rather than post-truncated.
                dimensions=self._dimension,
                timeout=self._timeout_seconds,
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
