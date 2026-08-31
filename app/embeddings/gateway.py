"""Provider-neutral embedding abstraction.

Embeddings are stored alongside the provider, model, and dimension that produced
them so vectors from different models are never compared. `EmbeddingVector`
carries that provenance with the values, which makes a mismatch a type-level
concern rather than a silent similarity bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class EmbeddingError(RuntimeError):
    """Raised when embeddings cannot be produced."""


class EmbeddingProviderUnavailableError(EmbeddingError):
    """Raised when the configured embedding provider is unreachable."""


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    provider: str
    model: str
    dimension: int
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.values) != self.dimension:
            raise EmbeddingError(
                f"Embedding declares dimension {self.dimension} but carries "
                f"{len(self.values)} values."
            )

    @property
    def signature(self) -> tuple[str, str, int]:
        """Identity that two vectors must share before they may be compared."""
        return (self.provider, self.model, self.dimension)

    def assert_comparable(self, other: EmbeddingVector) -> None:
        if self.signature != other.signature:
            raise EmbeddingError(
                "Refusing to compare embeddings from different models: "
                f"{self.signature} vs {other.signature}."
            )


@runtime_checkable
class EmbeddingGateway(Protocol):
    """Turns text into vectors. Implementations must not log the input text."""

    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[EmbeddingVector]:
        """Embed a batch of documents, preserving order."""
        ...
