"""Approved query examples and business instructions in the real SQL path.

The point of these is what does *not* happen: a stored example is never
executed, never escapes its datasource, and never reveals a table the caller
cannot read.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.agent.graph import build_graph
from app.data.fake import FakeDatabaseGateway
from app.knowledge.contracts import ApprovalStatus
from app.knowledge.guidance import (
    ApprovedQueryExample,
    BusinessInstruction,
    GuidanceError,
    InMemoryGuidanceStore,
)
from app.knowledge.seed import DEFAULT_DATA_SOURCE_ID
from app.llm.gateway import LLMGateway, ResponseModelT, SQLGeneration
from app.security.sql_validation import SQLValidator

SOURCE_B = uuid4()

APPROVED_SQL = (
    "SELECT d.name AS department, SUM(e.salary) AS payroll "
    "FROM analytics.employees e "
    "JOIN analytics.departments d ON d.id = e.department_id "
    "GROUP BY d.name"
)


class CapturingSQLLLM(LLMGateway):
    """Records the SQL prompt and returns its own statement."""

    def __init__(self, sql: str | None = None) -> None:
        self.prompts: list[str] = []
        self._sql = sql or (
            "SELECT d.name AS department FROM analytics.departments d LIMIT 100"
        )

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system
        if response_model is SQLGeneration:
            self.prompts.append(user)
            return response_model.model_validate(
                {"action": "execute", "sql": self._sql}
            )
        raise AssertionError(f"unexpected model call: {response_model.__name__}")


async def graph_with(guidance: InMemoryGuidanceStore, llm: LLMGateway) -> Any:
    return build_graph(
        db_gateway=FakeDatabaseGateway(),
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        guidance_store=guidance,
        generate_answer=False,
    )


async def run(graph: Any, question: str) -> dict[str, Any]:
    result: dict[str, Any] = await graph.ainvoke(
        {
            "request_id": "guidance-request",
            "trace_id": "guidance-trace",
            "thread_id": "guidance-thread",
            "question": question,
        }
    )
    return result


async def approved_store() -> InMemoryGuidanceStore:
    store = InMemoryGuidanceStore()
    await store.approve_example(
        ApprovedQueryExample(
            data_source_id=DEFAULT_DATA_SOURCE_ID,
            question="What is total payroll by department?",
            query_pattern=APPROVED_SQL,
        ),
        was_successful=True,
        was_validated=True,
    )
    return store


# --- Approval gates -------------------------------------------------------


@pytest.mark.anyio
async def test_a_failed_request_cannot_become_an_approved_example() -> None:
    store = InMemoryGuidanceStore()
    with pytest.raises(GuidanceError, match="successful"):
        await store.approve_example(
            ApprovedQueryExample(
                data_source_id=DEFAULT_DATA_SOURCE_ID,
                question="q",
                query_pattern=APPROVED_SQL,
            ),
            was_successful=False,
            was_validated=True,
        )


@pytest.mark.anyio
async def test_a_mutating_statement_cannot_be_approved() -> None:
    store = InMemoryGuidanceStore()
    with pytest.raises(GuidanceError, match="read-only"):
        await store.approve_example(
            ApprovedQueryExample(
                data_source_id=DEFAULT_DATA_SOURCE_ID,
                question="remove staff",
                query_pattern="DELETE FROM analytics.employees",
            ),
            was_successful=True,
            was_validated=True,
        )


# --- Retrieval into the real prompt ---------------------------------------


@pytest.mark.anyio
async def test_an_approved_example_reaches_sql_generation_as_context() -> None:
    llm = CapturingSQLLLM()
    graph = await graph_with(await approved_store(), llm)

    await run(graph, "show me payroll by department")

    assert llm.prompts, "SQL generation was never reached"
    prompt = llm.prompts[0]
    assert "previously approved examples" in prompt.casefold()
    assert APPROVED_SQL in prompt


@pytest.mark.anyio
async def test_the_stored_sql_is_never_what_gets_executed() -> None:
    """The model writes its own SQL; the example is only context."""
    own_sql = "SELECT d.name AS department FROM analytics.departments d LIMIT 100"
    llm = CapturingSQLLLM(sql=own_sql)
    database = FakeDatabaseGateway()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        guidance_store=await approved_store(),
        generate_answer=False,
    )

    await run(graph, "show me payroll by department")

    assert database.executed_sql, "nothing was executed"
    executed = " ".join(database.executed_sql)
    assert APPROVED_SQL not in executed, "the stored example was executed directly"


@pytest.mark.anyio
async def test_an_example_from_another_datasource_is_not_offered() -> None:
    store = InMemoryGuidanceStore()
    await store.approve_example(
        ApprovedQueryExample(
            data_source_id=SOURCE_B,
            question="What is total payroll by department?",
            query_pattern=APPROVED_SQL,
        ),
        was_successful=True,
        was_validated=True,
    )
    llm = CapturingSQLLLM()
    graph = await graph_with(store, llm)

    await run(graph, "show me payroll by department")

    assert APPROVED_SQL not in llm.prompts[0]


@pytest.mark.anyio
async def test_an_example_touching_an_unauthorized_table_is_withheld() -> None:
    """Approved knowledge must not reveal that a table exists."""
    store = InMemoryGuidanceStore()
    await store.approve_example(
        ApprovedQueryExample(
            data_source_id=DEFAULT_DATA_SOURCE_ID,
            question="What is total payroll by department?",
            query_pattern=(
                "SELECT * FROM analytics.classified_compensation_plan LIMIT 10"
            ),
        ),
        was_successful=True,
        was_validated=True,
    )

    offered = await store.relevant_examples(
        DEFAULT_DATA_SOURCE_ID,
        "show me payroll by department",
        authorized_tables=frozenset({"analytics.employees"}),
    )

    assert offered == []


@pytest.mark.anyio
async def test_a_stale_example_is_not_offered() -> None:
    store = InMemoryGuidanceStore()
    await store.approve_example(
        ApprovedQueryExample(
            data_source_id=DEFAULT_DATA_SOURCE_ID,
            question="What is total payroll by department?",
            query_pattern=APPROVED_SQL,
            schema_fingerprint="fp-old",
        ),
        was_successful=True,
        was_validated=True,
    )

    marked = await store.mark_stale_for_schema(
        DEFAULT_DATA_SOURCE_ID, new_schema_fingerprint="fp-new"
    )

    assert marked == 1
    assert (
        await store.relevant_examples(
            DEFAULT_DATA_SOURCE_ID, "show me payroll by department"
        )
        == []
    )
    stored = await store.examples(DEFAULT_DATA_SOURCE_ID)
    assert stored[0].status is ApprovalStatus.STALE, "approved work was deleted"


# --- Business instructions ------------------------------------------------


async def store_with_instruction() -> InMemoryGuidanceStore:
    store = InMemoryGuidanceStore()
    await store.approve_instruction(
        BusinessInstruction(
            data_source_id=DEFAULT_DATA_SOURCE_ID,
            title="Payroll roster scope",
            instruction=(
                "Annual base payroll includes all roster employees, while active "
                "headcount counts only active employees."
            ),
            semantic_concepts=("payroll", "headcount", "compensation"),
            metric_keys=("annual_base_payroll",),
        )
    )
    return store


@pytest.mark.anyio
async def test_a_relevant_instruction_reaches_the_prompt() -> None:
    llm = CapturingSQLLLM()
    graph = await graph_with(await store_with_instruction(), llm)

    await run(graph, "what is our annual payroll compensation commitment?")

    assert "Payroll roster scope" in llm.prompts[0]
    assert "roster employees" in llm.prompts[0]


@pytest.mark.anyio
async def test_an_unrelated_question_does_not_get_the_instruction() -> None:
    llm = CapturingSQLLLM()
    graph = await graph_with(await store_with_instruction(), llm)

    await run(graph, "list the customers we invoiced in Berlin")

    assert "Payroll roster scope" not in llm.prompts[0]


@pytest.mark.anyio
async def test_instructions_do_not_cross_datasources() -> None:
    store = await store_with_instruction()

    assert (
        await store.relevant_instructions(SOURCE_B, "annual payroll compensation")
        == []
    )
