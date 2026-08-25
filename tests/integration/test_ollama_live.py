import httpx
import pytest

from app.config import Settings
from app.llm.factory import build_llm_gateway
from app.llm.gateway import (
    LLMConnectionError,
    LLMGatewayWithUsage,
    LLMModelUnavailableError,
    LLMOutOfMemoryError,
    LLMTimeoutError,
    SQLGeneration,
)
from app.security.sql_validation import SQLValidator

EXPECTED_MODEL_TAG = "qwen3.5:9b"
EXPECTED_LITELLM_MODEL = f"ollama_chat/{EXPECTED_MODEL_TAG}"


@pytest.mark.local_llm
@pytest.mark.asyncio
async def test_local_qwen_generates_safe_structured_sql() -> None:
    settings = Settings()
    if not settings.run_local_llm_tests:
        pytest.skip("RUN_LOCAL_LLM_TESTS=1 is required for a genuine local model call")
    assert settings.llm_provider == "litellm"
    configured_models = set(settings.model_aliases.values())
    assert configured_models == {EXPECTED_LITELLM_MODEL}

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{settings.ollama_api_base.rstrip('/')}/api/tags")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"Ollama is unavailable: {type(exc).__name__}")

    installed = {
        str(item.get("name"))
        for item in response.json().get("models", [])
        if isinstance(item, dict)
    }
    physical_tags = {model.removeprefix("ollama_chat/") for model in configured_models}
    if not physical_tags.issubset(installed):
        pytest.skip(f"Configured Qwen tag is not installed: {sorted(physical_tags - installed)}")

    gateway = build_llm_gateway(settings)
    try:
        generated = await gateway.generate_structured(
            model_alias="sql-reasoner",
            system=(
                "Generate exactly one read-only PostgreSQL SELECT statement. Return structured "
                "output only. Never mutate data."
            ),
            user=(
                "Schema: analytics.departments(id, name), "
                "analytics.employees(id, department_id, salary). "
                "Question: Show each department and its employee count."
            ),
            response_model=SQLGeneration,
        )
    except (
        LLMConnectionError,
        LLMModelUnavailableError,
        LLMOutOfMemoryError,
        LLMTimeoutError,
    ) as exc:
        pytest.skip(f"Local model infrastructure unavailable: {type(exc).__name__}")

    assert generated.sql is not None
    SQLValidator().validate_readonly(generated.sql)
    assert "departments" in generated.sql.casefold()
    assert "employees" in generated.sql.casefold()
    assert isinstance(gateway, LLMGatewayWithUsage)
    usage = gateway.usage_snapshot()
    assert usage.call_count == 1
    assert usage.provider_calls == {"ollama": 1}
    assert any(EXPECTED_MODEL_TAG in model for model in usage.model_calls)
