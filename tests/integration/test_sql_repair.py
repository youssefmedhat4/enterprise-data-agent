from __future__ import annotations

from typing import Any, cast

import pytest

from app.agent.graph import build_graph
from app.data.fake import FakeDatabaseGateway
from app.llm.gateway import LLMGateway, ResponseModelT, SQLGeneration, SQLRepair
from app.security.sql_validation import SQLRepairFailedError, SQLValidationError, SQLValidator


class RepairingLLM(LLMGateway):
    def __init__(self, *, original_sql: str, repaired_sql: str) -> None:
        self.original_sql = original_sql
        self.repaired_sql = repaired_sql
        self.generation_calls = 0
        self.repair_calls = 0
        self.repair_prompts: list[str] = []

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        assert model_alias == "sql-reasoner"
        if response_model is SQLGeneration:
            self.generation_calls += 1
            return response_model.model_validate(
                {"action": "execute", "sql": self.original_sql}
            )
        if response_model is SQLRepair:
            self.repair_calls += 1
            self.repair_prompts.append(user)
            assert "Repair one PostgreSQL SELECT query" in system
            return response_model.model_validate({"repaired_sql": self.repaired_sql})
        raise AssertionError(f"Unexpected response model: {response_model.__name__}")


async def _invoke(llm: LLMGateway, database: FakeDatabaseGateway) -> dict[str, Any]:
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        generate_answer=False,
    )
    return cast(
        dict[str, Any],
        await graph.ainvoke(
            {
                "request_id": "repair-request",
                "trace_id": "repair-trace",
                "thread_id": None,
                "question": "Show employee names and their department names",
            }
        ),
    )


@pytest.mark.asyncio
async def test_repairable_schema_error_gets_one_successful_repair() -> None:
    original = (
        "SELECT e.full_name, d.department_title "
        "FROM analytics.employees e "
        "JOIN analytics.departments d ON e.department_id = d.id"
    )
    repaired = (
        "SELECT e.full_name, d.name AS department "
        "FROM analytics.employees e "
        "JOIN analytics.departments d ON e.department_id = d.id"
    )
    llm = RepairingLLM(original_sql=original, repaired_sql=repaired)
    database = FakeDatabaseGateway()

    result = await _invoke(llm, database)

    assert llm.generation_calls == 1
    assert llm.repair_calls == 1
    assert len(database.executed_sql) == 1
    assert result["sql_validation_attempts"] == 2
    assert result["sql_repair_attempted"] is True
    assert result["sql_repair_succeeded"] is True
    assert result["initial_validation_error_code"] == "unknown_column"
    assert result["final_validation_status"] == "valid"
    assert result["original_candidate_sql"] == original
    assert result["repaired_candidate_sql"] == repaired
    provenance = result["internal_provenance"]
    assert provenance.sql_validation_attempts == 2
    assert provenance.original_candidate_sql == original
    assert provenance.repaired_candidate_sql == repaired
    assert provenance.repair_latency_ms >= 0
    assert "analytics.departments" in llm.repair_prompts[0]
    assert "unknown_column" in llm.repair_prompts[0]


@pytest.mark.asyncio
async def test_failed_repair_is_not_retried_and_does_not_execute() -> None:
    llm = RepairingLLM(
        original_sql="SELECT e.department_title FROM analytics.employees e",
        repaired_sql="SELECT e.department_label FROM analytics.employees e",
    )
    database = FakeDatabaseGateway()

    with pytest.raises(SQLRepairFailedError):
        await _invoke(llm, database)

    assert llm.generation_calls == 1
    assert llm.repair_calls == 1
    assert database.executed_sql == []


@pytest.mark.parametrize(
    "unsafe_sql",
    [
        "DROP TABLE analytics.employees",
        "SELECT p.usename FROM pg_catalog.pg_user p",
        "SELECT e.id FROM analytics.employees e; DELETE FROM analytics.employees",
        "SELECT made_up_function(e.salary) FROM analytics.employees e",
    ],
)
@pytest.mark.asyncio
async def test_security_failures_never_trigger_repair(unsafe_sql: str) -> None:
    llm = RepairingLLM(
        original_sql=unsafe_sql,
        repaired_sql="SELECT e.id FROM analytics.employees e",
    )
    database = FakeDatabaseGateway()

    with pytest.raises(SQLValidationError):
        await _invoke(llm, database)

    assert llm.generation_calls == 1
    assert llm.repair_calls == 0
    assert database.executed_sql == []
