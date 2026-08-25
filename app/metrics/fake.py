from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.metrics.catalog import GOVERNED_METRICS, metric_definition, validate_metric_query
from app.metrics.gateway import (
    MetricDefinition,
    MetricGateway,
    MetricOrderDirection,
    MetricQuery,
    MetricResult,
    MetricResultProvenance,
)

_ANNUAL_PAYROLL: tuple[dict[str, Any], ...] = (
    {"department": "Engineering", "annual_base_payroll": Decimal("710000.00")},
    {"department": "Sales", "annual_base_payroll": Decimal("375000.00")},
    {"department": "Finance", "annual_base_payroll": Decimal("255000.00")},
    {"department": "People Operations", "annual_base_payroll": Decimal("225000.00")},
)


class FakeMetricGateway(MetricGateway):
    """Deterministic governed results for graph and API contract tests."""

    def __init__(self) -> None:
        self.queries: list[MetricQuery] = []

    async def list_metrics(self) -> tuple[MetricDefinition, ...]:
        return GOVERNED_METRICS

    async def describe_metric(self, metric_id: str) -> MetricDefinition:
        return metric_definition(metric_id)

    async def query_metric(self, query: MetricQuery) -> MetricResult:
        definition = validate_metric_query(query)
        self.queries.append(query)
        rows = self._rows(query)
        return MetricResult(
            columns=tuple(rows[0]) if rows else (*query.dimensions, query.metric),
            rows=tuple(rows),
            provenance=MetricResultProvenance(
                metric_provider="fake",
                metric_id=query.metric,
                metric_definition_id=definition.definition_id,
                metric_version=definition.version,
                dimensions=query.dimensions,
                filters=query.filters,
                time_dimension=query.time_dimension,
                time_grain=query.time_grain,
                date_range=query.date_range,
                order=query.order,
                source_models=definition.source_models,
                source_tables=definition.source_tables,
                query_id=str(uuid4()),
                retrieved_at=datetime.now(UTC),
                metric_retrieval_latency_ms=0,
                metric_execution_latency_ms=0,
            ),
        )

    def _rows(self, query: MetricQuery) -> list[dict[str, Any]]:
        if query.metric != "annual_base_payroll":
            return [{query.metric: Decimal("0")}]
        if "department" not in query.dimensions:
            return [{query.metric: Decimal("1565000.00")}]
        rows = [dict(row) for row in _ANNUAL_PAYROLL]
        for filter_ in query.filters:
            if filter_.dimension == "department":
                rows = [row for row in rows if str(row["department"]) in filter_.values]
        if query.order and query.order[0].direction == MetricOrderDirection.ASC:
            rows.sort(key=lambda row: row[query.metric])
        elif query.order:
            rows.sort(key=lambda row: row[query.metric], reverse=True)
        return rows[: query.limit]

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None
