from pathlib import Path

import pytest

from app.config import Settings
from app.evals.cli import (
    EvaluationConfigurationError,
    _validate_cloud_configuration,
    _validate_configured_configuration,
)
from app.evals.deterministic_llm import DeterministicEvaluationLLM
from app.evals.duckdb_gateway import DuckDBEvaluationGateway
from app.evals.loader import load_evaluation_cases
from app.evals.report import (
    render_cerebras_comparison,
    render_cloud_comparison,
    render_groq_qwen_comparison,
)
from app.evals.runner import run_evaluations
from app.security.sql_validation import SQLValidator

CASES_PATH = Path(__file__).parents[2] / "evals" / "cases.json"


def test_cloud_configuration_requires_explicit_gemini_key_and_aliases() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="gemini/gemini-2.5-flash",
        LLM_MODEL_SQL_REASONER="gemini/gemini-2.5-flash",
        GEMINI_API_KEY=None,
        RUN_CLOUD_LLM_TESTS=True,
    )

    with pytest.raises(EvaluationConfigurationError, match="GEMINI_API_KEY"):
        _validate_cloud_configuration(settings)


def test_cloud_configuration_requires_explicit_opt_in() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="gemini/gemini-2.5-flash",
        LLM_MODEL_SQL_REASONER="gemini/gemini-2.5-flash",
        GEMINI_API_KEY="unit-test-credential",
        RUN_CLOUD_LLM_TESTS=False,
    )

    with pytest.raises(EvaluationConfigurationError, match="RUN_CLOUD_LLM_TESTS=1"):
        _validate_cloud_configuration(settings)


def test_cloud_configuration_supports_gemini_and_openai_alias_credentials() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="gemini/gemini-2.5-flash",
        LLM_MODEL_SQL_REASONER="openai/sql-model",
        GEMINI_API_KEY="gemini-unit-test-credential",
        OPENAI_API_KEY="openai-unit-test-credential",
        RUN_CLOUD_LLM_TESTS=True,
    )

    _validate_cloud_configuration(settings)
    assert set(settings.api_keys_by_alias) == {"analytics-general", "sql-reasoner"}
    assert "unit-test-credential" not in settings.model_dump_json()


def test_configured_evaluation_supports_explicit_local_ollama_without_key() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="ollama_chat/qwen3.6:27b",
        LLM_MODEL_SQL_REASONER="ollama_chat/qwen3.6:27b",
        RUN_LOCAL_LLM_TESTS=True,
    )

    _validate_configured_configuration(settings)
    assert settings.api_keys_by_alias == {}


def test_configured_evaluation_requires_local_opt_in() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="ollama_chat/qwen3.6:27b",
        LLM_MODEL_SQL_REASONER="ollama_chat/qwen3.6:27b",
        RUN_LOCAL_LLM_TESTS=False,
    )

    with pytest.raises(EvaluationConfigurationError, match="RUN_LOCAL_LLM_TESTS=1"):
        _validate_configured_configuration(settings)


def test_cloud_configuration_supports_groq_with_explicit_key_and_opt_in() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="groq/qwen/qwen3.6-27b",
        LLM_MODEL_SQL_REASONER="groq/qwen/qwen3.6-27b",
        GROQ_API_KEY="groq-unit-test-credential",
        RUN_CLOUD_LLM_TESTS=True,
    )

    _validate_configured_configuration(settings)
    assert set(settings.api_keys_by_alias) == {"analytics-general", "sql-reasoner"}


def test_cloud_configuration_requires_groq_key() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="groq/qwen/qwen3.6-27b",
        LLM_MODEL_SQL_REASONER="groq/qwen/qwen3.6-27b",
        GROQ_API_KEY=None,
        RUN_CLOUD_LLM_TESTS=True,
    )

    with pytest.raises(EvaluationConfigurationError, match="GROQ_API_KEY"):
        _validate_configured_configuration(settings)


def test_cloud_configuration_supports_cerebras_with_explicit_key_and_opt_in() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="cerebras/gpt-oss-120b",
        LLM_MODEL_SQL_REASONER="cerebras/gpt-oss-120b",
        CEREBRAS_API_KEY="cerebras-unit-test-credential",
        RUN_CLOUD_LLM_TESTS=True,
    )

    _validate_configured_configuration(settings)
    assert set(settings.api_keys_by_alias) == {"analytics-general", "sql-reasoner"}


def test_cloud_configuration_requires_cerebras_key() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="cerebras/gpt-oss-120b",
        LLM_MODEL_SQL_REASONER="cerebras/gpt-oss-120b",
        CEREBRAS_API_KEY=None,
        RUN_CLOUD_LLM_TESTS=True,
    )

    with pytest.raises(EvaluationConfigurationError, match="CEREBRAS_API_KEY"):
        _validate_configured_configuration(settings)


def test_cloud_configuration_supports_zai_with_explicit_key_and_opt_in() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="zai/glm-4.5-flash",
        LLM_MODEL_SQL_REASONER="zai/glm-4.5-flash",
        ZAI_API_KEY="zai-unit-test-credential",
        RUN_CLOUD_LLM_TESTS=True,
    )

    _validate_configured_configuration(settings)
    assert set(settings.api_keys_by_alias) == {"analytics-general", "sql-reasoner"}


def test_cloud_configuration_requires_zai_key() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="zai/glm-4.5-flash",
        LLM_MODEL_SQL_REASONER="zai/glm-4.5-flash",
        ZAI_API_KEY=None,
        RUN_CLOUD_LLM_TESTS=True,
    )

    with pytest.raises(EvaluationConfigurationError, match="ZAI_API_KEY"):
        _validate_configured_configuration(settings)


def test_cloud_configuration_supports_vertex_adc_with_explicit_opt_in() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="vertex_ai/gemini-2.5-flash",
        LLM_MODEL_SQL_REASONER="vertex_ai/gemini-2.5-flash",
        VERTEXAI_PROJECT="test-project",
        VERTEXAI_LOCATION="global",
        RUN_CLOUD_LLM_TESTS=True,
    )

    _validate_configured_configuration(settings)
    assert settings.api_keys_by_alias == {}


def test_self_hosted_qwen_endpoint_is_configured_like_any_vertex_model() -> None:
    """A Model Garden endpoint needs no bespoke settings.

    `vertex_ai/openai/<endpoint id>` resolves to the vertex_ai provider, so the
    existing alias plumbing supplies project and location, ADC supplies the
    credentials, and no API key is required or stored.
    """
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="vertex_ai/openai/1234567890123456789",
        LLM_MODEL_SQL_REASONER="vertex_ai/openai/1234567890123456789",
        VERTEXAI_PROJECT="test-project",
        VERTEXAI_LOCATION="us-central1",
        RUN_CLOUD_LLM_TESTS=True,
    )

    _validate_configured_configuration(settings)

    assert settings.model_provider(settings.llm_model_sql_reasoner) == "vertex_ai"
    assert settings.api_keys_by_alias == {}
    assert settings.model_options_by_alias["sql-reasoner"] == {
        "vertex_project": "test-project",
        "vertex_location": "us-central1",
        # Qwen3.6 is a reasoning model; its thinking trace is not valid JSON and
        # breaks every structured response unless disabled at the chat template.
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }


def test_managed_vertex_models_do_not_receive_vllm_request_options() -> None:
    """A managed Vertex model would reject vLLM-specific options."""
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="vertex_ai/gemini-2.5-flash",
        LLM_MODEL_SQL_REASONER="vertex_ai/gemini-2.5-flash",
        VERTEXAI_PROJECT="test-project",
        VERTEXAI_LOCATION="global",
    )

    assert settings.model_options_by_alias["sql-reasoner"] == {
        "vertex_project": "test-project",
        "vertex_location": "global",
    }
    assert not Settings.is_vertex_openai_endpoint("vertex_ai/gemini-2.5-flash")
    assert Settings.is_vertex_openai_endpoint("vertex_ai/openai/mg-endpoint-abc")


def test_self_hosted_qwen_endpoint_still_requires_cloud_data_approval() -> None:
    """Self-hosting the weights does not exempt the endpoint.

    Result content still leaves the process, so the cloud-data guard must treat a
    project-owned Vertex endpoint exactly like any other cloud model.
    """
    with pytest.raises(ValueError, match="ALLOW_CLOUD_DATABASE_DATA"):
        Settings(
            LLM_PROVIDER="litellm",
            LLM_MODEL_ANALYTICS_GENERAL="vertex_ai/openai/1234567890123456789",
            LLM_MODEL_SQL_REASONER="vertex_ai/openai/1234567890123456789",
            VERTEXAI_PROJECT="test-project",
            VERTEXAI_LOCATION="us-central1",
            DATABASE_PROVIDER="postgres",
            DATABASE_URL="postgresql://u:p@localhost:5432/db",
            ALLOW_CLOUD_DATABASE_DATA=False,
        )


def test_cloud_configuration_requires_vertex_project() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="vertex_ai/gemini-2.5-flash",
        LLM_MODEL_SQL_REASONER="vertex_ai/gemini-2.5-flash",
        VERTEXAI_PROJECT=None,
        RUN_CLOUD_LLM_TESTS=True,
    )

    with pytest.raises(EvaluationConfigurationError, match="VERTEXAI_PROJECT"):
        _validate_configured_configuration(settings)


@pytest.mark.asyncio
async def test_comparison_report_distinguishes_deterministic_and_cloud_metrics() -> None:
    cases = load_evaluation_cases(CASES_PATH)
    summary = await run_evaluations(
        cases,
        backend="duckdb",
        database_factory=DuckDBEvaluationGateway,
        llm_gateway=DeterministicEvaluationLLM(cases),
        sql_validator=SQLValidator(),
        retry_count=0,
        llm_backend="deterministic",
        dataset_sha256="abc123",
    )

    pending = render_cloud_comparison(
        summary,
        None,
        blocker="GEMINI_API_KEY is not configured.",
    )
    comparison = render_cloud_comparison(summary, summary)

    assert "no synthetic or deterministic result is represented as real model accuracy" in pending
    assert "GEMINI_API_KEY is not configured" in pending
    assert "Deterministic reference SQL is never used" in comparison
    assert "## Failed Cases" in comparison

    database = DuckDBEvaluationGateway()
    try:
        schema = await database.search_schema("")
    finally:
        await database.close()
    groq_pending = render_groq_qwen_comparison(
        summary,
        None,
        schema=schema,
        blocker="GROQ_API_KEY is not configured.",
    )
    groq_comparison = render_groq_qwen_comparison(summary, summary, schema=schema)

    assert "No cloud accuracy or latency values" in groq_pending
    assert "GROQ_API_KEY is not configured" in groq_pending
    assert "Hallucinated columns" in groq_comparison
    assert "Failed Groq Cases" in groq_comparison

    cerebras_comparison = render_cerebras_comparison(
        summary,
        summary,
        summary,
        schema=schema,
    )
    assert "Cerebras GPT-OSS 120B Cloud Baseline" in cerebras_comparison
    assert "available rate-limited run" in cerebras_comparison
    assert "Failed Cerebras Cases" in cerebras_comparison
