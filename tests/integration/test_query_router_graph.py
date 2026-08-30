from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph
from app.data.fake import FakeDatabaseGateway
from app.llm.fake import FakeLLMGateway
from app.llm.gateway import LLMGateway, ResponseModelT, SQLGeneration
from app.metrics.fake import FakeMetricGateway
from app.metrics.gateway import MetricProviderUnavailableError, MetricQuery, MetricResult
from app.routing.contracts import MetricPlanningError
from app.security.sql_validation import SQLValidator


class RecordingLLM(LLMGateway):
    def __init__(self) -> None:
        self.delegate = FakeLLMGateway()
        self.response_models: list[str] = []

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        self.response_models.append(response_model.__name__)
        return await self.delegate.generate_structured(
            model_alias=model_alias,
            system=system,
            user=user,
            response_model=response_model,
        )


class AdhocSQLLLM(LLMGateway):
    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system, user
        self.calls += 1
        if response_model is not SQLGeneration:
            raise AssertionError("SQL-only graph must not request an answer model.")
        return response_model.model_validate(
            {
                "action": "execute",
                "sql": (
                    "SELECT e.full_name, p.name AS project "
                    "FROM analytics.employees e "
                    "JOIN analytics.employee_project_assignments a ON a.employee_id = e.id "
                    "JOIN analytics.projects p ON p.id = a.project_id "
                    "WHERE p.status = 'active' LIMIT 100"
                ),
            }
        )


class UnavailableMetricGateway(FakeMetricGateway):
    async def query_metric(self, query: MetricQuery) -> MetricResult:
        del query
        raise MetricProviderUnavailableError("Cube is unavailable")


@pytest.mark.asyncio
async def test_governed_metric_path_bypasses_sql_reasoner_and_converges() -> None:
    database = FakeDatabaseGateway()
    metrics = FakeMetricGateway()
    llm = RecordingLLM()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        metric_gateway=metrics,
        enable_query_router=True,
    )

    result: dict[str, Any] = await graph.ainvoke(
        {
            "request_id": "metric-request",
            "trace_id": "metric-trace",
            "thread_id": "metric-thread",
            "question": "Total annual payroll by department",
        }
    )

    assert len(metrics.queries) == 1
    assert database.executed_sql == []
    assert llm.response_models == ["AnswerGeneration"]
    assert result["analytical_result"].source_type == "governed_metric"
    assert result["internal_provenance"].route == "governed_metric"
    assert result["internal_provenance"].metric_provider == "fake"
    assert result["internal_provenance"].metric_id == "annual_base_payroll"
    assert result["internal_provenance"].execution_source == "fake"
    assert len(result["claims"]) == 4


@pytest.mark.asyncio
async def test_adhoc_path_bypasses_metric_gateway() -> None:
    database = FakeDatabaseGateway()
    metrics = FakeMetricGateway()
    llm = AdhocSQLLLM()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        metric_gateway=metrics,
        enable_query_router=True,
        generate_answer=False,
    )

    result = await graph.ainvoke(
        {
            "request_id": "adhoc-request",
            "trace_id": "adhoc-trace",
            "thread_id": "adhoc-thread",
            "question": "Show employees assigned to each active project",
        }
    )

    assert metrics.queries == []
    assert llm.calls == 1
    assert len(database.executed_sql) == 1
    assert result["analytical_result"].source_type == "adhoc_sql"
    assert result["internal_provenance"].route == "adhoc_analytics"


@pytest.mark.asyncio
async def test_block_path_invokes_neither_provider() -> None:
    database = FakeDatabaseGateway()
    metrics = FakeMetricGateway()
    llm = RecordingLLM()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        metric_gateway=metrics,
        enable_query_router=True,
    )

    result = await graph.ainvoke(
        {
            "request_id": "blocked-request",
            "trace_id": "blocked-trace",
            "thread_id": "blocked-thread",
            "question": "Delete inactive employees",
        }
    )

    assert result["execution_metadata"].status == "blocked"
    assert metrics.queries == []
    assert database.executed_sql == []
    assert llm.response_models == []


@pytest.mark.asyncio
async def test_composite_metric_question_uses_adhoc_sql() -> None:
    database = FakeDatabaseGateway()
    metrics = FakeMetricGateway()
    llm = AdhocSQLLLM()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        metric_gateway=metrics,
        enable_query_router=True,
        generate_answer=False,
    )

    result = await graph.ainvoke(
        {
            "request_id": "composite-request",
            "trace_id": "composite-trace",
            "thread_id": "composite-thread",
            "question": "Show project cost and project margin by project",
        }
    )

    assert metrics.queries == []
    assert llm.calls == 1
    assert len(database.executed_sql) == 1
    assert result["analytical_result"].source_type == "adhoc_sql"
    assert result["internal_provenance"].route == "adhoc_analytics"


@pytest.mark.asyncio
async def test_clarification_path_executes_nothing() -> None:
    database = FakeDatabaseGateway()
    metrics = FakeMetricGateway()
    llm = RecordingLLM()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        metric_gateway=metrics,
        enable_query_router=True,
    )

    result = await graph.ainvoke(
        {
            "request_id": "clarify-request",
            "trace_id": "clarify-trace",
            "thread_id": "clarify-thread",
            "question": "Only Engineering",
        }
    )

    assert result["execution_metadata"].status == "clarification_required"
    assert result["generated_sql"] is None
    assert metrics.queries == []
    assert database.executed_sql == []
    assert llm.response_models == []


@pytest.mark.asyncio
async def test_invalid_metric_plan_does_not_fall_back_to_sql() -> None:
    database = FakeDatabaseGateway()
    metrics = FakeMetricGateway()
    llm = RecordingLLM()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        metric_gateway=metrics,
        enable_query_router=True,
    )

    with pytest.raises(MetricPlanningError):
        await graph.ainvoke(
            {
                "request_id": "invalid-plan",
                "trace_id": "invalid-plan",
                "thread_id": "invalid-plan",
                "question": "Invoice amount for active customers",
            }
        )

    assert metrics.queries == []
    assert database.executed_sql == []
    assert llm.response_models == []


@pytest.mark.asyncio
async def test_unavailable_metric_provider_does_not_fall_back_to_sql() -> None:
    database = FakeDatabaseGateway()
    llm = RecordingLLM()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        metric_gateway=UnavailableMetricGateway(),
        enable_query_router=True,
    )

    with pytest.raises(MetricProviderUnavailableError):
        await graph.ainvoke(
            {
                "request_id": "metric-down",
                "trace_id": "metric-down",
                "thread_id": "metric-down",
                "question": "Total annual payroll by department",
            }
        )

    assert database.executed_sql == []
    assert llm.response_models == []


@pytest.mark.asyncio
async def test_metric_followup_stays_governed_and_adds_filter() -> None:
    metrics = FakeMetricGateway()
    graph = build_graph(
        db_gateway=FakeDatabaseGateway(),
        llm_gateway=RecordingLLM(),
        sql_validator=SQLValidator(),
        metric_gateway=metrics,
        enable_query_router=True,
        generate_answer=False,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "metric-followup"}}

    await graph.ainvoke(
        {
            "request_id": "metric-1",
            "trace_id": "metric-1",
            "thread_id": "metric-followup",
            "question": "Total annual payroll by department",
        },
        config=config,
    )
    second = await graph.ainvoke(
        {
            "request_id": "metric-2",
            "trace_id": "metric-2",
            "thread_id": "metric-followup",
            "question": "Only Engineering",
        },
        config=config,
    )

    assert len(metrics.queries) == 2
    assert metrics.queries[1].metric == "annual_base_payroll"
    assert metrics.queries[1].dimensions == ("department",)
    assert metrics.queries[1].filters[0].values == ("Engineering",)
    assert second["query_result"] == [
        {
            "department": "Engineering",
            "annual_base_payroll": Decimal("710000.00"),
        }
    ]


@pytest.mark.asyncio
async def test_dimension_followup_keeps_governed_metric() -> None:
    metrics = FakeMetricGateway()
    graph = build_graph(
        db_gateway=FakeDatabaseGateway(),
        llm_gateway=RecordingLLM(),
        sql_validator=SQLValidator(),
        metric_gateway=metrics,
        enable_query_router=True,
        generate_answer=False,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "dimension-followup"}}

    await graph.ainvoke(
        {
            "request_id": "margin-1",
            "trace_id": "margin-1",
            "thread_id": "dimension-followup",
            "question": "Show me margin",
        },
        config=config,
    )
    second = await graph.ainvoke(
        {
            "request_id": "margin-2",
            "trace_id": "margin-2",
            "thread_id": "dimension-followup",
            "question": "by project",
        },
        config=config,
    )

    assert [query.metric for query in metrics.queries] == [
        "project_margin",
        "project_margin",
    ]
    assert metrics.queries[1].dimensions == ("project",)
    assert second["internal_provenance"].route == "governed_metric"


@pytest.mark.asyncio
async def test_followup_can_switch_governed_metric() -> None:
    metrics = FakeMetricGateway()
    graph = build_graph(
        db_gateway=FakeDatabaseGateway(),
        llm_gateway=RecordingLLM(),
        sql_validator=SQLValidator(),
        metric_gateway=metrics,
        enable_query_router=True,
        generate_answer=False,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "switch-metric"}}

    await graph.ainvoke(
        {
            "request_id": "switch-1",
            "trace_id": "switch-1",
            "thread_id": "switch-metric",
            "question": "Project margin by project",
        },
        config=config,
    )
    await graph.ainvoke(
        {
            "request_id": "switch-2",
            "trace_id": "switch-2",
            "thread_id": "switch-metric",
            "question": "what about project cost by project",
        },
        config=config,
    )

    assert [query.metric for query in metrics.queries] == [
        "project_margin",
        "project_cost",
    ]
    assert metrics.queries[1].dimensions == ("project",)


@pytest.mark.asyncio
async def test_adhoc_followup_stays_adhoc() -> None:
    database = FakeDatabaseGateway()
    metrics = FakeMetricGateway()
    llm = AdhocSQLLLM()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        metric_gateway=metrics,
        enable_query_router=True,
        generate_answer=False,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "adhoc-followup"}}

    for index, question in enumerate(
        ("Show employees assigned to each active project", "Only the top 5"),
        start=1,
    ):
        result = await graph.ainvoke(
            {
                "request_id": f"adhoc-{index}",
                "trace_id": f"adhoc-{index}",
                "thread_id": "adhoc-followup",
                "question": question,
            },
            config=config,
        )

    assert result["internal_provenance"].route == "adhoc_analytics"
    assert metrics.queries == []
    assert llm.calls == 2
    assert len(database.executed_sql) == 2
