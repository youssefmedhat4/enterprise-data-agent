from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, Protocol, cast
from uuid import uuid4

from app.data.gateway import DatabaseGateway
from app.metrics.catalog import GOVERNED_METRICS, metric_definition, validate_metric_query
from app.metrics.gateway import (
    MetricDefinition,
    MetricGateway,
    MetricProviderUnavailableError,
    MetricQuery,
    MetricResult,
    MetricResultProvenance,
)
from app.security.sql_validation import SQLValidator


class WrenCubeClient(Protocol):
    async def translate(self, cube_query: dict[str, Any]) -> str:
        """Translate a structured Wren CubeQuery to physical PostgreSQL SQL."""

    async def health_check(self) -> bool:
        """Return whether Wren's local translation service is reachable."""

    async def close(self) -> None:
        """Release client resources."""


class MCPWrenCubeClient:
    """Client for the credential-free Wren cube translation MCP tool."""

    def __init__(self, url: str, *, timeout_seconds: float) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds

    async def translate(self, cube_query: dict[str, Any]) -> str:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise MetricProviderUnavailableError(
                "The Wren MCP client dependency is not installed."
            ) from exc

        try:
            async with (
                asyncio.timeout(self._timeout_seconds),
                streamable_http_client(self._url) as (read_stream, write_stream, _),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                payload = _tool_payload(
                    await session.call_tool(
                        "translate_cube_query",
                        {"query": cube_query},
                    )
                )
        except MetricProviderUnavailableError:
            raise
        except (OSError, TimeoutError, ConnectionError) as exc:
            raise MetricProviderUnavailableError(
                "The configured Wren cube translation service is unavailable."
            ) from exc
        except Exception as exc:
            raise MetricProviderUnavailableError(
                "The configured Wren cube translation service rejected the request."
            ) from exc

        sql = payload.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise MetricProviderUnavailableError(
                "The Wren cube translation service returned no SQL."
            )
        return sql

    async def health_check(self) -> bool:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with (
                asyncio.timeout(self._timeout_seconds),
                streamable_http_client(self._url) as (read_stream, write_stream, _),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        return None


class WrenCubeMetricGateway(MetricGateway):
    """Translate Wren cubes, then validate and execute through DatabaseGateway."""

    def __init__(
        self,
        client: WrenCubeClient,
        database: DatabaseGateway,
        *,
        validator: SQLValidator | None = None,
    ) -> None:
        self._client = client
        self._database = database
        self._validator = validator or SQLValidator()

    async def list_metrics(self) -> tuple[MetricDefinition, ...]:
        return GOVERNED_METRICS

    async def describe_metric(self, metric_id: str) -> MetricDefinition:
        return metric_definition(metric_id)

    async def query_metric(self, query: MetricQuery) -> MetricResult:
        definition = validate_metric_query(query)
        cube_query = _wren_cube_query(query)
        retrieval_started = perf_counter()
        candidate_sql = await self._client.translate(cube_query)
        retrieval_latency_ms = (perf_counter() - retrieval_started) * 1000
        validated_sql = self._validator.validate_readonly(candidate_sql)
        execution_started = perf_counter()
        database_result = await self._database.execute_readonly(validated_sql)
        execution_latency_ms = (perf_counter() - execution_started) * 1000
        rows = _normalize_wren_rows(query, database_result.rows)
        columns = tuple(rows[0]) if rows else _expected_columns(query)
        return MetricResult(
            columns=columns,
            rows=tuple(rows),
            provenance=MetricResultProvenance(
                metric_provider="wren",
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
                metric_retrieval_latency_ms=round(retrieval_latency_ms, 3),
                metric_execution_latency_ms=round(execution_latency_ms, 3),
                generated_sql=validated_sql,
            ),
        )

    async def health_check(self) -> bool:
        return await self._client.health_check() and await self._database.health_check()

    async def close(self) -> None:
        await self._client.close()


def _wren_cube_query(query: MetricQuery) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "cube": _WREN_CUBES[query.metric],
        "measures": [query.metric],
        "limit": query.limit,
    }
    if query.dimensions:
        payload["dimensions"] = list(query.dimensions)
    if query.filters:
        payload["filters"] = [
            {
                "dimension": filter_.dimension,
                "operator": filter_.operator.value,
                **(
                    {}
                    if not filter_.values
                    else {
                        "values" if filter_.operator.value in {"in", "not_in"} else "value": (
                            list(filter_.values)
                            if filter_.operator.value in {"in", "not_in"}
                            else filter_.values[0]
                        )
                    }
                ),
            }
            for filter_ in query.filters
        ]
    if query.order:
        payload["order"] = [
            {"member": item.member, "direction": item.direction.value}
            for item in query.order
        ]
    if query.time_dimension:
        time_dimension: dict[str, Any] = {"dimension": query.time_dimension}
        if query.time_grain:
            time_dimension["granularity"] = query.time_grain.value
        if query.date_range:
            time_dimension["dateRange"] = [
                query.date_range[0].isoformat(),
                (query.date_range[1] + timedelta(days=1)).isoformat(),
            ]
        payload["timeDimensions"] = [time_dimension]
    return payload


def _tool_payload(result: Any) -> dict[str, Any]:
    if bool(getattr(result, "isError", False) or getattr(result, "is_error", False)):
        raise MetricProviderUnavailableError("A Wren cube translation tool reported an error.")
    structured = getattr(result, "structuredContent", None) or getattr(
        result, "structured_content", None
    )
    if isinstance(structured, Mapping):
        return dict(structured)
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return cast(dict[str, Any], payload)
    raise MetricProviderUnavailableError(
        "A Wren cube translation tool returned no structured data."
    )


_WREN_CUBES = {
    "active_headcount": "workforce_metrics",
    "annual_base_payroll": "workforce_metrics",
    "net_payroll": "payroll_metrics",
    "invoice_amount": "invoice_metrics",
    "project_cost": "project_cost_metrics",
    "project_margin": "project_financial_metrics",
    "budget_utilization": "project_financial_metrics",
}


def _normalize_wren_rows(
    query: MetricQuery,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if query.time_dimension is None or query.time_grain is None:
        return rows
    provider_name = f"{query.time_dimension}__{query.time_grain.value}"
    return [
        {
            query.time_dimension if key == provider_name else key: (
                value.date() if key == provider_name and isinstance(value, datetime) else value
            )
            for key, value in row.items()
        }
        for row in rows
    ]


def _expected_columns(query: MetricQuery) -> tuple[str, ...]:
    columns = [*query.dimensions]
    if query.time_dimension:
        columns.append(query.time_dimension)
    columns.append(query.metric)
    return tuple(columns)
