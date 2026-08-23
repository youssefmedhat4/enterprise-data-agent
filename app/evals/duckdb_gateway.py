from pathlib import Path
from typing import Any

import duckdb

from app.data.gateway import DatabaseGateway, TableMetadata

DEFAULT_FIXTURE_PATH = Path(__file__).parents[2] / "evals" / "duckdb_schema.sql"


class DuckDBEvaluationGateway(DatabaseGateway):
    """Embedded SQL execution adapter used only by tests and evaluations."""

    def __init__(self, fixture_path: Path = DEFAULT_FIXTURE_PATH) -> None:
        self._connection = duckdb.connect(":memory:")
        self._connection.execute(fixture_path.read_text(encoding="utf-8"))

    async def health_check(self) -> bool:
        row = self._connection.execute("SELECT true").fetchone()
        return bool(row and row[0])

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        rows = self._connection.execute(
            """
            SELECT
                table_schema,
                table_name,
                list(column_name ORDER BY ordinal_position) AS columns
            FROM information_schema.columns
            WHERE table_schema = 'analytics'
            GROUP BY table_schema, table_name
            ORDER BY table_name
            """
        ).fetchall()
        return [
            TableMetadata(
                schema_name=row[0],
                table_name=row[1],
                columns=list(row[2]),
                description="Synthetic enterprise evaluation table.",
            )
            for row in rows
        ]

    async def execute_readonly(self, sql: str) -> list[dict[str, Any]]:
        cursor = self._connection.execute(sql)
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    async def close(self) -> None:
        self._connection.close()
