import os
from collections.abc import Iterable
from functools import lru_cache
from typing import Any, Literal

from pydantic import AliasChoices, Field, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from app.llm.profiles import MODEL_PROFILE_DISPLAY_NAMES, ModelProfile, ResolvedModelProfile


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del cls, settings_cls
        if os.environ.get("APP_ENV", "development").casefold() in {
            "production",
            "staging",
        }:
            return init_settings, env_settings, file_secret_settings
        return init_settings, env_settings, dotenv_settings, file_secret_settings

    app_env: str = Field(default="development", alias="APP_ENV")
    app_name: str = Field(default="Enterprise Data Agent", alias="APP_NAME")
    api_debug_provenance_enabled: bool = Field(
        default=False,
        alias="API_DEBUG_PROVENANCE_ENABLED",
    )
    observability_provider: Literal["none", "logging"] = Field(
        default="none",
        alias="OBSERVABILITY_PROVIDER",
    )
    readiness_require_metric_provider: bool = Field(
        default=False,
        alias="READINESS_REQUIRE_METRIC_PROVIDER",
    )
    authentication_provider: Literal["local", "oidc"] = Field(
        default="local",
        alias="AUTHENTICATION_PROVIDER",
    )
    local_auth_subject: str = Field(
        default="local-developer",
        min_length=1,
        alias="LOCAL_AUTH_SUBJECT",
    )
    local_auth_roles_csv: str = Field(
        default="admin_analytics",
        alias="LOCAL_AUTH_ROLES",
    )
    oidc_issuer: str | None = Field(default=None, alias="OIDC_ISSUER")
    oidc_audience: str | None = Field(default=None, alias="OIDC_AUDIENCE")
    oidc_discovery_url: str | None = Field(default=None, alias="OIDC_DISCOVERY_URL")
    oidc_subject_claim: str = Field(default="sub", alias="OIDC_SUBJECT_CLAIM")
    oidc_roles_claim: str = Field(default="roles", alias="OIDC_ROLES_CLAIM")
    oidc_display_name_claim: str = Field(default="name", alias="OIDC_DISPLAY_NAME_CLAIM")
    oidc_attribute_claims_csv: str = Field(
        default="tid,oid,preferred_username,email",
        alias="OIDC_ATTRIBUTE_CLAIMS",
    )
    oidc_timeout_seconds: float = Field(
        default=5,
        gt=0,
        le=30,
        alias="OIDC_TIMEOUT_SECONDS",
    )
    oidc_cache_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        alias="OIDC_CACHE_SECONDS",
    )
    oidc_clock_skew_seconds: int = Field(
        default=30,
        ge=0,
        le=300,
        alias="OIDC_CLOCK_SKEW_SECONDS",
    )
    authorization_provider: Literal["local", "opa"] = Field(
        default="local",
        alias="AUTHORIZATION_PROVIDER",
    )
    local_authorization_policy_path: str = Field(
        default="infra/opa/data/local_roles.json",
        min_length=1,
        alias="LOCAL_AUTHORIZATION_POLICY_PATH",
    )
    opa_url: str = Field(default="http://localhost:8181", alias="OPA_URL")
    opa_decision_path: str = Field(
        default="/v1/data/enterprise/analytics/decision",
        alias="OPA_DECISION_PATH",
    )
    opa_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        le=30,
        alias="OPA_TIMEOUT_SECONDS",
    )
    governance_provider: Literal["disabled", "openmetadata"] = Field(
        default="disabled",
        alias="GOVERNANCE_PROVIDER",
    )
    openmetadata_api_url: str = Field(
        default="http://localhost:8585/api",
        alias="OPENMETADATA_API_URL",
    )
    openmetadata_jwt_token: SecretStr | None = Field(
        default=None,
        alias="OPENMETADATA_JWT_TOKEN",
        exclude=True,
    )
    openmetadata_fqn_prefix: str = Field(
        default="enterprise_postgres.enterprise_analytics",
        alias="OPENMETADATA_FQN_PREFIX",
    )
    openmetadata_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
        alias="OPENMETADATA_TIMEOUT_SECONDS",
    )
    openmetadata_include_lineage: bool = Field(
        default=True,
        alias="OPENMETADATA_INCLUDE_LINEAGE",
    )
    openmetadata_sensitivity_classifications_csv: str = Field(
        default="PII,PersonalData,Sensitive",
        alias="OPENMETADATA_SENSITIVITY_CLASSIFICATIONS",
    )
    database_provider: Literal["fake", "postgres", "toolbox"] = Field(
        default="fake",
        alias="DATABASE_PROVIDER",
    )
    toolbox_mcp_url: str = Field(
        default="http://localhost:5000/mcp",
        alias="TOOLBOX_MCP_URL",
    )
    toolbox_auth_token: SecretStr | None = Field(
        default=None,
        alias="TOOLBOX_AUTH_TOKEN",
        exclude=True,
    )
    toolbox_source_id: str = Field(
        default="enterprise-postgres",
        min_length=1,
        alias="TOOLBOX_SOURCE_ID",
    )
    toolbox_dialect: Literal["postgres"] = Field(
        default="postgres",
        alias="TOOLBOX_DIALECT",
    )
    toolbox_execute_tool: str = Field(
        default="execute_sql",
        min_length=1,
        alias="TOOLBOX_EXECUTE_TOOL",
    )
    toolbox_schema_tool: str = Field(
        default="list_tables",
        min_length=1,
        alias="TOOLBOX_SCHEMA_TOOL",
    )
    toolbox_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        le=120,
        alias="TOOLBOX_TIMEOUT_SECONDS",
    )
    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql://eda_readonly@localhost:5432/enterprise_analytics"),
        alias="DATABASE_URL",
    )
    database_allowed_schemas_csv: str = Field(
        default="analytics",
        alias="DB_ALLOWED_SCHEMAS",
    )
    database_pool_min_size: int = Field(default=1, ge=1, le=20, alias="DB_POOL_MIN_SIZE")
    database_pool_max_size: int = Field(default=5, ge=1, le=100, alias="DB_POOL_MAX_SIZE")
    database_connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
        alias="DB_CONNECT_TIMEOUT_SECONDS",
    )
    database_require_read_only: bool = Field(
        default=True,
        alias="DB_REQUIRE_READ_ONLY",
    )
    allow_cloud_database_data: bool = Field(
        default=False,
        alias="ALLOW_CLOUD_DATABASE_DATA",
    )
    database_schema_cache_seconds: int = Field(
        default=300,
        ge=0,
        le=86400,
        alias="DB_SCHEMA_CACHE_SECONDS",
    )
    database_categorical_max_values: int = Field(
        default=20,
        ge=0,
        le=100,
        alias="DB_CATEGORICAL_MAX_VALUES",
    )
    database_categorical_columns_csv: str = Field(
        default="status,type,category,region",
        alias="DB_CATEGORICAL_COLUMNS",
    )
    database_categorical_max_value_length: int = Field(
        default=64,
        ge=1,
        le=256,
        alias="DB_CATEGORICAL_MAX_VALUE_LENGTH",
    )
    database_categorical_max_columns: int = Field(
        default=50,
        ge=0,
        le=500,
        alias="DB_CATEGORICAL_MAX_COLUMNS",
    )
    llm_provider: Literal["fake", "litellm"] = Field(default="fake", alias="LLM_PROVIDER")
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY", exclude=True)
    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY", exclude=True)
    groq_api_key: SecretStr | None = Field(default=None, alias="GROQ_API_KEY", exclude=True)
    cerebras_api_key: SecretStr | None = Field(
        default=None,
        alias="CEREBRAS_API_KEY",
        exclude=True,
    )
    zai_api_key: SecretStr | None = Field(default=None, alias="ZAI_API_KEY", exclude=True)
    vertex_ai_project: str | None = Field(default=None, alias="VERTEXAI_PROJECT")
    vertex_ai_location: str = Field(default="global", alias="VERTEXAI_LOCATION")
    llm_model_analytics_general: str = Field(
        default="fake/analytics-general",
        alias="LLM_MODEL_ANALYTICS_GENERAL",
    )
    llm_model_sql_reasoner: str = Field(default="fake/sql-reasoner", alias="LLM_MODEL_SQL_REASONER")
    llm_model_qwen_analytics_general: str | None = Field(
        default=None,
        alias="LLM_MODEL_QWEN_ANALYTICS_GENERAL",
    )
    llm_model_qwen_sql_reasoner: str | None = Field(
        default=None,
        alias="LLM_MODEL_QWEN_SQL_REASONER",
    )
    llm_model_gemini_analytics_general: str = Field(
        default="vertex_ai/gemini-2.5-flash",
        alias="LLM_MODEL_GEMINI_ANALYTICS_GENERAL",
    )
    llm_model_gemini_sql_reasoner: str = Field(
        default="vertex_ai/gemini-2.5-flash",
        alias="LLM_MODEL_GEMINI_SQL_REASONER",
    )
    embedding_provider: Literal["fake", "gemini"] = Field(
        default="fake",
        alias="EMBEDDING_PROVIDER",
    )
    #: Region for Vertex embeddings. Separate from VERTEXAI_LOCATION, which a
    #: deployment may point at a regional endpoint: Gemini embedding models are
    #: served from `global`, and a regional value yields a 404 for a model that
    #: is in fact available.
    embedding_vertex_location: str = Field(
        default="global",
        alias="EMBEDDING_VERTEX_LOCATION",
    )
    embedding_model: str = Field(
        default="vertex_ai/gemini-embedding-2",
        alias="EMBEDDING_MODEL",
    )
    # Gemini Embedding 2 is a Matryoshka model, so a prefix of the 3072-wide
    # vector stays valid. 768 keeps the pgvector index small at a small recall
    # cost; changing this requires re-embedding, which the stored dimension
    # makes detectable rather than silent.
    embedding_dimension: int = Field(
        default=768,
        ge=1,
        le=3072,
        alias="EMBEDDING_DIMENSION",
    )
    #: Where learned knowledge lives at runtime. `postgres` is the production
    #: shape; `memory` exists for development and tests. There is deliberately
    #: no automatic downgrade between them: silently losing persistence would
    #: let learning state diverge per worker while the API kept serving.
    #: Whether this process drains the knowledge generation queue. Off by
    #: default: generation calls a model, and starting to spend quota is an
    #: operator decision rather than something that begins on upgrade.
    knowledge_worker_enabled: bool = Field(
        default=False,
        alias="KNOWLEDGE_WORKER_ENABLED",
    )
    #: Seconds between polls. Conservative: the queue receives one job per
    #: cluster crossing a threshold, so frequent polling would spend far more
    #: on empty checks than the work is worth.
    knowledge_worker_poll_seconds: float = Field(
        default=60.0,
        ge=5.0,
        le=3600.0,
        alias="KNOWLEDGE_WORKER_POLL_SECONDS",
    )
    #: Connection references a reviewer may register a datasource against.
    #: An allowlist rather than free text: without it, registration would let
    #: an admin name any environment variable and have its value used as a
    #: connection string, turning a form into an environment reader.
    allowed_connection_refs_csv: str = Field(
        default="DATABASE_URL",
        alias="ALLOWED_CONNECTION_REFS",
    )
    knowledge_storage: Literal["postgres", "memory"] = Field(
        default="memory",
        alias="KNOWLEDGE_STORAGE",
    )
    #: Whether terminal analytics requests are remembered as product data.
    #: Off by default: retaining question text is a decision an operator makes
    #: deliberately, not something that starts happening on upgrade.
    question_memory_enabled: bool = Field(
        default=False,
        alias="QUESTION_MEMORY_ENABLED",
    )
    #: How much recurrence justifies proposing reusable knowledge. Conservative
    #: defaults: a handful of one-off questions is not a pattern. Tests override
    #: these with small values so behaviour stays deterministic.
    question_cluster_min_occurrences: int = Field(
        default=5,
        ge=1,
        alias="QUESTION_CLUSTER_MIN_OCCURRENCES",
    )
    question_cluster_min_successful: int = Field(
        default=3,
        ge=1,
        alias="QUESTION_CLUSTER_MIN_SUCCESSFUL",
    )
    question_cluster_similarity_threshold: float = Field(
        default=0.82,
        ge=0.0,
        le=1.0,
        alias="QUESTION_CLUSTER_SIMILARITY_THRESHOLD",
    )
    #: Gemini 3.1 Pro is reached through Vertex AI with Application Default
    #: Credentials rather than the Gemini Developer API. Vertex bills against
    #: the project instead of a per-key free tier, and ADC removes a long-lived
    #: API key from the deployment entirely.
    llm_model_gemini_pro_analytics_general: str = Field(
        default="vertex_ai/gemini-3.1-pro-preview",
        alias="LLM_MODEL_GEMINI_PRO_ANALYTICS_GENERAL",
    )
    llm_model_gemini_pro_sql_reasoner: str = Field(
        default="vertex_ai/gemini-3.1-pro-preview",
        alias="LLM_MODEL_GEMINI_PRO_SQL_REASONER",
    )
    llm_gemini_vertex_ai_location: str = Field(
        default="global",
        alias="LLM_GEMINI_VERTEXAI_LOCATION",
    )
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=300, alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=2, ge=0, le=10, alias="LLM_MAX_RETRIES")
    llm_max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        le=32768,
        alias="LLM_MAX_OUTPUT_TOKENS",
    )
    llm_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = Field(
        default=None, alias="LLM_REASONING_EFFORT"
    )
    ollama_api_base: str = Field(
        default="http://localhost:11434",
        alias="OLLAMA_API_BASE",
    )
    ollama_num_ctx: int = Field(default=8192, ge=1024, le=262144, alias="OLLAMA_NUM_CTX")
    run_cloud_llm_tests: bool = Field(default=False, alias="RUN_CLOUD_LLM_TESTS")
    run_local_llm_tests: bool = Field(default=False, alias="RUN_LOCAL_LLM_TESTS")
    evaluation_request_delay_seconds: float = Field(
        default=0.0,
        ge=0,
        le=60,
        alias="EVALUATION_REQUEST_DELAY_SECONDS",
    )
    query_row_limit: int = Field(
        default=100,
        ge=1,
        le=10000,
        validation_alias=AliasChoices("DB_MAX_ROWS", "QUERY_ROW_LIMIT"),
    )
    query_timeout_seconds: float = Field(
        default=10,
        gt=0,
        le=300,
        validation_alias=AliasChoices("DB_QUERY_TIMEOUT_SECONDS", "QUERY_TIMEOUT_SECONDS"),
    )
    query_max_result_bytes: int = Field(
        default=1_000_000,
        ge=1024,
        le=100_000_000,
        alias="DB_MAX_RESULT_BYTES",
    )
    conversation_checkpoint_provider: Literal["memory", "postgres"] = Field(
        default="memory",
        alias="CONVERSATION_CHECKPOINT_PROVIDER",
    )
    checkpoint_database_url: SecretStr | None = Field(
        default=None,
        alias="CHECKPOINT_DATABASE_URL",
        exclude=True,
    )
    checkpoint_pool_min_size: int = Field(
        default=1,
        ge=1,
        le=10,
        alias="CHECKPOINT_POOL_MIN_SIZE",
    )
    checkpoint_pool_max_size: int = Field(
        default=5,
        ge=1,
        le=50,
        alias="CHECKPOINT_POOL_MAX_SIZE",
    )
    checkpoint_connect_timeout_seconds: float = Field(
        default=10,
        gt=0,
        le=60,
        alias="CHECKPOINT_CONNECT_TIMEOUT_SECONDS",
    )
    semantic_provider: Literal["inmemory", "wren"] = Field(
        default="inmemory",
        alias="SEMANTIC_PROVIDER",
    )
    wren_mcp_url: str = Field(default="http://localhost:8080/mcp", alias="WREN_MCP_URL")
    wren_timeout_seconds: float = Field(default=10.0, gt=0, le=120, alias="WREN_TIMEOUT_SECONDS")
    wren_max_context_models: int = Field(default=6, ge=1, le=50, alias="WREN_MAX_CONTEXT_MODELS")
    wren_project_id: str = Field(default="enterprise_analytics", alias="WREN_PROJECT_ID")
    sql_generation_provider: Literal["llm", "wren"] = Field(
        default="llm",
        alias="SQL_GENERATION_PROVIDER",
    )
    metric_provider: Literal["cube", "wren"] = Field(
        default="wren",
        alias="METRIC_PROVIDER",
    )
    cube_api_url: str = Field(default="http://localhost:4000", alias="CUBE_API_URL")
    cube_api_token: SecretStr | None = Field(
        default=None,
        alias="CUBE_API_TOKEN",
        exclude=True,
    )
    cube_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=120,
        alias="CUBE_TIMEOUT_SECONDS",
    )

    @model_validator(mode="after")
    def validate_database_safety(self) -> "Settings":
        if self.database_pool_min_size > self.database_pool_max_size:
            raise ValueError("DB_POOL_MIN_SIZE cannot exceed DB_POOL_MAX_SIZE.")
        if (
            self.database_provider in {"postgres", "toolbox"}
            and self.app_env.casefold() in {"production", "staging"}
            and not self.database_require_read_only
        ):
            raise ValueError("DB_REQUIRE_READ_ONLY must remain enabled in staging and production.")
        if not self.database_allowed_schemas:
            raise ValueError("DB_ALLOWED_SCHEMAS must contain at least one schema.")
        if not self.local_auth_roles:
            raise ValueError("LOCAL_AUTH_ROLES must contain at least one role.")
        if self.authentication_provider == "oidc":
            if not self.oidc_issuer or not self.oidc_audience:
                raise ValueError(
                    "OIDC_ISSUER and OIDC_AUDIENCE are required when OIDC authentication "
                    "is enabled."
                )
            if not self.oidc_issuer.startswith("https://"):
                raise ValueError("OIDC_ISSUER must use HTTPS.")
        if self.checkpoint_pool_min_size > self.checkpoint_pool_max_size:
            raise ValueError(
                "CHECKPOINT_POOL_MIN_SIZE cannot exceed CHECKPOINT_POOL_MAX_SIZE."
            )
        if (
            self.conversation_checkpoint_provider == "postgres"
            and self.checkpoint_database_url is None
        ):
            raise ValueError(
                "CHECKPOINT_DATABASE_URL is required for PostgreSQL conversation checkpointing."
            )
        if self.governance_provider == "openmetadata" and not self.openmetadata_fqn_prefix.strip(
            "."
        ):
            raise ValueError(
                "OPENMETADATA_FQN_PREFIX is required when OpenMetadata governance is enabled."
            )
        if (
            self.app_env.casefold() in {"production", "staging"}
            and self.authorization_provider != "opa"
        ):
            raise ValueError("AUTHORIZATION_PROVIDER=opa is required in staging and production.")
        if self.app_env.casefold() in {"production", "staging"}:
            if self.conversation_checkpoint_provider == "memory":
                raise ValueError(
                    "CONVERSATION_CHECKPOINT_PROVIDER=memory is forbidden in staging "
                    "and production."
                )
            if self.database_provider == "fake":
                raise ValueError("DATABASE_PROVIDER=fake is forbidden in staging and production.")
            if self.llm_provider == "fake":
                raise ValueError("LLM_PROVIDER=fake is forbidden in staging and production.")
            if self.authentication_provider == "local":
                raise ValueError(
                    "AUTHENTICATION_PROVIDER=local is forbidden in staging and production; "
                    "configure AUTHENTICATION_PROVIDER=oidc."
                )
        return self

    @property
    def database_allowed_schemas(self) -> tuple[str, ...]:
        return _safe_csv_identifiers(self.database_allowed_schemas_csv)

    @property
    def database_categorical_columns(self) -> frozenset[str]:
        return frozenset(_safe_csv_identifiers(self.database_categorical_columns_csv))

    @property
    def local_auth_roles(self) -> tuple[str, ...]:
        return _safe_csv_identifiers(self.local_auth_roles_csv)

    @property
    def oidc_attribute_claims(self) -> tuple[str, ...]:
        return _safe_csv_identifiers(self.oidc_attribute_claims_csv)

    @property
    def openmetadata_sensitivity_classifications(self) -> tuple[str, ...]:
        return _safe_csv_identifiers(self.openmetadata_sensitivity_classifications_csv)

    @property
    def allowed_connection_refs(self) -> tuple[str, ...]:
        return _safe_csv_identifiers(self.allowed_connection_refs_csv)

    @property
    def model_aliases(self) -> dict[str, str]:
        return {
            "analytics-general": self.llm_model_analytics_general,
            "sql-reasoner": self.llm_model_sql_reasoner,
        }

    @property
    def api_keys_by_alias(self) -> dict[str, str]:
        return self._api_keys_for_aliases(self.model_aliases)

    @property
    def api_bases_by_alias(self) -> dict[str, str]:
        return self._api_bases_for_aliases(self.model_aliases)

    @property
    def model_options_by_alias(self) -> dict[str, dict[str, Any]]:
        return self._model_options_for_aliases(
            self.model_aliases,
            vertex_location=self.vertex_ai_location,
        )

    def resolve_model_profile(self, profile: ModelProfile) -> ResolvedModelProfile:
        if profile == "qwen":
            aliases = {
                "analytics-general": (
                    self.llm_model_qwen_analytics_general
                    or self.llm_model_analytics_general
                ),
                "sql-reasoner": (
                    self.llm_model_qwen_sql_reasoner or self.llm_model_sql_reasoner
                ),
            }
            vertex_location = self.vertex_ai_location
        elif profile == "gemini_pro":
            aliases = {
                "analytics-general": self.llm_model_gemini_pro_analytics_general,
                "sql-reasoner": self.llm_model_gemini_pro_sql_reasoner,
            }
            vertex_location = self.llm_gemini_vertex_ai_location
        else:
            aliases = {
                "analytics-general": self.llm_model_gemini_analytics_general,
                "sql-reasoner": self.llm_model_gemini_sql_reasoner,
            }
            vertex_location = self.llm_gemini_vertex_ai_location

        self.validate_cloud_data_for_models(aliases.values())
        return ResolvedModelProfile(
            profile=profile,
            display_name=MODEL_PROFILE_DISPLAY_NAMES[profile],
            model_aliases=aliases,
            model_options_by_alias=self._model_options_for_aliases(
                aliases,
                vertex_location=vertex_location,
            ),
            api_keys_by_alias=self._api_keys_for_aliases(aliases),
            api_bases_by_alias=self._api_bases_for_aliases(aliases),
            structured_output_modes_by_alias=self._structured_output_modes_for_aliases(
                aliases
            ),
        )

    def validate_cloud_data_for_models(self, models: Iterable[str]) -> None:
        cloud_providers = {self.model_provider(model) for model in models} & {
            "cerebras",
            "gemini",
            "groq",
            "openai",
            "vertex_ai",
            "zai",
        }
        if (
            self.database_provider in {"postgres", "toolbox"}
            and self.llm_provider == "litellm"
            and cloud_providers
            and not self.allow_cloud_database_data
        ):
            raise ValueError(
                "Set ALLOW_CLOUD_DATABASE_DATA=1 only for approved non-sensitive "
                "database data sent to a cloud model."
            )

    def _model_options_for_aliases(
        self,
        aliases: dict[str, str],
        *,
        vertex_location: str,
    ) -> dict[str, dict[str, Any]]:
        options: dict[str, dict[str, Any]] = {}
        for alias, model in aliases.items():
            provider = self.model_provider(model)
            if provider == "ollama":
                options[alias] = {"num_ctx": self.ollama_num_ctx}
            elif provider == "vertex_ai" and self.vertex_ai_project:
                options[alias] = {
                    "vertex_project": self.vertex_ai_project,
                    "vertex_location": vertex_location,
                }
                if self.is_vertex_openai_endpoint(model):
                    # Qwen3.6 is a reasoning model: by default it emits a thinking
                    # trace before the answer, which is not valid JSON and breaks
                    # every structured response. Turning thinking off at the chat
                    # template is what makes `response_format` usable at all.
                    options[alias]["extra_body"] = {
                        "chat_template_kwargs": {"enable_thinking": False}
                    }
        return options

    def _api_keys_for_aliases(self, aliases: dict[str, str]) -> dict[str, str]:
        keys: dict[str, str] = {}
        for alias, model in aliases.items():
            secret = self._api_key_for_model(model)
            if secret is not None and secret.get_secret_value():
                keys[alias] = secret.get_secret_value()
        return keys

    def _api_bases_for_aliases(self, aliases: dict[str, str]) -> dict[str, str]:
        return {
            alias: self.ollama_api_base.rstrip("/")
            for alias, model in aliases.items()
            if self.model_provider(model) == "ollama"
        }

    def _structured_output_modes_for_aliases(
        self,
        aliases: dict[str, str],
    ) -> dict[str, Literal["response_format", "tool_call"]]:
        return {
            alias: "tool_call"
            for alias, model in aliases.items()
            if self.model_provider(model) in {"groq", "zai"}
        }

    @staticmethod
    def is_vertex_openai_endpoint(model: str) -> bool:
        """True for a self-hosted Model Garden endpoint served over the OpenAI API.

        Distinguishes `vertex_ai/openai/<endpoint id>` (our own vLLM deployment)
        from a managed Vertex model such as `vertex_ai/gemini-2.5-flash`, which
        would reject vLLM-specific request options.
        """
        return model.lower().startswith("vertex_ai/openai/")

    @property
    def structured_output_modes_by_alias(
        self,
    ) -> dict[str, Literal["response_format", "tool_call"]]:
        return self._structured_output_modes_for_aliases(self.model_aliases)

    def model_provider(self, model: str) -> str:
        provider = model.partition("/")[0].lower()
        return "ollama" if provider == "ollama_chat" else provider

    def required_api_key_name(self, model: str) -> str | None:
        provider = self.model_provider(model)
        return {
            "gemini": "GEMINI_API_KEY",
            "groq": "GROQ_API_KEY",
            "cerebras": "CEREBRAS_API_KEY",
            "zai": "ZAI_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(provider)

    def _api_key_for_model(self, model: str) -> SecretStr | None:
        key_name = self.required_api_key_name(model)
        if key_name == "GEMINI_API_KEY":
            return self.gemini_api_key
        if key_name == "OPENAI_API_KEY":
            return self.openai_api_key
        if key_name == "GROQ_API_KEY":
            return self.groq_api_key
        if key_name == "CEREBRAS_API_KEY":
            return self.cerebras_api_key
        if key_name == "ZAI_API_KEY":
            return self.zai_api_key
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _safe_csv_identifiers(value: str) -> tuple[str, ...]:
    identifiers = tuple(item.strip() for item in value.split(",") if item.strip())
    if any(not item.replace("_", "").isalnum() for item in identifiers):
        raise ValueError("Database metadata identifiers may contain only letters, numbers, and _.")
    return identifiers
