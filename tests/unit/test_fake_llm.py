import pytest

from app.llm.fake import FakeLLMGateway
from app.llm.gateway import SQLGeneration


@pytest.mark.asyncio
async def test_fake_llm_generates_deterministic_payroll_sql() -> None:
    gateway = FakeLLMGateway()

    first = await gateway.generate_structured(
        model_alias="sql-reasoner",
        system="system",
        user=(
            "Show each department, its number of employees, total salary, average salary, "
            "and highest paid employee, ordered by total payroll."
        ),
        response_model=SQLGeneration,
    )
    second = await gateway.generate_structured(
        model_alias="sql-reasoner",
        system="system",
        user=(
            "Show each department, its number of employees, total salary, average salary, "
            "and highest paid employee, ordered by total payroll."
        ),
        response_model=SQLGeneration,
    )

    assert first == second
    assert isinstance(first, SQLGeneration)
    assert first.sql is not None
    assert "analytics.employees" in first.sql
    assert "ORDER BY total_salary DESC" in first.sql
