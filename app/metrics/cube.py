from __future__ import annotations

from datetime import UTC, date, datetime
from time import perf_counter
from typing import Any, Protocol
from uuid import uuid4

import httpx

from app.metrics.catalog import GOVERNED_METRICS, metric_definition, validate_metric_query
from app.metrics.gateway import (
    MetricDefinition,
    MetricExecutionError,
    MetricFilterOperator,
    MetricGateway,
    MetricProviderUnavailableError,
    MetricQuery,
    MetricResult,
    MetricResultProvenance,
)


class CubeClient(Protocol):
    async def load(self, query: dict[str, Any]) -> dict[str, Any]:
        """Execute one structured Cube REST query."""

    async def health_check(self) -> bool:
        """Return whether Cube Core is ready."""

    async def close(self) -> None:
        """Release client resources."""


class HTTPCubeClient:
    """Minimal async client for Cube Core's stable REST query API."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        api_token: str | None = None,
    ) -> None:
        headers = {"Authorization": api_token} if api_token else None
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers=headers,
        )

    async def load(self, query: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post("/cubejs-api/v1/load", json={"query": query})
            response.raise_for_status()
        except httpx.RequestError as exc:
            raise MetricProviderUnavailableError(
                "The configured Cube Core service is unavailable."
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise MetricProviderUnavailableError(
                    "The configured Cube Core service could not serve the request."
                ) from exc
            raise MetricExecutionError(
                "Cube Core rejected the governed metric query."
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise MetricProviderUnavailableError(
                "Cube Core returned an invalid response."
            ) from exc
        if not isinstance(payload, dict):
            raise MetricProviderUnavailableError("Cube Core returned an invalid response.")
        return payload

    async def health_check(self) -> bool:
        try:
            response = await self._client.get("/readyz")
            return response.is_success
        except httpx.RequestError:
            return False

    async def close(self) -> None:
        await self._client.aclose()


class CubeMetricGateway(MetricGateway):
    """Execute validated governed metrics through Cube Core's native REST API."""

    def __init__(self, client: CubeClient) -> None:
        self._client = client

    async def list_metrics(self) -> tuple[MetricDefinition, ...]:
        return GOVERNED_METRICS

    async def describe_metric(self, metric_id: str) -> MetricDefinition:
        return metric_definition(metric_id)

    async def query_metric(self, query: MetricQuery) -> MetricResult:
        definition = validate_metric_query(query)
        retrieval_started = perf_counter()
        cube_query, members = _cube_query(query)
        retrieval_latency_ms = (perf_counter() - retrieval_started) * 1000
        execution_started = perf_counter()
        response = await self._client.load(cube_query)
        execution_latency_ms = (perf_counter() - execution_started) * 1000
        rows = _normalize_rows(response, members, query=query)
        request_id = response.get("requestId")
        if not isinstance(request_id, str) or not request_id:
            request_id = str(uuid4())
        return MetricResult(
            columns=tuple(member.output_name for member in members),
            rows=tuple(rows),
            provenance=MetricResultProvenance(
                metric_provider="cube",
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
                query_id=request_id,
                retrieved_at=datetime.now(UTC),
                metric_retrieval_latency_ms=round(retrieval_latency_ms, 3),
                metric_execution_latency_ms=round(execution_latency_ms, 3),
            ),
        )

    async def health_check(self) -> bool:
        return await self._client.health_check()

    async def close(self) -> None:
        await self._client.close()


class _ResultMember:
    def __init__(self, provider_name: str, output_name: str) -> None:
        self.provider_name = provider_name
        self.output_name = output_name


def _cube_query(query: MetricQuery) -> tuple[dict[str, Any], tuple[_ResultMember, ...]]:
    cube = _CUBE_MODELS[query.metric]
    measure_member = f"{cube}.{query.metric}"
    payload: dict[str, Any] = {
        "measures": [measure_member],
        "limit": query.limit,
    }
    members: list[_ResultMember] = []
    if query.dimensions:
        dimension_members = [f"{cube}.{dimension}" for dimension in query.dimensions]
        payload["dimensions"] = dimension_members
        members.extend(
            _ResultMember(provider_name, output_name)
            for provider_name, output_name in zip(
                dimension_members,
                query.dimensions,
                strict=True,
            )
        )
    if query.filters:
        payload["filters"] = [
            {
                "member": f"{cube}.{filter_.dimension}",
                "operator": _CUBE_FILTER_OPERATORS[filter_.operator],
                **({} if not filter_.values else {"values": list(filter_.values)}),
            }
            for filter_ in query.filters
        ]
    if query.time_dimension:
        provider_name = f"{cube}.{query.time_dimension}"
        time_dimension: dict[str, Any] = {"dimension": provider_name}
        if query.time_grain:
            time_dimension["granularity"] = query.time_grain.value
            result_name = f"{provider_name}.{query.time_grain.value}"
        else:
            result_name = provider_name
        if query.date_range:
            time_dimension["dateRange"] = [value.isoformat() for value in query.date_range]
        payload["timeDimensions"] = [time_dimension]
        members.append(_ResultMember(result_name, query.time_dimension))
    if query.order:
        payload["order"] = [
            [
                measure_member if item.member == query.metric else f"{cube}.{item.member}",
                item.direction.value,
            ]
            for item in query.order
        ]
    members.append(_ResultMember(measure_member, query.metric))
    return payload, tuple(members)


def _normalize_rows(
    response: dict[str, Any],
    members: tuple[_ResultMember, ...],
    *,
    query: MetricQuery,
) -> list[dict[str, Any]]:
    data = response.get("data")
    if not isinstance(data, list):
        raise MetricProviderUnavailableError("Cube Core returned no result data.")
    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            raise MetricProviderUnavailableError("Cube Core returned an invalid result row.")
        rows.append(
            {
                member.output_name: _normalized_member_value(
                    item.get(member.provider_name),
                    is_time_dimension=member.output_name == query.time_dimension,
                )
                for member in members
            }
        )
    return rows


def _normalized_member_value(value: Any, *, is_time_dimension: bool) -> Any:
    if is_time_dimension and isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return value
    return value


_CUBE_MODELS = {
    "active_headcount": "workforce_metrics",
    "annual_base_payroll": "workforce_metrics",
    "net_payroll": "payroll_metrics",
    "invoice_amount": "invoice_metrics",
    "project_cost": "project_cost_metrics",
    "project_margin": "project_financial_metrics",
    "budget_utilization": "project_financial_metrics",
}

_CUBE_FILTER_OPERATORS = {
    MetricFilterOperator.EQ: "equals",
    MetricFilterOperator.NEQ: "notEquals",
    MetricFilterOperator.IN: "equals",
    MetricFilterOperator.NOT_IN: "notEquals",
    MetricFilterOperator.GT: "gt",
    MetricFilterOperator.GTE: "gte",
    MetricFilterOperator.LT: "lt",
    MetricFilterOperator.LTE: "lte",
    MetricFilterOperator.CONTAINS: "contains",
    MetricFilterOperator.STARTS_WITH: "startsWith",
    MetricFilterOperator.IS_NULL: "notSet",
    MetricFilterOperator.IS_NOT_NULL: "set",
}
