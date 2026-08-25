import pytest

from app.evals.duckdb_gateway import DuckDBEvaluationGateway


@pytest.mark.asyncio
async def test_duckdb_gateway_executes_real_join_and_aggregation() -> None:
    gateway = DuckDBEvaluationGateway()
    try:
        result = await gateway.execute_readonly(
            """
            SELECT d.name AS department, COUNT(*) AS employee_count
            FROM analytics.departments AS d
            JOIN analytics.employees AS e ON e.department_id = d.id
            WHERE e.status = 'active'
            GROUP BY d.name
            ORDER BY employee_count DESC, department
            """
        )
    finally:
        await gateway.close()

    assert result.rows[0] == {"department": "Engineering", "employee_count": 4}
    assert len(result.rows) == 4
    assert result.metadata.live is False
