from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    app_name: str = Field(default="Enterprise Data Agent", alias="APP_NAME")
    database_provider: Literal["fake", "postgres"] = Field(
        default="fake",
        alias="DATABASE_PROVIDER",
    )
    database_url: PostgresDsn = Field(
        default=PostgresDsn(
            "postgresql://eda_readonly@localhost:5432/enterprise_analytics"
        ),
        alias="DATABASE_URL",
    )
    llm_provider: Literal["fake", "litellm"] = Field(default="fake", alias="LLM_PROVIDER")
    llm_model_analytics_general: str = Field(
        default="fake/analytics-general",
        alias="LLM_MODEL_ANALYTICS_GENERAL",
    )
    llm_model_sql_reasoner: str = Field(default="fake/sql-reasoner", alias="LLM_MODEL_SQL_REASONER")
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=300, alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=2, ge=0, le=10, alias="LLM_MAX_RETRIES")
    query_row_limit: int = Field(default=100, ge=1, le=1000, alias="QUERY_ROW_LIMIT")
    query_timeout_seconds: int = Field(default=10, ge=1, le=120, alias="QUERY_TIMEOUT_SECONDS")

    @property
    def model_aliases(self) -> dict[str, str]:
        return {
            "analytics-general": self.llm_model_analytics_general,
            "sql-reasoner": self.llm_model_sql_reasoner,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
