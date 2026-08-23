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
