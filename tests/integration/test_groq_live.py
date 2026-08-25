import pytest

from app.config import Settings
from app.llm.factory import build_llm_gateway
from app.llm.gateway import LLMGatewayWithUsage, SQLGeneration
from app.security.sql_validation import SQLValidator

GROQ_QWEN_MODEL = "groq/qwen/qwen3.6-27b"


@pytest.mark.cloud
@pytest.mark.asyncio
async def test_live_groq_qwen_structured_sql() -> None:
    settings = Settings()
    if not settings.run_cloud_llm_tests:
        pytest.skip("Set RUN_CLOUD_LLM_TESTS=1 to allow a live Groq request")
    if settings.llm_provider != "litellm":
        pytest.skip("Configure LLM_PROVIDER=litellm for the Groq smoke test")
    assert set(settings.model_aliases.values()) == {GROQ_QWEN_MODEL}
    if settings.groq_api_key is None or not settings.groq_api_key.get_secret_value():
        pytest.skip("GROQ_API_KEY is not configured")
    assert settings.api_bases_by_alias == {}

    gateway = build_llm_gateway(settings)
    result = await gateway.generate_structured(
        model_alias="sql-reasoner",
        system=(
            "Generate exactly one safe, read-only PostgreSQL SELECT statement as structured output."
        ),
        user=(
            "Schema: analytics.departments(id, name). "
            "Select department id and name, ordered by name."
        ),
        response_model=SQLGeneration,
    )

    assert result.sql is not None
    SQLValidator().validate_readonly(result.sql)
    assert isinstance(gateway, LLMGatewayWithUsage)
    usage = gateway.usage_snapshot()
    assert usage.call_count == 1
    assert usage.provider_calls == {"groq": 1}
    assert any("qwen/qwen3.6-27b" in model for model in usage.model_calls)
