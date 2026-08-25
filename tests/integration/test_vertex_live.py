from time import perf_counter

import google.auth
import pytest
from google.auth.exceptions import DefaultCredentialsError

from app.config import Settings
from app.llm.factory import build_llm_gateway
from app.llm.gateway import LLMGatewayError, LLMGatewayWithUsage, SQLGeneration
from app.security.sql_validation import SQLValidator

VERTEX_GEMINI_MODEL = "vertex_ai/gemini-2.5-flash"


@pytest.mark.cloud
@pytest.mark.asyncio
async def test_live_vertex_structured_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    for key_name in (
        "CEREBRAS_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "ZAI_API_KEY",
    ):
        monkeypatch.setenv(key_name, "")

    settings = Settings(LLM_MAX_RETRIES=0)
    if not settings.run_cloud_llm_tests:
        pytest.skip("Set RUN_CLOUD_LLM_TESTS=1 to allow a live Vertex AI request")
    if settings.llm_provider != "litellm":
        pytest.skip("Configure LLM_PROVIDER=litellm for the Vertex AI smoke test")
    if set(settings.model_aliases.values()) != {VERTEX_GEMINI_MODEL}:
        pytest.skip("Configure both logical aliases with the Vertex AI Gemini model")
    if not settings.vertex_ai_project:
        pytest.skip("VERTEXAI_PROJECT is not configured")
    try:
        google.auth.default()
    except DefaultCredentialsError:
        pytest.skip("Google Application Default Credentials are unavailable")

    assert settings.required_api_key_name(VERTEX_GEMINI_MODEL) is None
    assert settings.api_keys_by_alias == {}
    assert settings.model_options_by_alias == {
        "analytics-general": {
            "vertex_project": settings.vertex_ai_project,
            "vertex_location": settings.vertex_ai_location,
        },
        "sql-reasoner": {
            "vertex_project": settings.vertex_ai_project,
            "vertex_location": settings.vertex_ai_location,
        },
    }

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
            f"Vertex AI smoke infrastructure error: {category} "
            f"(HTTP {status or 'unknown'}, code {code or 'unknown'}).",
            pytrace=False,
        )
    latency_ms = round((perf_counter() - started_at) * 1000, 3)

    assert result.sql is not None
    SQLValidator().validate_readonly(result.sql)
    assert isinstance(gateway, LLMGatewayWithUsage)
    usage = gateway.usage_snapshot()
    assert usage.call_count == 1
    assert usage.provider_calls == {"vertex_ai": 1}
    assert any("gemini-2.5-flash" in model for model in usage.model_calls)
    assert latency_ms > 0
