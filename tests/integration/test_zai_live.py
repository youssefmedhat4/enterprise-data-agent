import json
from time import perf_counter

import pytest

from app.config import Settings
from app.llm.factory import build_llm_gateway
from app.llm.gateway import LLMGatewayError, LLMGatewayWithUsage, SQLGeneration
from app.security.sql_validation import SQLValidator

ZAI_GLM_FLASH_MODEL = "zai/glm-4.5-flash"


@pytest.mark.cloud
@pytest.mark.asyncio
async def test_live_zai_structured_sql() -> None:
    settings = Settings()
    if not settings.run_cloud_llm_tests:
        pytest.skip("Set RUN_CLOUD_LLM_TESTS=1 to allow a live Z.ai request")
    if settings.llm_provider != "litellm":
        pytest.skip("Configure LLM_PROVIDER=litellm for the Z.ai smoke test")
    assert set(settings.model_aliases.values()) == {ZAI_GLM_FLASH_MODEL}
    if settings.zai_api_key is None or not settings.zai_api_key.get_secret_value():
        pytest.skip("ZAI_API_KEY is not configured")
    assert settings.api_bases_by_alias == {}

    gateway = build_llm_gateway(settings)
    started_at = perf_counter()
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
            f"Z.ai smoke infrastructure error: {category} "
            f"(HTTP {status or 'unknown'}, code {code or 'unknown'}).",
            pytrace=False,
        )
    latency_ms = round((perf_counter() - started_at) * 1000, 3)

    assert result.sql is not None
    validated_sql = SQLValidator().validate_readonly(result.sql)
    assert isinstance(gateway, LLMGatewayWithUsage)
    usage = gateway.usage_snapshot()
    assert usage.call_count == 1
    assert usage.provider_calls == {"zai": 1}
    assert any("glm-4.5-flash" in model for model in usage.model_calls)
    print(
        json.dumps(
            {
                "provider": "zai",
                "configured_model": ZAI_GLM_FLASH_MODEL,
                "reported_models": usage.model_calls,
                "latency_ms": latency_ms,
                "prompt_tokens": usage.prompt_tokens if usage.usage_available_calls else None,
                "completion_tokens": (
                    usage.completion_tokens if usage.usage_available_calls else None
                ),
                "total_tokens": usage.total_tokens if usage.usage_available_calls else None,
                "structured_sql": validated_sql,
            },
            ensure_ascii=False,
        )
    )
