"""PostgreSQL-backed quality assertions and their most recent results.

Only the latest result per assertion is read back. A reader asks whether a table
is healthy now and whether that just changed; keeping a full time series here
would turn a health signal into a metrics store with none of the tooling.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.knowledge.quality import (
    AssertionType,
    QualityAssertion,
    QualityCheckResult,
    QualityStatus,
    QualityStore,
)

#: How many results to keep per assertion. Enough to see a change, not a series.
RETAINED_RESULTS = 20


class PostgresQualityStore(QualityStore):
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def upsert(self, assertion: QualityAssertion) -> QualityAssertion:
        async with (
            self._pool.connection() as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(
                "INSERT INTO knowledge.quality_assertions"
                " (id, data_source_id, name, assertion_type, schema_name,"
                "  table_name, column_name, configuration, enabled, created_by,"
                "  created_at, updated_at)"
                " VALUES (%(id)s, %(data_source_id)s, %(name)s, %(type)s,"
                "  %(schema_name)s, %(table_name)s, %(column_name)s, %(config)s,"
                "  %(enabled)s, %(created_by)s, %(created_at)s, now())"
                " ON CONFLICT (id) DO UPDATE SET"
                "  name = EXCLUDED.name,"
                "  assertion_type = EXCLUDED.assertion_type,"
                "  schema_name = EXCLUDED.schema_name,"
                "  table_name = EXCLUDED.table_name,"
                "  column_name = EXCLUDED.column_name,"
                "  configuration = EXCLUDED.configuration,"
                "  enabled = EXCLUDED.enabled,"
                "  updated_at = now()",
                {
                    "id": assertion.id,
                    "data_source_id": assertion.data_source_id,
                    "name": assertion.name,
                    "type": assertion.assertion_type.value,
                    "schema_name": assertion.schema_name,
                    "table_name": assertion.table_name,
                    "column_name": assertion.column_name,
                    "config": Jsonb(assertion.configuration),
                    "enabled": assertion.enabled,
                    "created_by": assertion.created_by,
                    "created_at": assertion.created_at,
                },
            )
        stored = await self.assertion(assertion.data_source_id, assertion.id)
        return stored if stored is not None else assertion

    async def assertions(
        self, data_source_id: UUID, *, enabled_only: bool = False
    ) -> list[QualityAssertion]:
        clause = " AND enabled" if enabled_only else ""
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT * FROM knowledge.quality_assertions"
                " WHERE data_source_id = %(data_source_id)s" + clause + " ORDER BY name",
                {"data_source_id": data_source_id},
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        return [_to_assertion(row) for row in rows]

    async def assertion(
        self, data_source_id: UUID, assertion_id: UUID
    ) -> QualityAssertion | None:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT * FROM knowledge.quality_assertions"
                " WHERE data_source_id = %(data_source_id)s AND id = %(id)s",
                {"data_source_id": data_source_id, "id": assertion_id},
            )
            row = cast("dict[str, Any] | None", await cursor.fetchone())
        return _to_assertion(row) if row is not None else None

    async def record(self, result: QualityCheckResult) -> QualityCheckResult:
        async with (
            self._pool.connection() as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(
                "INSERT INTO knowledge.quality_check_results"
                " (id, assertion_id, data_source_id, status, observed, detail,"
                "  checked_at)"
                " VALUES (%(id)s, %(assertion_id)s, %(data_source_id)s,"
                "  %(status)s, %(observed)s, %(detail)s, %(checked_at)s)",
                {
                    "id": result.id,
                    "assertion_id": result.assertion_id,
                    "data_source_id": result.data_source_id,
                    "status": result.status.value,
                    "observed": result.observed,
                    "detail": result.detail,
                    "checked_at": result.checked_at,
                },
            )
            await cursor.execute(
                "DELETE FROM knowledge.quality_check_results"
                " WHERE assertion_id = %(assertion_id)s AND id NOT IN ("
                "   SELECT id FROM knowledge.quality_check_results"
                "   WHERE assertion_id = %(assertion_id)s"
                "   ORDER BY checked_at DESC LIMIT %(keep)s)",
                {"assertion_id": result.assertion_id, "keep": RETAINED_RESULTS},
            )
        return result

    async def latest(self, data_source_id: UUID) -> dict[UUID, QualityCheckResult]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT DISTINCT ON (assertion_id)"
                "  id, assertion_id, data_source_id, status, observed, detail,"
                "  checked_at"
                " FROM knowledge.quality_check_results"
                " WHERE data_source_id = %(data_source_id)s"
                " ORDER BY assertion_id, checked_at DESC",
                {"data_source_id": data_source_id},
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        return {row["assertion_id"]: _to_result(row) for row in rows}


def _to_assertion(row: dict[str, Any]) -> QualityAssertion:
    return QualityAssertion(
        id=row["id"],
        data_source_id=row["data_source_id"],
        name=row["name"],
        assertion_type=AssertionType(row["assertion_type"]),
        schema_name=row["schema_name"],
        table_name=row["table_name"],
        column_name=row["column_name"],
        configuration=row["configuration"] or {},
        enabled=row["enabled"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _to_result(row: dict[str, Any]) -> QualityCheckResult:
    return QualityCheckResult(
        id=row["id"],
        assertion_id=row["assertion_id"],
        data_source_id=row["data_source_id"],
        status=QualityStatus(row["status"]),
        observed=row["observed"],
        detail=row["detail"],
        checked_at=row["checked_at"],
    )
