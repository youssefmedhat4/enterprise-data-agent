from typing import Any

import pytest

from app.agent.graph import build_graph
from app.data.fake import FakeDatabaseGateway
from app.llm.fake import FakeLLMGateway
from app.security.sql_validation import SQLValidator

QUESTION = (
    "Show each department, its number of employees, total salary, average salary, "
    "and highest paid employee, ordered by total payroll."
)


@pytest.mark.asyncio
async def test_vertical_slice_returns_grounded_department_payroll() -> None:
    database = FakeDatabaseGateway()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=FakeLLMGateway(),
        sql_validator=SQLValidator(),
    )

    result: dict[str, Any] = await graph.ainvoke(
        {
            "request_id": "request-test",
            "trace_id": "trace-test",
            "thread_id": None,
            "question": QUESTION,
        }
    )

    assert len(database.executed_sql) == 1
    assert database.executed_sql[0] == result["validated_sql"]
    assert result["query_result"][0] == {
        "department": "Engineering",
        "employee_count": 4,
        "total_salary": "610000.00",
        "average_salary": "152500.00",
        "highest_paid_employee": "Maya Haddad",
    }
    assert "Engineering: 4 employees" in result["final_answer"]
    assert "People Operations: 1 employees" in result["final_answer"]
    assert result["chart_spec"].y == "total_salary"
    provenance = result["internal_provenance"]
    assert provenance.request_id == "request-test"
    assert provenance.result.row_count == 4
    assert provenance.result.columns == [
        "department",
        "employee_count",
        "total_salary",
        "average_salary",
        "highest_paid_employee",
    ]
    assert provenance.tables == ["analytics.departments", "analytics.employees"]
    assert len(result["claims"]) == 4
