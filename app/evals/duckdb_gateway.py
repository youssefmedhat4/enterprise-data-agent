import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import duckdb

from app.data.gateway import (
    DatabaseExecutionMetadata,
    DatabaseGateway,
    DatabaseQueryResult,
    DatabaseSource,
    ResultColumnMetadata,
    TableMetadata,
)
from app.data.schema_metadata import synthetic_enterprise_metadata

DEFAULT_FIXTURE_PATH = Path(__file__).parents[2] / "evals" / "duckdb_schema.sql"


class DuckDBEvaluationGateway(DatabaseGateway):
    """Embedded SQL execution adapter used only by tests and evaluations."""

    def __init__(self, fixture_path: Path = DEFAULT_FIXTURE_PATH) -> None:
        self._connection = duckdb.connect(":memory:")
        self._connection.execute(fixture_path.read_text(encoding="utf-8"))

    def source(self) -> DatabaseSource:
        return DatabaseSource(
            identifier="synthetic-enterprise",
            dialect="duckdb",
            provider="duckdb",
        )

    async def health_check(self) -> bool:
        row = self._connection.execute("SELECT true").fetchone()
        return bool(row and row[0])

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        return synthetic_enterprise_metadata()

    async def execute_readonly(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> DatabaseQueryResult:
        started_at = perf_counter()
        executed_at = datetime.now(UTC)
        if parameters:
            cursor = self._connection.execute(sql, parameters)
        else:
            cursor = self._connection.execute(sql)
        columns = [description[0] for description in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        result_columns = [
            ResultColumnMetadata(name=description[0], data_type=str(description[1]))
            for description in cursor.description
        ]
        return DatabaseQueryResult(
            rows=rows,
            columns=result_columns,
            metadata=DatabaseExecutionMetadata(
                duration_ms=round((perf_counter() - started_at) * 1000, 3),
                executed_at=executed_at,
                row_count=len(rows),
                result_bytes=len(json.dumps(rows, default=str).encode("utf-8")),
                truncated=False,
                live=False,
            ),
        )

    async def close(self) -> None:
        self._connection.close()
