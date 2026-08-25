import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ColumnMetadata:
    name: str
    data_type: str
    nullable: bool
    description: str = ""
    primary_key: bool = False
    observed_values: tuple[str, ...] = ()
    observed_values_source: Literal["fixture", "database"] | None = None
    date_meaning: str | None = None


@dataclass(frozen=True)
class ForeignKeyMetadata:
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]


@dataclass(frozen=True)
class TableMetadata:
    schema_name: str
    table_name: str
    columns: list[str]
    description: str
    column_metadata: list[ColumnMetadata] = field(default_factory=list)
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[ForeignKeyMetadata, ...] = ()
    object_type: Literal[
        "table", "partitioned_table", "view", "materialized_view", "foreign_table"
    ] = "table"

    @property
    def identifier(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


@dataclass(frozen=True)
class DatabaseSource:
    identifier: str
    dialect: str
    provider: str = "direct"
    freshness_as_of: datetime | None = None


@dataclass(frozen=True)
class ResultColumnMetadata:
    name: str
    data_type: str


@dataclass(frozen=True)
class DatabaseExecutionMetadata:
    duration_ms: float
    executed_at: datetime
    row_count: int
    result_bytes: int
    truncated: bool
    live: bool


@dataclass(frozen=True)
class DatabaseQueryResult:
    rows: list[dict[str, Any]]
    columns: list[ResultColumnMetadata]
    metadata: DatabaseExecutionMetadata


def query_result_from_rows(
    rows: list[dict[str, Any]],
    *,
    column_names: Sequence[str] | None = None,
    duration_ms: float = 0,
    truncated: bool = False,
    live: bool = False,
) -> DatabaseQueryResult:
    return DatabaseQueryResult(
        rows=rows,
        columns=[
            ResultColumnMetadata(name=column, data_type="unknown")
            for column in (column_names if column_names is not None else (rows[0] if rows else {}))
        ],
        metadata=DatabaseExecutionMetadata(
            duration_ms=duration_ms,
            executed_at=datetime.now(UTC),
            row_count=len(rows),
            result_bytes=len(json.dumps(rows, default=str).encode("utf-8")),
            truncated=truncated,
            live=live,
        ),
    )


class DatabaseGatewayError(RuntimeError):
    """Base error for database adapter failures."""


class DatabaseUnavailableError(DatabaseGatewayError):
    """Raised when the configured database cannot be reached."""


class DatabaseQueryTimeoutError(DatabaseGatewayError):
    """Raised when a read-only query exceeds its timeout."""


class DatabaseReadOnlyConfigurationError(DatabaseGatewayError):
    """Raised when database credentials cannot be verified as read-only."""


class DatabasePermissionError(DatabaseGatewayError):
    """Raised when the database role lacks required read access."""


class DatabaseQueryExecutionError(DatabaseGatewayError):
    """Raised when PostgreSQL rejects an otherwise validated read-only query."""


class DatabaseResultTooLargeError(DatabaseGatewayError):
    """Raised when even one result row exceeds the configured byte budget."""


class DatabaseGateway(Protocol):
    def source(self) -> DatabaseSource:
        """Return non-sensitive source metadata for provenance."""

    async def health_check(self) -> bool:
        """Return whether the database is reachable."""

    async def search_schema(self, question: str) -> list[TableMetadata]:
        """Return schema context relevant to the user's question."""

    async def execute_readonly(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> DatabaseQueryResult:
        """Execute validated read-only SQL and return bounded structured results."""

    async def close(self) -> None:
        """Close any pooled resources."""
