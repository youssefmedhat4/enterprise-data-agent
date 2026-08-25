from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.data.gateway import (
    DatabaseGateway,
    DatabaseQueryResult,
    DatabaseSource,
    TableMetadata,
    query_result_from_rows,
)
from app.errors import ErrorCode, normalize_error
from app.metrics.catalog import GOVERNED_METRICS, validate_metric_query
from app.metrics.cube import CubeMetricGateway
from app.metrics.factory import (
    build_experimental_wren_metric_gateway,
    build_metric_gateway,
)
from app.metrics.gateway import (
    MetricFilter,
    MetricProviderUnavailableError,
    MetricQuery,
    MetricQueryValidationError,
)
from app.metrics.wren import WrenCubeMetricGateway
from app.security.sql_validation import SQLValidationError


class StubDatabase(DatabaseGateway):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.executed_sql: str | None = None

    def source(self) -> DatabaseSource:
        return DatabaseSource(identifier="test", dialect="postgres")

    async def health_check(self) -> bool:
        return True

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        return []

    async def execute_readonly(
        self,
        sql: str,
        parameters: Sequence[Any] = (),
    ) -> DatabaseQueryResult:
        del parameters
        self.executed_sql = sql
        return query_result_from_rows(self.rows)

    async def close(self) -> None:
        return None


class StubWrenClient:
    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.queries: list[dict[str, Any]] = []

    async def translate(self, cube_query: dict[str, Any]) -> str:
        self.queries.append(cube_query)
        return self.sql

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class StubCubeClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.queries: list[dict[str, Any]] = []

    async def load(self, query: dict[str, Any]) -> dict[str, Any]:
        self.queries.append(query)
        return self.response

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def test_catalog_contains_seven_versioned_governed_metrics() -> None:
    assert {metric.id for metric in GOVERNED_METRICS} == {
        "active_headcount",
        "annual_base_payroll",
        "net_payroll",
        "invoice_amount",
        "project_cost",
        "project_margin",
        "budget_utilization",
    }
    assert all(metric.definition_id and metric.version == "1.0" for metric in GOVERNED_METRICS)


def test_metric_query_has_no_formula_override_field() -> None:
    with pytest.raises(ValidationError):
        MetricQuery.model_validate(
            {
                "metric": "annual_base_payroll",
                "formula": "SUM(salary * 2)",
            }
        )


@pytest.mark.parametrize(
    "query",
    [
        MetricQuery(metric="not_a_metric"),
        MetricQuery(metric="annual_base_payroll", dimensions=("customer",)),
        MetricQuery(
            metric="budget_utilization",
            filters=(MetricFilter(dimension="raw_sql", operator="eq", values=("1=1",)),),
        ),
    ],
)
def test_invalid_metric_members_are_rejected(query: MetricQuery) -> None:
    with pytest.raises(MetricQueryValidationError):
        validate_metric_query(query)


async def test_wren_gateway_translates_validates_executes_and_records_provenance() -> None:
    client = StubWrenClient(
        "SELECT SUM(salary) AS annual_base_payroll FROM analytics.employees"
    )
    database = StubDatabase([{"annual_base_payroll": 1_565_000}])
    gateway = WrenCubeMetricGateway(client, database)

    result = await gateway.query_metric(MetricQuery(metric="annual_base_payroll"))

    assert client.queries == [
        {"cube": "workforce_metrics", "measures": ["annual_base_payroll"], "limit": 100}
    ]
    assert database.executed_sql is not None
    assert "LIMIT 100" in database.executed_sql
    assert result.rows == ({"annual_base_payroll": 1_565_000},)
    assert result.provenance.metric_provider == "wren"
    assert result.provenance.generated_sql == database.executed_sql


async def test_wren_gateway_keeps_sqlglot_between_translation_and_execution() -> None:
    gateway = WrenCubeMetricGateway(
        StubWrenClient("DELETE FROM analytics.employees"),
        StubDatabase([]),
    )

    with pytest.raises(SQLValidationError):
        await gateway.query_metric(MetricQuery(metric="active_headcount"))


async def test_wren_gateway_normalizes_inclusive_date_range_to_half_open_interval() -> None:
    client = StubWrenClient(
        "SELECT SUM(quantity * unit_price) AS invoice_amount "
        "FROM analytics.invoice_lines"
    )
    gateway = WrenCubeMetricGateway(client, StubDatabase([{"invoice_amount": 95_000}]))

    await gateway.query_metric(
        MetricQuery(
            metric="invoice_amount",
            time_dimension="invoice_issued",
            time_grain="month",
            date_range=(date(2025, 1, 1), date(2025, 1, 31)),
        )
    )

    assert client.queries[0]["timeDimensions"] == [
        {
            "dimension": "invoice_issued",
            "granularity": "month",
            "dateRange": ["2025-01-01", "2025-02-01"],
        }
    ]


async def test_cube_gateway_uses_only_structured_governed_members() -> None:
    client = StubCubeClient(
        {
            "requestId": "cube-request",
            "data": [
                {
                    "workforce_metrics.department": "Engineering",
                    "workforce_metrics.active_headcount": "4",
                }
            ],
        }
    )
    gateway = CubeMetricGateway(client)

    result = await gateway.query_metric(
        MetricQuery(
            metric="active_headcount",
            dimensions=("department",),
            filters=(
                MetricFilter(
                    dimension="department",
                    operator="eq",
                    values=("Engineering",),
                ),
            ),
        )
    )

    assert client.queries == [
        {
            "measures": ["workforce_metrics.active_headcount"],
            "dimensions": ["workforce_metrics.department"],
            "filters": [
                {
                    "member": "workforce_metrics.department",
                    "operator": "equals",
                    "values": ["Engineering"],
                }
            ],
            "limit": 100,
        }
    ]
    assert result.rows == ({"department": "Engineering", "active_headcount": "4"},)
    assert result.provenance.metric_provider == "cube"
    assert result.provenance.generated_sql is None


def test_metric_factory_selection_is_explicit() -> None:
    wren = build_experimental_wren_metric_gateway(
        Settings(),
        database=StubDatabase([]),
    )
    cube = build_metric_gateway(Settings(METRIC_PROVIDER="cube"))

    assert isinstance(wren, WrenCubeMetricGateway)
    assert isinstance(cube, CubeMetricGateway)


def test_metric_provider_unavailable_is_sanitized_without_fallback() -> None:
    normalized = normalize_error(
        MetricProviderUnavailableError("internal provider detail"),
        request_id="request-1",
    )

    assert normalized.code == ErrorCode.METRIC_PROVIDER_UNAVAILABLE
    assert normalized.safe_message == (
        "The configured governed metric provider is temporarily unavailable."
    )
    assert "internal provider detail" not in normalized.safe_message
