import pytest

from app.data.gateway import (
    DatabasePermissionError,
    DatabaseQueryExecutionError,
    DatabaseReadOnlyConfigurationError,
    DatabaseResultTooLargeError,
)
from app.errors import normalize_error
from app.semantic.gateway import SemanticProviderUnavailableError


@pytest.mark.parametrize(
    ("error", "code", "status"),
    [
        (
            DatabaseReadOnlyConfigurationError("password=secret"),
            "database_configuration_error",
            503,
        ),
        (DatabasePermissionError("private table"), "database_permission_denied", 403),
        (DatabaseQueryExecutionError("raw SQL error"), "query_execution_failed", 422),
        (DatabaseResultTooLargeError("raw row"), "result_too_large", 422),
        (
            SemanticProviderUnavailableError("http://internal-wren:8080/mcp"),
            "semantic_provider_unavailable",
            503,
        ),
    ],
)
def test_database_errors_are_typed_and_sanitized(
    error: Exception,
    code: str,
    status: int,
) -> None:
    normalized = normalize_error(error, request_id="request-1")

    assert normalized.code == code
    assert normalized.status_code == status
    assert "secret" not in normalized.safe_message
    assert "raw" not in normalized.safe_message
