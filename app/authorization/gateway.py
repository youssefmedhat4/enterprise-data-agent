from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.authentication.gateway import UserIdentity
from app.data.gateway import ForeignKeyMetadata, TableMetadata


class AuthorizationTableResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: str
    table_name: str
    columns: tuple[str, ...]

    @property
    def identifier(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


class AuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: UserIdentity
    operation: str = "analytics.query"
    tables: tuple[AuthorizationTableResource, ...]
    metrics: tuple[str, ...]
    capabilities: tuple[str, ...] = ("debug_provenance",)


class AuthorizedScopeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemas: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()
    column_count: int = Field(default=0, ge=0)
    metrics: tuple[str, ...] = ()


class AuthorizationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    provider: str
    decision_id: str | None = None
    allowed_schemas: tuple[str, ...] = ()
    table_columns: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    allowed_metrics: tuple[str, ...] = ()
    debug_allowed: bool = False
    #: Whether this identity may review semantics, certify metrics, or
    #: register datasources. Separate from analytics access: reading data is
    #: not authority over what the data is defined to mean.
    knowledge_review_allowed: bool = False
    latency_ms: float = Field(default=0, ge=0)

    def scope_summary(self) -> AuthorizedScopeSummary:
        return AuthorizedScopeSummary(
            schemas=tuple(sorted(self.allowed_schemas)),
            tables=tuple(sorted(self.table_columns)),
            column_count=sum(len(columns) for columns in self.table_columns.values()),
            metrics=tuple(sorted(self.allowed_metrics)),
        )


class AuthorizationGatewayError(RuntimeError):
    """Base error for authorization providers."""


class AuthorizationDeniedError(AuthorizationGatewayError):
    """Raised when policy denies the requested analytics operation or resource."""


class AuthorizationProviderUnavailableError(AuthorizationGatewayError):
    """Raised when the configured policy provider cannot make a decision."""


class InvalidAuthorizationDecisionError(AuthorizationGatewayError):
    """Raised when a provider returns a malformed or out-of-scope decision."""


class AuthorizationGateway(Protocol):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Return the allowed resource scope for an authenticated identity."""

    async def close(self) -> None:
        """Release provider resources."""


def build_authorization_request(
    *,
    identity: UserIdentity,
    tables: list[TableMetadata],
    metrics: tuple[str, ...],
) -> AuthorizationRequest:
    return AuthorizationRequest(
        identity=identity,
        tables=tuple(
            AuthorizationTableResource(
                schema_name=table.schema_name,
                table_name=table.table_name,
                columns=tuple(table.columns),
            )
            for table in tables
        ),
        metrics=metrics,
    )


def filter_authorized_schema(
    tables: list[TableMetadata],
    decision: AuthorizationDecision,
) -> list[TableMetadata]:
    if not decision.allowed:
        return []
    allowed_table_ids = set(decision.table_columns)
    filtered: list[TableMetadata] = []
    for table in tables:
        allowed_columns = set(decision.table_columns.get(table.identifier, ()))
        if table.identifier not in allowed_table_ids or not allowed_columns:
            continue
        columns = [column for column in table.columns if column in allowed_columns]
        column_metadata = [
            column for column in table.column_metadata if column.name in allowed_columns
        ]
        primary_key = tuple(column for column in table.primary_key if column in allowed_columns)
        foreign_keys = tuple(
            foreign_key
            for foreign_key in table.foreign_keys
            if _foreign_key_is_authorized(
                foreign_key,
                local_columns=allowed_columns,
                decision=decision,
            )
        )
        filtered.append(
            replace(
                table,
                columns=columns,
                column_metadata=column_metadata,
                primary_key=primary_key,
                foreign_keys=foreign_keys,
            )
        )
    return filtered


def _foreign_key_is_authorized(
    foreign_key: ForeignKeyMetadata,
    *,
    local_columns: set[str],
    decision: AuthorizationDecision,
) -> bool:
    remote_columns = set(decision.table_columns.get(foreign_key.referenced_table, ()))
    return set(foreign_key.columns).issubset(local_columns) and set(
        foreign_key.referenced_columns
    ).issubset(remote_columns)
