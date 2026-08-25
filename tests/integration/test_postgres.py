import asyncpg
import pytest

from app.agent.graph import build_graph
from app.config import Settings
from app.data.gateway import DatabaseQueryTimeoutError, DatabaseUnavailableError
from app.data.postgres import PostgresDatabaseGateway
from app.llm.fake import FakeLLMGateway
from app.security.sql_validation import SQLValidator

QUESTION = (
    "Show each department, its number of employees, total salary, average salary, "
    "and highest paid employee, ordered by total payroll."
)


async def _postgres_available(settings: Settings) -> bool:
    gateway = PostgresDatabaseGateway(settings)
    try:
        return await gateway.health_check()
    except (DatabaseUnavailableError, OSError, asyncpg.PostgresError):
        return False
    finally:
        await gateway.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_real_postgres_vertical_slice_and_readonly_role() -> None:
    settings = Settings(DATABASE_PROVIDER="postgres")
    if not await _postgres_available(settings):
        pytest.skip("Dockerized PostgreSQL is not running")

    gateway = PostgresDatabaseGateway(settings)
    graph = build_graph(
        db_gateway=gateway,
        llm_gateway=FakeLLMGateway(),
        sql_validator=SQLValidator(max_rows=settings.query_row_limit),
    )
    try:
        metadata = await gateway.search_schema("active employee status")
        employees = next(table for table in metadata if table.table_name == "employees")
        employee_status = next(
            column for column in employees.column_metadata if column.name == "status"
        )
        assert employees.primary_key == ("id",)
        assert any(
            foreign_key.referenced_table == "analytics.departments"
            for foreign_key in employees.foreign_keys
        )
        assert set(employee_status.observed_values) == {"active", "leave", "terminated"}
        assert employee_status.observed_values_source == "database"

        parameterized = await gateway.execute_readonly(
            "SELECT name FROM analytics.departments WHERE name = $1",
            ("Engineering",),
        )
        assert parameterized.rows == [{"name": "Engineering"}]
        assert parameterized.columns[0].name == "name"
        assert parameterized.metadata.live is True
        assert parameterized.metadata.duration_ms >= 0
        assert parameterized.metadata.result_bytes > 0

        result = await graph.ainvoke(
            {
                "request_id": "postgres-test",
                "trace_id": "postgres-test",
                "thread_id": None,
                "question": QUESTION,
            }
        )

        assert [row["department"] for row in result["query_result"]] == [
            "Engineering",
            "Sales",
            "Finance",
            "People Operations",
        ]
        assert result["query_result"][0]["total_salary"] == 610000

    finally:
        await gateway.close()

    connection = await asyncpg.connect(str(settings.database_url))
    try:
        assert await connection.fetchval("SHOW default_transaction_read_only") == "on"
        with pytest.raises(asyncpg.PostgresError):
            await connection.execute("UPDATE analytics.employees SET salary = salary WHERE false")
    finally:
        await connection.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_real_postgres_timeout_cancels_query() -> None:
    settings = Settings(
        DATABASE_PROVIDER="postgres",
        DB_QUERY_TIMEOUT_SECONDS=0.2,
    )
    if not await _postgres_available(settings):
        pytest.skip("Configured PostgreSQL is not running")

    gateway = PostgresDatabaseGateway(settings)
    try:
        with pytest.raises(DatabaseQueryTimeoutError):
            await gateway.execute_readonly(
                """
                WITH RECURSIVE numbers(value) AS (
                    SELECT 1
                    UNION ALL
                    SELECT value + 1 FROM numbers WHERE value < 10000000
                )
                SELECT SUM(value) AS total FROM numbers
                """
            )
        assert await gateway.health_check() is True
    finally:
        await gateway.close()
