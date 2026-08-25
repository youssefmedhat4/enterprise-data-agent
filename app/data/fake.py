from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from app.data.gateway import (
    DatabaseExecutionMetadata,
    DatabaseGateway,
    DatabaseQueryResult,
    DatabaseSource,
    ResultColumnMetadata,
    TableMetadata,
)
from app.data.schema_metadata import synthetic_enterprise_metadata


class FakeDatabaseGateway(DatabaseGateway):
    def __init__(
        self,
        results_by_sql: Mapping[str, list[dict[str, Any]]] | None = None,
        *,
        strict_results: bool = False,
    ) -> None:
        self.executed_sql: list[str] = []
        self._results_by_sql = dict(results_by_sql or {})
        self._strict_results = strict_results

    def source(self) -> DatabaseSource:
        return DatabaseSource(
            identifier="synthetic-enterprise",
            dialect="postgres",
            provider="fake",
        )

    async def health_check(self) -> bool:
        return True

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        return synthetic_enterprise_metadata()

    async def execute_readonly(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> DatabaseQueryResult:
        del parameters
        started_at = perf_counter()
        executed_at = datetime.now(UTC)
        self.executed_sql.append(sql)
        if sql in self._results_by_sql:
            rows = [row.copy() for row in self._results_by_sql[sql]]
        elif self._strict_results:
            raise ValueError("No deterministic fake result is configured for this SQL.")
        else:
            rows = [
                {
                    "department": "Engineering",
                    "employee_count": 4,
                    "total_salary": "610000.00",
                    "average_salary": "152500.00",
                    "highest_paid_employee": "Maya Haddad",
                },
                {
                    "department": "Sales",
                    "employee_count": 3,
                    "total_salary": "375000.00",
                    "average_salary": "125000.00",
                    "highest_paid_employee": "Noura Mansour",
                },
                {
                    "department": "Finance",
                    "employee_count": 2,
                    "total_salary": "255000.00",
                    "average_salary": "127500.00",
                    "highest_paid_employee": "Omar Farouk",
                },
                {
                    "department": "People Operations",
                    "employee_count": 1,
                    "total_salary": "135000.00",
                    "average_salary": "135000.00",
                    "highest_paid_employee": "Dalia Fawzi",
                },
            ]
        columns = [
            ResultColumnMetadata(name=name, data_type="unknown")
            for name in (rows[0] if rows else {})
        ]
        return DatabaseQueryResult(
            rows=rows,
            columns=columns,
            metadata=DatabaseExecutionMetadata(
                duration_ms=round((perf_counter() - started_at) * 1000, 3),
                executed_at=executed_at,
                row_count=len(rows),
                result_bytes=len(str(rows).encode("utf-8")),
                truncated=False,
                live=False,
            ),
        )

    async def close(self) -> None:
        return None
