import pytest

from app.config import Settings
from app.llm.factory import build_llm_gateway
from app.llm.gateway import LLMGatewayError, LLMGatewayWithUsage, SQLGeneration
from app.security.sql_validation import SQLValidator

CEREBRAS_GPT_OSS_MODEL = "cerebras/gpt-oss-120b"


@pytest.mark.cloud
@pytest.mark.asyncio
async def test_live_cerebras_structured_sql() -> None:
    settings = Settings()
    if not settings.run_cloud_llm_tests:
        pytest.skip("Set RUN_CLOUD_LLM_TESTS=1 to allow a live Cerebras request")
    if settings.llm_provider != "litellm":
        pytest.skip("Configure LLM_PROVIDER=litellm for the Cerebras smoke test")
    assert set(settings.model_aliases.values()) == {CEREBRAS_GPT_OSS_MODEL}
    if settings.cerebras_api_key is None or not settings.cerebras_api_key.get_secret_value():
        pytest.skip("CEREBRAS_API_KEY is not configured")
    assert settings.api_bases_by_alias == {}

    gateway = build_llm_gateway(settings)
    try:
        result = await gateway.generate_structured(
            model_alias="sql-reasoner",
            system=(
                "Generate exactly one safe, read-only PostgreSQL SELECT statement as "
                "structured output."
            ),
            user=(
                "Schema: analytics.departments(id, name). "
                "Select department id and name, ordered by name."
            ),
            response_model=SQLGeneration,
        )
    except LLMGatewayError as exc:
        detail = exc.provider_error
        category = detail.category if detail is not None else "unknown"
        status = detail.http_status if detail is not None else None
        code = detail.provider_code if detail is not None else None
        pytest.fail(
            f"Cerebras smoke infrastructure error: {category} "
            f"(HTTP {status or 'unknown'}, code {code or 'unknown'}).",
            pytrace=False,
        )

    assert result.sql is not None
    SQLValidator().validate_readonly(result.sql)
    assert isinstance(gateway, LLMGatewayWithUsage)
    usage = gateway.usage_snapshot()
    assert usage.call_count == 1
    assert usage.provider_calls == {"cerebras": 1}
    assert any("gpt-oss-120b" in model for model in usage.model_calls)
