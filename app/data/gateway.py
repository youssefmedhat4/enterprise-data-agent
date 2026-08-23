from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class TableMetadata:
    schema_name: str
    table_name: str
    columns: list[str]
    description: str


class DatabaseGateway(Protocol):
    async def health_check(self) -> bool:
        """Return whether the database is reachable."""

    async def search_schema(self, question: str) -> list[TableMetadata]:
        """Return schema context relevant to the user's question."""

    async def execute_readonly(self, sql: str) -> list[dict[str, Any]]:
        """Execute validated read-only SQL and return structured rows."""

    async def close(self) -> None:
        """Close any pooled resources."""

