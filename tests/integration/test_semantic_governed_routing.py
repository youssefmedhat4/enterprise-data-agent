"""Governed routing decided by meaning, through the real graph.

These drive `build_graph` end to end rather than exercising the retriever or
the planner in isolation. That distinction is the point of this file: the
knowledge layer was previously well tested but never reached by a request, so
only a test that goes through the compiled graph proves it is wired in.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.agent.graph import build_graph
from app.data.fake import FakeDatabaseGateway
from app.embeddings.fake import HashingEmbeddingGateway
from app.knowledge.metrics import InMemoryMetricRegistry, MetricStatus
from app.knowledge.planner import MetricIntentPlanner, MetricSelection
from app.knowledge.retrieval import MetricRetriever
from app.knowledge.seed import (
    DEFAULT_DATA_SOURCE_ID,
    registered_metrics_for_default_datasource,
)
from app.llm.fake import FakeLLMGateway
from app.llm.gateway import LLMGateway, ResponseModelT, SQLGeneration
from app.metrics.fake import FakeMetricGateway
from app.routing.router import DeterministicQueryRouter
from app.security.sql_validation import SQLValidator

#: Shares no configured alias with any metric. Literal alias matching cannot
#: route this; only retrieval by meaning can.
PARAPHRASE = (
    "How much money does the organization commit to employee base "
    "compensation each year?"
)


class SelectingLLM(LLMGateway):
    """Returns a fixed metric selection, delegating everything else."""

    def __init__(self, selection: MetricSelection) -> None:
        self._selection = selection
        self._delegate = FakeLLMGateway()
        self.intent_calls = 0
        self.adhoc_sql_calls = 0
        self.last_intent_prompt = ""

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        if response_model is MetricSelection:
            self.intent_calls += 1
            self.last_intent_prompt = user
            return self._selection  # type: ignore[return-value]
        if response_model is SQLGeneration:
            # The ad-hoc fallback is a real route, so it has to produce
            # something executable. These tests assert on which route was
            # taken, not on the SQL, so one valid statement suffices.
            self.adhoc_sql_calls += 1
            return response_model.model_validate(
                {
                    "action": "execute",
                    "sql": (
                        "SELECT d.name AS department FROM analytics.departments d"
                        " LIMIT 100"
                    ),
                }
            )
        return await self._delegate.generate_structured(
            model_alias=model_alias,
            system=system,
            user=user,
            response_model=response_model,
        )


async def semantic_graph(
    llm: LLMGateway,
    *,
    metrics_gateway: FakeMetricGateway | None = None,
    registry_metrics: list[Any] | None = None,
    generate_answer: bool = False,
) -> tuple[Any, FakeMetricGateway, FakeDatabaseGateway]:
    seeded = (
        registry_metrics
        if registry_metrics is not None
        else registered_metrics_for_default_datasource(DEFAULT_DATA_SOURCE_ID)
    )
    registry = InMemoryMetricRegistry(seeded)
    retriever = MetricRetriever(HashingEmbeddingGateway())
    await retriever.index(
        DEFAULT_DATA_SOURCE_ID, await registry.certified(DEFAULT_DATA_SOURCE_ID)
    )
    planner = MetricIntentPlanner(retriever=retriever, llm=llm)
    database = FakeDatabaseGateway()
    gateway = metrics_gateway or FakeMetricGateway()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        metric_gateway=gateway,
        metric_registry=registry,
        metric_intent_planner=planner,
        enable_query_router=True,
        generate_answer=generate_answer,
    )
    return graph, gateway, database


async def run(graph: Any, question: str) -> dict[str, Any]:
    result: dict[str, Any] = await graph.ainvoke(
        {
            "request_id": "semantic-request",
            "trace_id": "semantic-trace",
            "thread_id": "semantic-thread",
            "question": question,
        }
    )
    return result


@pytest.mark.anyio
async def test_alias_free_paraphrase_routes_governed_through_the_graph() -> None:
    llm = SelectingLLM(
        MetricSelection(
            intent="governed",
            metrics=["annual_base_payroll"],
            dimensions=["department"],
            confidence=0.94,
        )
    )
    graph, metrics, database = await semantic_graph(llm)

    result = await run(graph, PARAPHRASE)

    assert result["execution_route"] == "governed_metric"
    assert llm.intent_calls == 1, "the semantic planner was not consulted"
    assert len(metrics.queries) == 1
    assert metrics.queries[0].metric == "annual_base_payroll"
    # Governed execution must not fall through to ad-hoc SQL.
    assert database.executed_sql == []


@pytest.mark.anyio
async def test_an_invented_metric_never_executes() -> None:
    llm = SelectingLLM(
        MetricSelection(intent="governed", metrics=["fake_profit_metric"])
    )
    graph, metrics, _ = await semantic_graph(llm)

    result = await run(graph, PARAPHRASE)

    assert result["execution_route"] == "adhoc_analytics"
    assert metrics.queries == [], "an invented metric reached the metric gateway"


@pytest.mark.anyio
async def test_an_uncertified_metric_never_executes() -> None:
    downgraded = [
        metric.model_copy(update={"status": MetricStatus.PROPOSED})
        if metric.metric_key == "annual_base_payroll"
        else metric
        for metric in registered_metrics_for_default_datasource(DEFAULT_DATA_SOURCE_ID)
    ]
    llm = SelectingLLM(
        MetricSelection(intent="governed", metrics=["annual_base_payroll"])
    )
    graph, metrics, _ = await semantic_graph(llm, registry_metrics=downgraded)

    result = await run(graph, PARAPHRASE)

    assert result["execution_route"] == "adhoc_analytics"
    assert metrics.queries == []


@pytest.mark.anyio
async def test_no_relevant_governed_metric_falls_back_to_adhoc() -> None:
    llm = SelectingLLM(MetricSelection(intent="adhoc"))
    graph, metrics, database = await semantic_graph(llm)

    result = await run(graph, "List every employee hired in the last week")

    assert result["execution_route"] == "adhoc_analytics"
    assert metrics.queries == []
    assert database.executed_sql, "ad-hoc fallback did not reach SQL execution"


@pytest.mark.anyio
async def test_a_datasource_with_no_metrics_cannot_route_governed() -> None:
    """Datasource isolation at runtime: another datasource's metrics are unusable."""
    other = registered_metrics_for_default_datasource(uuid4())
    llm = SelectingLLM(
        MetricSelection(intent="governed", metrics=["annual_base_payroll"])
    )
    graph, metrics, _ = await semantic_graph(llm, registry_metrics=other)

    result = await run(graph, PARAPHRASE)

    assert result["execution_route"] == "adhoc_analytics"
    assert metrics.queries == []


@pytest.mark.anyio
async def test_write_intent_is_blocked_before_any_semantic_planning() -> None:
    """Deterministic safety still runs first and must not consult a model."""
    llm = SelectingLLM(
        MetricSelection(intent="governed", metrics=["annual_base_payroll"])
    )
    graph, metrics, _ = await semantic_graph(llm)

    result = await run(graph, "delete all employees from the payroll table")

    assert result["execution_route"] == "block"
    assert llm.intent_calls == 0, "a blocked question reached the planner"
    assert metrics.queries == []


@pytest.mark.anyio
async def test_multiple_metrics_compose_at_the_requested_grain() -> None:
    llm = SelectingLLM(
        MetricSelection(
            intent="governed",
            metrics=["active_headcount", "annual_base_payroll"],
            dimensions=["department"],
        )
    )
    graph, metrics, database = await semantic_graph(llm)

    result = await run(graph, "headcount and payroll by department")

    assert result["execution_route"] == "governed_metric"
    # One governed query per metric, each grouped by the planned grain, so the
    # join is one-to-one and cannot fan out.
    assert [query.metric for query in metrics.queries] == [
        "active_headcount",
        "annual_base_payroll",
    ]
    assert all(query.dimensions == ("department",) for query in metrics.queries)
    assert database.executed_sql == []


@pytest.mark.anyio
async def test_the_planner_prompt_carries_no_physical_schema() -> None:
    llm = SelectingLLM(
        MetricSelection(intent="governed", metrics=["annual_base_payroll"])
    )
    graph, _, _ = await semantic_graph(llm)

    await run(graph, PARAPHRASE)

    prompt = llm.last_intent_prompt.lower()
    for leaked in ("select ", "sum(", "analytics.", " join "):
        assert leaked not in prompt, f"prompt leaked physical detail: {leaked!r}"


@pytest.mark.anyio
async def test_semantic_routing_does_not_depend_on_literal_alias_matching() -> None:
    """The structural regression guard for the whole change.

    The deterministic router is asked the same question directly. It finds no
    alias, so it would route ad-hoc with no metric candidates at all. The graph
    still reaches governed execution, which can only happen through semantic
    retrieval. If alias matching ever became the decision again, the first
    assertion would still hold and the second would fail.
    """
    alias_decision = DeterministicQueryRouter().route(
        PARAPHRASE,
        prior_context=None,
        allowed_metric_ids=frozenset(
            metric.metric_key
            for metric in registered_metrics_for_default_datasource(
                DEFAULT_DATA_SOURCE_ID
            )
        ),
    )
    assert alias_decision.route.value == "adhoc_analytics"
    assert alias_decision.metric_candidates == ()

    llm = SelectingLLM(
        MetricSelection(
            intent="governed",
            metrics=["annual_base_payroll"],
            dimensions=["department"],
        )
    )
    graph, metrics, _ = await semantic_graph(llm)

    result = await run(graph, PARAPHRASE)

    assert result["execution_route"] == "governed_metric"
    assert metrics.queries[0].metric == "annual_base_payroll"
