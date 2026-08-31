from __future__ import annotations

from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.authorization.gateway import (
    AuthorizationDecision,
    AuthorizationGateway,
    AuthorizationProviderUnavailableError,
    AuthorizationRequest,
    InvalidAuthorizationDecisionError,
)


class _OPAResult(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    allow: bool = False
    allowed_schemas: tuple[str, ...] = ()
    allowed_tables: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    allowed_metrics: tuple[str, ...] = ()
    debug_allowed: bool = False
    knowledge_review_allowed: bool = False


class _OPAResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    result: _OPAResult
    decision_id: str | None = None


class OPAAuthorizationGateway(AuthorizationGateway):
    def __init__(
        self,
        *,
        base_url: str,
        decision_path: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
        )
        self._owns_client = client is None
        self._decision_path = "/" + decision_path.lstrip("/")

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        started = perf_counter()
        try:
            response = await self._client.post(
                self._decision_path,
                json={"input": _opa_input(request)},
            )
            response.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise AuthorizationProviderUnavailableError(
                "The configured authorization policy service is unavailable."
            ) from exc
        try:
            payload = _OPAResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise InvalidAuthorizationDecisionError(
                "The authorization policy service returned an invalid decision."
            ) from exc
        result = payload.result
        _validate_scope_subset(request, result)
        return AuthorizationDecision(
            allowed=result.allow,
            provider="opa",
            decision_id=payload.decision_id,
            allowed_schemas=result.allowed_schemas,
            table_columns=result.allowed_tables,
            allowed_metrics=result.allowed_metrics,
            debug_allowed=result.debug_allowed,
            knowledge_review_allowed=result.knowledge_review_allowed,
            latency_ms=round((perf_counter() - started) * 1000, 3),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _opa_input(request: AuthorizationRequest) -> dict[str, Any]:
    return {
        "identity": {
            "subject_id": request.identity.subject_id,
            "roles": list(request.identity.roles),
            "attributes": request.identity.attributes,
            "provider": request.identity.provider,
        },
        "operation": request.operation,
        "resources": {
            "tables": [
                {
                    "schema": table.schema_name,
                    "name": table.table_name,
                    "identifier": table.identifier,
                    "columns": list(table.columns),
                }
                for table in request.tables
            ],
            "metrics": list(request.metrics),
            "capabilities": list(request.capabilities),
        },
    }


def _validate_scope_subset(request: AuthorizationRequest, result: _OPAResult) -> None:
    requested_tables = {table.identifier: set(table.columns) for table in request.tables}
    if any(table not in requested_tables for table in result.allowed_tables):
        raise InvalidAuthorizationDecisionError(
            "The authorization decision referenced an unknown table."
        )
    if any(
        not set(columns).issubset(requested_tables[table])
        for table, columns in result.allowed_tables.items()
    ):
        raise InvalidAuthorizationDecisionError(
            "The authorization decision referenced an unknown column."
        )
    if not set(result.allowed_metrics).issubset(request.metrics):
        raise InvalidAuthorizationDecisionError(
            "The authorization decision referenced an unknown metric."
        )
