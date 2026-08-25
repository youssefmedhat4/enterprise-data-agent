from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.authorization.gateway import (
    AuthorizationDecision,
    AuthorizationGateway,
    AuthorizationProviderUnavailableError,
    AuthorizationRequest,
)


class _RoleGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemas: tuple[str, ...]
    tables: tuple[str, ...]
    denied_columns: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    metrics: tuple[str, ...]
    debug: bool = False


class _LocalPolicyData(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    roles: dict[str, _RoleGrant]


class LocalPolicyAuthorizationGateway(AuthorizationGateway):
    """Evaluate checked-in development policy data without requiring an OPA process."""

    def __init__(self, policy_path: Path) -> None:
        self._policy_path = policy_path

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        started = perf_counter()
        policy = self._load_policy()
        grants = [policy.roles[role] for role in request.identity.roles if role in policy.roles]
        table_columns: dict[str, tuple[str, ...]] = {}
        for table in request.tables:
            columns = tuple(
                column
                for column in table.columns
                if any(
                    _grant_allows_column(
                        grant,
                        table.schema_name,
                        table.identifier,
                        column,
                    )
                    for grant in grants
                )
            )
            if columns:
                table_columns[table.identifier] = columns
        allowed_metrics = tuple(
            metric
            for metric in request.metrics
            if any(_grant_allows(grant.metrics, metric) for grant in grants)
        )
        schemas = tuple(
            sorted(
                {
                    table.schema_name
                    for table in request.tables
                    if table.identifier in table_columns
                }
            )
        )
        allowed = bool(grants and (table_columns or allowed_metrics))
        return AuthorizationDecision(
            allowed=allowed,
            provider="local_policy",
            decision_id=f"local-{uuid4()}",
            allowed_schemas=schemas,
            table_columns=table_columns,
            allowed_metrics=allowed_metrics,
            debug_allowed=any(grant.debug for grant in grants),
            latency_ms=round((perf_counter() - started) * 1000, 3),
        )

    def _load_policy(self) -> _LocalPolicyData:
        try:
            return _LocalPolicyData.model_validate_json(
                self._policy_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            raise AuthorizationProviderUnavailableError(
                "The local development authorization policy is unavailable."
            ) from exc

    async def close(self) -> None:
        return None


def _grant_allows(values: tuple[str, ...], candidate: str) -> bool:
    return "*" in values or candidate in values


def _grant_allows_column(
    grant: _RoleGrant,
    schema: str,
    table: str,
    column: str,
) -> bool:
    if not _grant_allows(grant.schemas, schema) or not _grant_allows(grant.tables, table):
        return False
    denied = {*grant.denied_columns.get("*", ()), *grant.denied_columns.get(table, ())}
    return column not in denied
