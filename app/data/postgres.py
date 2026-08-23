from typing import Any

import asyncpg

from app.config import Settings
from app.data.gateway import DatabaseGateway, TableMetadata


class PostgresDatabaseGateway(DatabaseGateway):
    def __init__(self, settings: Settings) -> None:
        self._database_url = str(settings.database_url)
        self._timeout = settings.query_timeout_seconds
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self._database_url,
                min_size=1,
                max_size=5,
                command_timeout=self._timeout,
            )
        return self._pool

    async def health_check(self) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            return bool(await connection.fetchval("SELECT true"))

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT
                table_schema,
                table_name,
                array_agg(column_name ORDER BY ordinal_position) AS columns
            FROM information_schema.columns
            WHERE table_schema = 'analytics'
              AND table_name IN (
                  'customers',
                  'departments',
                  'employee_project_assignments',
                  'employees',
                  'invoice_lines',
                  'invoices',
                  'payroll',
                  'project_costs',
                  'projects'
              )
            GROUP BY table_schema, table_name
            ORDER BY table_name
            """
        )
        descriptions = {
            "departments": "Enterprise departments with English and Arabic names.",
            "employees": "Synthetic employees, department assignments, status, and salary.",
            "payroll": "Synthetic monthly payroll facts for employees.",
            "customers": "Synthetic enterprise customers and industries.",
            "projects": "Synthetic customer projects, dates, status, and budget.",
            "employee_project_assignments": "Employee project allocation facts.",
            "invoices": "Synthetic customer and project invoice headers.",
            "invoice_lines": "Synthetic invoice lines used to calculate totals.",
            "project_costs": "Synthetic dated project costs by category.",
        }
        return [
            TableMetadata(
                schema_name=row["table_schema"],
                table_name=row["table_name"],
                columns=list(row["columns"]),
                description=descriptions.get(row["table_name"], ""),
            )
            for row in rows
        ]

    async def execute_readonly(self, sql: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction(readonly=True):
            rows = await connection.fetch(sql)
        return [dict(row) for row in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
