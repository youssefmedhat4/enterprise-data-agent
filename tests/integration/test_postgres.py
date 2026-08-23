import asyncpg
import pytest

from app.agent.graph import build_graph
from app.config import Settings
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
    except (OSError, asyncpg.PostgresError):
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
            await connection.execute(
                "UPDATE analytics.employees SET salary = salary WHERE false"
            )
    finally:
        await connection.close()
