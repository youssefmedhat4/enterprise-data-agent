from collections.abc import Sequence
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import get_database_gateway, get_llm_gateway
from app.contracts.analytics import ClaimEvidence, GroundedClaim
from app.data.fake import FakeDatabaseGateway
from app.data.gateway import (
    DatabaseQueryResult,
    DatabaseQueryTimeoutError,
    DatabaseUnavailableError,
)
from app.llm.fake import FakeLLMGateway
from app.llm.gateway import (
    AnswerGeneration,
    InvalidStructuredModelOutputError,
    LLMGateway,
    LLMGatewayError,
    LLMRateLimitError,
    ResponseModelT,
    SQLGeneration,
)
from app.main import app

QUESTION = (
    "Show each department, its number of employees, total salary, average salary, "
    "and highest paid employee, ordered by total payroll."
)


class RaisingLLMGateway(LLMGateway):
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system, user, response_model
        raise self.error


class UnsafeLLMGateway(LLMGateway):
    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system, user
        return response_model.model_validate(
            {
                "action": "execute",
                "sql": "DROP TABLE analytics.departments",
                "explanation": "Unsafe test output.",
            }
        )


class UngroundedLLMGateway(FakeLLMGateway):
    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        if response_model is AnswerGeneration:
            return response_model.model_validate(
                {
                    "answer": "Engineering payroll is 999.",
                    "claims": [
                        GroundedClaim(
                            claim="Unsupported payroll.",
                            evidence=[
                                ClaimEvidence(
                                    row_index=0,
                                    field="total_salary",
                                    value=999,
                                )
                            ],
                        )
                    ],
                }
            )
        return await super().generate_structured(
            model_alias=model_alias,
            system=system,
            user=user,
            response_model=response_model,
        )


class ClarifyingLLMGateway(LLMGateway):
    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system, user
        assert response_model is SQLGeneration
        return response_model.model_validate(
            {
                "action": "clarify",
                "explanation": "Revenue needs a governed definition.",
                "clarification_question": "Which revenue definition should I use?",
            }
        )


class UnavailableDatabaseGateway(FakeDatabaseGateway):
    async def execute_readonly(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> DatabaseQueryResult:
        del sql, parameters
        raise DatabaseUnavailableError("password=secret database host is unavailable")


class TimeoutDatabaseGateway(FakeDatabaseGateway):
    async def execute_readonly(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> DatabaseQueryResult:
        del sql, parameters
        raise DatabaseQueryTimeoutError("internal timeout diagnostics")


async def post_query() -> Any:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.post("/analytics/query", json={"question": QUESTION})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (LLMGatewayError("api_key=secret"), 503, "llm_unavailable"),
        (LLMRateLimitError("provider quota details"), 429, "llm_rate_limited"),
        (
            InvalidStructuredModelOutputError("raw provider payload"),
            502,
            "invalid_structured_model_output",
        ),
        (RuntimeError("stack trace and password=secret"), 500, "internal_unexpected_error"),
    ],
)
async def test_api_sanitizes_llm_and_unexpected_errors(
    error: Exception,
    status: int,
    code: str,
) -> None:
    database = FakeDatabaseGateway()
    app.dependency_overrides[get_database_gateway] = lambda: database
    app.dependency_overrides[get_llm_gateway] = lambda: RaisingLLMGateway(error)
    try:
        response = await post_query()
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["request_id"] != "unknown"
    assert "secret" not in response.text
    assert "provider" not in response.text
    assert database.executed_sql == []


@pytest.mark.asyncio
async def test_api_rejects_unsafe_sql_without_database_execution() -> None:
    database = FakeDatabaseGateway()
    app.dependency_overrides[get_database_gateway] = lambda: database
    app.dependency_overrides[get_llm_gateway] = lambda: UnsafeLLMGateway()
    try:
        response = await post_query()
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsafe_sql"
    assert database.executed_sql == []


@pytest.mark.asyncio
async def test_api_returns_safe_grounding_and_database_errors() -> None:
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: UngroundedLLMGateway()
    try:
        grounding_response = await post_query()
    finally:
        app.dependency_overrides.clear()

    app.dependency_overrides[get_database_gateway] = lambda: UnavailableDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway()
    try:
        database_response = await post_query()
    finally:
        app.dependency_overrides.clear()

    assert grounding_response.status_code == 422
    assert grounding_response.json()["error"]["code"] == "grounding_failure"
    assert database_response.status_code == 503
    assert database_response.json()["error"]["code"] == "database_unavailable"
    assert "secret" not in database_response.text


@pytest.mark.asyncio
async def test_api_maps_query_timeout_to_stable_retryable_error() -> None:
    app.dependency_overrides[get_database_gateway] = lambda: TimeoutDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway()
    try:
        response = await post_query()
    finally:
        app.dependency_overrides.clear()

    payload = response.json()["error"]
    assert response.status_code == 504
    assert payload["code"] == "query_timeout"
    assert payload["retryable"] is True


@pytest.mark.asyncio
async def test_clarification_is_a_typed_successful_response() -> None:
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: ClarifyingLLMGateway()
    try:
        response = await post_query()
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["clarification_required"] is True
    assert payload["clarification_question"] == "Which revenue definition should I use?"
    assert payload["execution"]["status"] == "clarification_required"
    assert payload["rows"] == []


@pytest.mark.asyncio
async def test_request_validation_uses_stable_error_contract() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/analytics/query", json={"question": ""})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "input" not in response.text
