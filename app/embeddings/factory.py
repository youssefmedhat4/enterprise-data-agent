"""Build the configured embedding gateway.

The cloud-data guard runs here for the same reason it runs for chat models:
embedding schema metadata, metric documents, or question text sends
database-derived content to a third party. A separate code path would have been
a silent loophole, so this reuses `validate_cloud_data_for_models`.
"""

from __future__ import annotations

from app.config import Settings
from app.embeddings.fake import FakeEmbeddingGateway
from app.embeddings.gateway import EmbeddingError, EmbeddingGateway
from app.embeddings.gemini import GeminiEmbeddingGateway


def build_embedding_gateway(settings: Settings) -> EmbeddingGateway:
    if settings.embedding_provider == "fake":
        return FakeEmbeddingGateway(dimension=settings.embedding_dimension)

    model = settings.embedding_model
    # Raises unless ALLOW_CLOUD_DATABASE_DATA approves cloud processing.
    settings.validate_cloud_data_for_models([model])

    if model.startswith("vertex_ai/"):
        # Vertex authenticates with Application Default Credentials, so there is
        # no key to require or to store.
        return GeminiEmbeddingGateway(
            model=model,
            dimension=settings.embedding_dimension,
            timeout_seconds=settings.llm_timeout_seconds,
            vertex_project=settings.vertex_ai_project,
            vertex_location=settings.embedding_vertex_location,
        )

    api_key = settings.gemini_api_key
    if api_key is None or not api_key.get_secret_value():
        raise EmbeddingError(
            "GEMINI_API_KEY is required for the gemini embedding provider."
        )
    return GeminiEmbeddingGateway(
        model=model,
        dimension=settings.embedding_dimension,
        api_key=api_key.get_secret_value(),
        timeout_seconds=settings.llm_timeout_seconds,
    )
