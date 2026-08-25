import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from app.api.routes import (
    get_authenticated_identity,
    get_conversation_checkpointer,
    get_database_gateway,
    get_llm_gateway,
    get_metric_gateway,
)
from app.authentication.gateway import UserIdentity
from app.config import Settings, get_settings
from app.data.fake import FakeDatabaseGateway
from app.llm.fake import FakeLLMGateway
from app.main import app
from app.metrics.fake import FakeMetricGateway

QUESTION = (
    "Show each department, its number of employees, total salary, average salary, "
    "and highest paid employee, ordered by total payroll."
)


def _debug_settings() -> Settings:
    return Settings(API_DEBUG_PROVENANCE_ENABLED=True)


@pytest.mark.asyncio
async def test_analytics_api_returns_rows_answer_chart_and_provenance() -> None:
    database = FakeDatabaseGateway()
    app.dependency_overrides[get_database_gateway] = lambda: database
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/analytics/query", json={"question": QUESTION})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["rows"]) == 4
    assert payload["rows"][0]["department"] == "Engineering"
    assert payload["schema_version"] == "1.0"
    assert payload["status"] == "completed"
    assert payload["thread_id"]
    assert payload["columns"][0] == "department"
    assert payload["chart"]["type"] == "bar"
    assert payload["provenance"]["result"]["row_count"] == 4
    assert payload["provenance"]["debug"] is None
    assert payload["sources"] == ["synthetic-enterprise"]
    assert payload["freshness"]["status"] == "unknown"
    assert payload["execution"]["status"] == "completed"
    assert payload["execution"]["live"] is False
    assert payload["clarification_required"] is False


@pytest.mark.asyncio
async def test_default_development_api_requires_no_database_service() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/analytics/query", json={"question": QUESTION})

    assert response.status_code == 200
    assert response.json()["rows"][0]["department"] == "Engineering"


@pytest.mark.asyncio
async def test_raw_sql_is_only_exposed_by_explicit_debug_request() -> None:
    database = FakeDatabaseGateway()
    app.dependency_overrides[get_database_gateway] = lambda: database
    app.dependency_overrides[get_llm_gateway] = FakeLLMGateway
    app.dependency_overrides[get_settings] = _debug_settings
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/analytics/query",
                json={"question": QUESTION, "include_debug": True},
            )
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["provenance"]["debug"]["validated_sql"] == database.executed_sql[0]
    assert set(payload["provenance"]["debug"]["selected_schema_ids"]) >= {
        "analytics.departments",
        "analytics.employees",
    }
    assert "annual_base_salary" in payload["provenance"]["debug"]["semantic_definition_ids"]


@pytest.mark.asyncio
async def test_governed_metric_api_response_uses_normalized_contract() -> None:
    database = FakeDatabaseGateway()
    metrics = FakeMetricGateway()
    app.dependency_overrides[get_database_gateway] = lambda: database
    app.dependency_overrides[get_metric_gateway] = lambda: metrics
    app.dependency_overrides[get_llm_gateway] = FakeLLMGateway
    app.dependency_overrides[get_settings] = _debug_settings
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/analytics/query",
                json={
                    "question": "Total annual payroll by department",
                    "include_debug": True,
                },
            )
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["columns"] == ["department", "annual_base_payroll"]
    assert payload["provenance"]["source"] == "metric:fake"
    assert payload["provenance"]["debug"]["route"] == "governed_metric"
    assert payload["provenance"]["debug"]["metric_id"] == "annual_base_payroll"
    assert payload["provenance"]["debug"]["validated_sql"] is None
    assert database.executed_sql == []


@pytest.mark.asyncio
async def test_api_thread_id_carries_follow_up_context() -> None:
    saver = InMemorySaver()
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway()
    app.dependency_overrides[get_conversation_checkpointer] = lambda: saver
    app.dependency_overrides[get_settings] = _debug_settings
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            first = await client.post(
                "/analytics/query",
                json={"question": "Which department has the highest payroll?"},
            )
            thread_id = first.json()["thread_id"]
            second = await client.post(
                "/analytics/query",
                json={
                    "question": "What about last year?",
                    "thread_id": thread_id,
                    "include_debug": True,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["thread_id"] == thread_id
    assert "2025-01-01" in second.json()["provenance"]["debug"]["validated_sql"]


@pytest.mark.asyncio
async def test_debug_sql_requires_policy_capability_even_when_configured() -> None:
    app.dependency_overrides[get_database_gateway] = lambda: FakeDatabaseGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway()
    app.dependency_overrides[get_settings] = _debug_settings
    app.dependency_overrides[get_authenticated_identity] = lambda: UserIdentity(
        subject_id="hr-analyst",
        roles=("hr_analyst",),
        provider="test",
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/analytics/query",
                json={"question": QUESTION, "include_debug": True},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["provenance"]["debug"] is None


def test_openapi_documents_stable_success_and_error_contracts() -> None:
    schema = app.openapi()
    operation = schema["paths"]["/analytics/query"]["post"]

    assert operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert operation["responses"]["503"]["content"]["application/json"]["schema"]
    assert "AnalyticsResponse" in schema["components"]["schemas"]
    assert "ErrorResponse" in schema["components"]["schemas"]
