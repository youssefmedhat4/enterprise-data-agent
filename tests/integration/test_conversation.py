from collections.abc import Sequence
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph
from app.data.fake import FakeDatabaseGateway
from app.data.gateway import DatabaseQueryResult, query_result_from_rows
from app.llm.fake import FakeLLMGateway
from app.llm.gateway import LLMGateway, ResponseModelT, SQLGeneration
from app.security.sql_validation import SQLValidator


class ConversationDatabaseGateway(FakeDatabaseGateway):
    async def execute_readonly(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> DatabaseQueryResult:
        del parameters
        self.executed_sql.append(sql)
        if "2025-01-01" in sql:
            return query_result_from_rows([{"department": "Sales", "total_payroll": "900000.00"}])
        return query_result_from_rows(
            [{"department": "Engineering", "total_payroll": "1200000.00"}]
        )


class RecordingLLMGateway(LLMGateway):
    def __init__(self) -> None:
        self.delegate = FakeLLMGateway()
        self.sql_prompts: list[str] = []

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        if response_model is SQLGeneration:
            self.sql_prompts.append(user)
        return await self.delegate.generate_structured(
            model_alias=model_alias,
            system=system,
            user=user,
            response_model=response_model,
        )


@pytest.mark.asyncio
async def test_follow_up_uses_structured_analytical_context() -> None:
    database = ConversationDatabaseGateway()
    llm = RecordingLLMGateway()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "payroll-thread"}}

    first = await graph.ainvoke(
        {
            "request_id": "request-1",
            "trace_id": "trace-1",
            "thread_id": "payroll-thread",
            "question": "Which department has the highest payroll?",
        },
        config=config,
    )
    second = await graph.ainvoke(
        {
            "request_id": "request-2",
            "trace_id": "trace-2",
            "thread_id": "payroll-thread",
            "question": "What about last year?",
        },
        config=config,
    )

    assert first["final_answer"].startswith("Engineering")
    assert second["final_answer"].startswith("Sales")
    assert "2025-01-01" in second["validated_sql"]
    assert second["analytical_context"].metric == "total_payroll"
    assert second["analytical_context"].time_range.label == "last year"
    assert len(second["conversation_turns"]) == 2
    assert '"metric":"total_payroll"' in llm.sql_prompts[1]


@pytest.mark.asyncio
async def test_conversation_threads_are_isolated() -> None:
    database = ConversationDatabaseGateway()
    llm = RecordingLLMGateway()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        checkpointer=InMemorySaver(),
    )

    for thread_id in ("thread-a", "thread-b"):
        await graph.ainvoke(
            {
                "request_id": f"request-{thread_id}",
                "trace_id": f"trace-{thread_id}",
                "thread_id": thread_id,
                "question": "Which department has the highest payroll?",
            },
            config={"configurable": {"thread_id": thread_id}},
        )

    assert "Previous structured analytical context:\nnone" in llm.sql_prompts[0]
    assert "Previous structured analytical context:\nnone" in llm.sql_prompts[1]
