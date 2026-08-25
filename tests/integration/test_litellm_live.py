import pytest

from app.config import Settings
from app.llm.factory import build_llm_gateway
from app.llm.gateway import SQLGeneration


@pytest.mark.cloud
@pytest.mark.asyncio
async def test_live_gemini_litellm_structured_output() -> None:
    settings = Settings()
    if not settings.run_cloud_llm_tests:
        pytest.skip("Set RUN_CLOUD_LLM_TESTS=1 to allow a live cloud LLM request")
    if settings.llm_provider != "litellm" or not settings.llm_model_sql_reasoner.startswith(
        "gemini/"
    ):
        pytest.skip("Configure the sql-reasoner alias with a Gemini LiteLLM model")
    if settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value():
        pytest.skip("GEMINI_API_KEY is not configured")

    gateway = build_llm_gateway(settings)
    result = await gateway.generate_structured(
        model_alias="sql-reasoner",
        system="Return one safe PostgreSQL SELECT statement as structured output.",
        user="Select the id column from analytics.departments with a limit of 1.",
        response_model=SQLGeneration,
    )

    assert result.sql is not None
    assert result.sql.strip().lower().startswith("select")
