import pytest

from app.config import Settings


def test_settings_read_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "Test Data Agent")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("QUERY_ROW_LIMIT", "42")

    settings = Settings()

    assert settings.app_name == "Test Data Agent"
    assert settings.database_provider == "fake"
    assert settings.llm_provider == "fake"
    assert settings.query_row_limit == 42
    assert settings.model_aliases["sql-reasoner"] == "fake/sql-reasoner"


def test_database_limits_and_metadata_configuration() -> None:
    settings = Settings(
        DB_ALLOWED_SCHEMAS="analytics,reporting",
        DB_MAX_ROWS=250,
        DB_QUERY_TIMEOUT_SECONDS=15,
        DB_MAX_RESULT_BYTES=250000,
        DB_CATEGORICAL_COLUMNS="status,region",
        DB_CATEGORICAL_MAX_VALUES=8,
    )

    assert settings.database_allowed_schemas == ("analytics", "reporting")
    assert settings.query_row_limit == 250
    assert settings.query_timeout_seconds == 15
    assert settings.query_max_result_bytes == 250000
    assert settings.database_categorical_columns == {"status", "region"}
    assert settings.database_categorical_max_values == 8


def test_semantic_provider_configuration_is_explicit() -> None:
    settings = Settings(
        SEMANTIC_PROVIDER="wren",
        WREN_MCP_URL="http://wren.test:8080/mcp",
        WREN_TIMEOUT_SECONDS=7,
        WREN_MAX_CONTEXT_MODELS=4,
        WREN_PROJECT_ID="enterprise-test",
        SQL_GENERATION_PROVIDER="llm",
    )

    assert settings.semantic_provider == "wren"
    assert settings.wren_mcp_url == "http://wren.test:8080/mcp"
    assert settings.wren_timeout_seconds == 7
    assert settings.wren_max_context_models == 4
    assert settings.wren_project_id == "enterprise-test"
    assert settings.sql_generation_provider == "llm"


def test_metric_provider_configuration_is_explicit() -> None:
    settings = Settings(
        METRIC_PROVIDER="cube",
        CUBE_API_URL="http://cube.test:4000",
        CUBE_TIMEOUT_SECONDS=12,
    )

    assert settings.metric_provider == "cube"
    assert settings.cube_api_url == "http://cube.test:4000"
    assert settings.cube_timeout_seconds == 12


def test_metric_provider_defaults_to_wren() -> None:
    # `tests/conftest.py` sets METRIC_PROVIDER=cube in the ambient test
    # environment for the whole suite, so a bare Settings() here would read
    # that override rather than the field's actual default. Inspect the field
    # default directly instead.
    assert Settings.model_fields["metric_provider"].default == "wren"


def test_wren_metric_provider_is_explicit() -> None:
    settings = Settings(
        METRIC_PROVIDER="wren",
        WREN_MCP_URL="http://wren.test:8080/mcp",
        WREN_TIMEOUT_SECONDS=7,
    )

    assert settings.metric_provider == "wren"
    assert settings.wren_mcp_url == "http://wren.test:8080/mcp"
    assert settings.wren_timeout_seconds == 7


def test_staging_postgres_cannot_disable_read_only_verification() -> None:
    with pytest.raises(ValueError, match="DB_REQUIRE_READ_ONLY"):
        Settings(
            APP_ENV="staging",
            DATABASE_PROVIDER="postgres",
            DB_REQUIRE_READ_ONLY=False,
        )


def test_production_rejects_development_only_backends() -> None:
    with pytest.raises(ValueError, match="AUTHENTICATION_PROVIDER=local"):
        Settings(
            APP_ENV="production",
            AUTHORIZATION_PROVIDER="opa",
            DATABASE_PROVIDER="postgres",
            LLM_PROVIDER="litellm",
            LLM_MODEL_ANALYTICS_GENERAL="ollama_chat/qwen3.5:9b",
            LLM_MODEL_SQL_REASONER="ollama_chat/qwen3.5:9b",
            CONVERSATION_CHECKPOINT_PROVIDER="postgres",
            CHECKPOINT_DATABASE_URL="postgresql://checkpoint:test@localhost:5433/checkpoints",
        )


def test_production_accepts_oidc_opa_persistent_checkpoint_and_real_backends() -> None:
    settings = Settings(
        APP_ENV="production",
        AUTHENTICATION_PROVIDER="oidc",
        OIDC_ISSUER="https://login.microsoftonline.com/test-tenant/v2.0",
        OIDC_AUDIENCE="api://enterprise-data-agent",
        AUTHORIZATION_PROVIDER="opa",
        DATABASE_PROVIDER="postgres",
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="ollama_chat/qwen3.5:9b",
        LLM_MODEL_SQL_REASONER="ollama_chat/qwen3.5:9b",
        CONVERSATION_CHECKPOINT_PROVIDER="postgres",
        CHECKPOINT_DATABASE_URL="postgresql://checkpoint:test@checkpoint-db/checkpoints",
    )

    assert settings.authentication_provider == "oidc"
    assert settings.authorization_provider == "opa"
    assert settings.conversation_checkpoint_provider == "postgres"


def test_cloud_model_with_postgres_requires_explicit_data_approval() -> None:
    with pytest.raises(ValueError, match="ALLOW_CLOUD_DATABASE_DATA"):
        Settings(
            DATABASE_PROVIDER="postgres",
            ALLOW_CLOUD_DATABASE_DATA=False,
            LLM_PROVIDER="litellm",
            LLM_MODEL_ANALYTICS_GENERAL="vertex_ai/gemini-2.5-flash",
            LLM_MODEL_SQL_REASONER="vertex_ai/gemini-2.5-flash",
            VERTEXAI_PROJECT="test-project",
        )


def test_cloud_keys_are_excluded_from_serialized_settings() -> None:
    settings = Settings(
        OPENAI_API_KEY="openai-unit-test-credential",
        GEMINI_API_KEY="gemini-unit-test-credential",
        GROQ_API_KEY="groq-unit-test-credential",
        CEREBRAS_API_KEY="cerebras-unit-test-credential",
        ZAI_API_KEY="zai-unit-test-credential",
        OPENMETADATA_JWT_TOKEN="openmetadata-unit-test-credential",
        TOOLBOX_AUTH_TOKEN="toolbox-unit-test-credential",
        CHECKPOINT_DATABASE_URL=(
            "postgresql://checkpoint:checkpoint-unit-test-credential@localhost/checkpoints"
        ),
    )

    assert settings.openai_api_key is not None
    assert settings.gemini_api_key is not None
    assert settings.groq_api_key is not None
    assert settings.cerebras_api_key is not None
    assert settings.zai_api_key is not None
    assert settings.openmetadata_jwt_token is not None
    assert settings.toolbox_auth_token is not None
    assert settings.checkpoint_database_url is not None
    assert "openai_api_key" not in settings.model_dump()
    assert "gemini_api_key" not in settings.model_dump()
    assert "groq_api_key" not in settings.model_dump()
    assert "cerebras_api_key" not in settings.model_dump()
    assert "zai_api_key" not in settings.model_dump()
    assert "openmetadata_jwt_token" not in settings.model_dump()
    assert "toolbox_auth_token" not in settings.model_dump()
    assert "checkpoint_database_url" not in settings.model_dump()
    assert "unit-test-credential" not in str(settings)


def test_groq_aliases_receive_only_the_groq_environment_credential() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="groq/qwen/qwen3.6-27b",
        LLM_MODEL_SQL_REASONER="groq/qwen/qwen3.6-27b",
        GROQ_API_KEY="groq-unit-test-credential",
    )

    assert settings.required_api_key_name("groq/qwen/qwen3.6-27b") == "GROQ_API_KEY"
    assert set(settings.api_keys_by_alias) == {"analytics-general", "sql-reasoner"}
    assert settings.api_bases_by_alias == {}
    assert settings.structured_output_modes_by_alias == {
        "analytics-general": "tool_call",
        "sql-reasoner": "tool_call",
    }


def test_cerebras_aliases_receive_only_the_cerebras_environment_credential() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="cerebras/gpt-oss-120b",
        LLM_MODEL_SQL_REASONER="cerebras/gpt-oss-120b",
        CEREBRAS_API_KEY="cerebras-unit-test-credential",
    )

    assert settings.required_api_key_name("cerebras/gpt-oss-120b") == "CEREBRAS_API_KEY"
    assert set(settings.api_keys_by_alias) == {"analytics-general", "sql-reasoner"}
    assert settings.api_bases_by_alias == {}


def test_zai_aliases_receive_only_the_zai_environment_credential() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="zai/glm-4.5-flash",
        LLM_MODEL_SQL_REASONER="zai/glm-4.5-flash",
        ZAI_API_KEY="zai-unit-test-credential",
    )

    assert settings.required_api_key_name("zai/glm-4.5-flash") == "ZAI_API_KEY"
    assert set(settings.api_keys_by_alias) == {"analytics-general", "sql-reasoner"}
    assert settings.api_bases_by_alias == {}


def test_ollama_aliases_receive_configured_local_api_base_without_credentials() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="ollama_chat/qwen3.5:9b",
        LLM_MODEL_SQL_REASONER="ollama_chat/qwen3.5:9b",
        OLLAMA_API_BASE="http://localhost:11434/",
        OLLAMA_NUM_CTX=8192,
    )

    assert settings.api_keys_by_alias == {}
    assert settings.api_bases_by_alias == {
        "analytics-general": "http://localhost:11434",
        "sql-reasoner": "http://localhost:11434",
    }
    assert settings.model_options_by_alias == {
        "analytics-general": {"num_ctx": 8192},
        "sql-reasoner": {"num_ctx": 8192},
    }


def test_vertex_aliases_receive_adc_project_options_without_api_keys() -> None:
    settings = Settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL_ANALYTICS_GENERAL="vertex_ai/gemini-2.5-flash",
        LLM_MODEL_SQL_REASONER="vertex_ai/gemini-2.5-flash",
        VERTEXAI_PROJECT="test-project",
        VERTEXAI_LOCATION="global",
        GEMINI_API_KEY="ai-studio-key-must-not-be-routed",
    )

    assert settings.required_api_key_name("vertex_ai/gemini-2.5-flash") is None
    assert settings.api_keys_by_alias == {}
    assert settings.api_bases_by_alias == {}
    assert settings.model_options_by_alias == {
        "analytics-general": {
            "vertex_project": "test-project",
            "vertex_location": "global",
        },
        "sql-reasoner": {
            "vertex_project": "test-project",
            "vertex_location": "global",
        },
    }
