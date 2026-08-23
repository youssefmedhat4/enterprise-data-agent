import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import get_database_gateway, get_llm_gateway
from app.data.fake import FakeDatabaseGateway
from app.llm.fake import FakeLLMGateway
from app.main import app

QUESTION = (
    "Show each department, its number of employees, total salary, average salary, "
    "and highest paid employee, ordered by total payroll."
)


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
    assert payload["chart"]["chart_type"] == "bar"
    assert payload["provenance"]["row_count"] == 4
    assert payload["provenance"]["validated_sql"] == database.executed_sql[0]


@pytest.mark.asyncio
async def test_default_development_api_requires_no_database_service() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/analytics/query", json={"question": QUESTION})

    assert response.status_code == 200
    assert response.json()["rows"][0]["department"] == "Engineering"
