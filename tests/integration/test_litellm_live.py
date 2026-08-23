import os

import pytest

from app.config import Settings
from app.llm.gateway import SQLGeneration
from app.llm.litellm_gateway import LiteLLMGateway


@pytest.mark.cloud
@pytest.mark.asyncio
async def test_live_litellm_structured_output() -> None:
    if os.getenv("RUN_CLOUD_LLM_TESTS") != "1":
        pytest.skip("Set RUN_CLOUD_LLM_TESTS=1 to allow a paid cloud LLM request")

    settings = Settings()
    if settings.llm_provider != "litellm" or settings.llm_model_sql_reasoner.startswith("fake/"):
        pytest.skip("Configure LLM_PROVIDER and LLM_MODEL_SQL_REASONER for a cloud model")
    credential_names = (
        "ANTHROPIC_API_KEY",
        "AZURE_API_KEY",
        "COHERE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "MISTRAL_API_KEY",
        "OPENAI_API_KEY",
    )
    if not any(os.getenv(name) for name in credential_names):
        pytest.skip("No supported cloud LLM credential is configured")

    gateway = LiteLLMGateway(
        settings.model_aliases,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
    result = await gateway.generate_structured(
        model_alias="sql-reasoner",
        system="Return one safe PostgreSQL SELECT statement as structured output.",
        user="Select the id column from analytics.departments with a limit of 1.",
        response_model=SQLGeneration,
    )

    assert result.sql is not None
    assert result.sql.strip().lower().startswith("select")
