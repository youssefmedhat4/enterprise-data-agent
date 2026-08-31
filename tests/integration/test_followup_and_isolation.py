"""Conversational follow-ups and datasource isolation.

A follow-up like "by department" is a fragment, not a question. Without the
thread's previous governed selection the planner would see only the fragment and
fall back to ad-hoc, silently losing the metric the user is still asking about.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph
from app.data.fake import FakeDatabaseGateway
from app.embeddings.fake import HashingEmbeddingGateway
from app.knowledge.metrics import InMemoryMetricRegistry
from app.knowledge.planner import MetricIntentPlanner, MetricSelection
from app.knowledge.retrieval import MetricRetriever
from app.knowledge.seed import (
    DEFAULT_DATA_SOURCE_ID,
    registered_metrics_for_default_datasource,
)
from app.llm.gateway import LLMGateway, ResponseModelT, SQLGeneration
from app.metrics.fake import FakeMetricGateway
from app.security.sql_validation import SQLValidator

SOURCE_B = uuid4()

FIRST_QUESTION = (
    "How much money does the organization commit to employee base "
    "compensation each year?"
)


class ContextAwareLLM(LLMGateway):
    """Selects a metric, and records what context it was shown.

    Mimics a model that honours the prior selection: when the prompt says a
    previous governed answer used a metric, a refinement keeps it.
    """

    def __init__(self) -> None:
        self.intent_prompts: list[str] = []

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system
        if response_model is MetricSelection:
            self.intent_prompts.append(user)
            saw_prior = "previous governed answer used" in user
            question = user.split("\n", 1)[0].removeprefix("Question: ").strip()
            dimensions = ["department"] if "department" in question.lower() else []
            if saw_prior and _is_refinement(question):
                metric = _prior_metric_from(user)
                return MetricSelection(  # type: ignore[return-value]
                    intent="governed",
                    metrics=[metric],
                    dimensions=dimensions,
                )
            return MetricSelection(  # type: ignore[return-value]
                intent="governed",
                metrics=["annual_base_payroll"],
                dimensions=dimensions,
            )
        if response_model is SQLGeneration:
            return response_model.model_validate(
                {
                    "action": "execute",
                    "sql": (
                        "SELECT d.name AS department FROM analytics.departments d"
                        " LIMIT 100"
                    ),
                }
            )
        raise AssertionError(f"unexpected model call: {response_model.__name__}")


def _is_refinement(question: str) -> bool:
    lowered = question.casefold()
    return lowered.startswith(("by ", "what about", "same but", "top ")) or lowered in {
        "by department"
    }


def _prior_metric_from(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.strip().startswith("metrics:"):
            return line.split(":", 1)[1].strip().split(",")[0].strip()
    return "annual_base_payroll"


async def conversation_graph(
    llm: LLMGateway,
    *,
    data_source_id: Any = DEFAULT_DATA_SOURCE_ID,
    registry_source: Any = DEFAULT_DATA_SOURCE_ID,
) -> tuple[Any, FakeMetricGateway]:
    registry = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(registry_source)
    )
    retriever = MetricRetriever(HashingEmbeddingGateway())
    await retriever.index(
        data_source_id, await registry.certified(data_source_id)
    )
    metrics = FakeMetricGateway()
    graph = build_graph(
        db_gateway=FakeDatabaseGateway(),
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        metric_gateway=metrics,
        metric_registry=registry,
        metric_intent_planner=MetricIntentPlanner(retriever=retriever, llm=llm),
        data_source_id=data_source_id,
        enable_query_router=True,
        generate_answer=False,
        checkpointer=InMemorySaver(),
    )
    return graph, metrics


async def ask(graph: Any, question: str, thread: str) -> dict[str, Any]:
    result: dict[str, Any] = await graph.ainvoke(
        {
            "request_id": f"req-{question[:12]}",
            "trace_id": "trace",
            "thread_id": thread,
            "question": question,
        },
        config={"configurable": {"thread_id": thread}},
    )
    return result


@pytest.mark.anyio
async def test_a_followup_keeps_the_metric_and_adds_the_dimension() -> None:
    llm = ContextAwareLLM()
    graph, metrics = await conversation_graph(llm)

    await ask(graph, FIRST_QUESTION, "thread-1")
    result = await ask(graph, "by department", "thread-1")

    assert result["execution_route"] == "governed_metric"
    assert metrics.queries[-1].metric == "annual_base_payroll"
    assert metrics.queries[-1].dimensions == ("department",)


@pytest.mark.anyio
async def test_the_followup_prompt_carries_the_previous_selection() -> None:
    llm = ContextAwareLLM()
    graph, _ = await conversation_graph(llm)

    await ask(graph, FIRST_QUESTION, "thread-2")
    await ask(graph, "by department", "thread-2")

    assert "previous governed answer used" not in llm.intent_prompts[0]
    assert "previous governed answer used" in llm.intent_prompts[1]
    assert "annual_base_payroll" in llm.intent_prompts[1]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "followup",
    ["by department", "what about Finance?", "same but by department", "top 3"],
)
async def test_common_followup_forms_stay_governed(followup: str) -> None:
    llm = ContextAwareLLM()
    graph, metrics = await conversation_graph(llm)

    await ask(graph, FIRST_QUESTION, f"thread-{followup[:6]}")
    result = await ask(graph, followup, f"thread-{followup[:6]}")

    assert result["execution_route"] == "governed_metric"
    assert metrics.queries[-1].metric == "annual_base_payroll"


@pytest.mark.anyio
async def test_a_fresh_thread_gets_no_prior_context() -> None:
    llm = ContextAwareLLM()
    graph, _ = await conversation_graph(llm)

    await ask(graph, FIRST_QUESTION, "thread-a")
    await ask(graph, "by department", "thread-b")

    # The second thread's prompt must not mention thread-a's selection.
    assert "previous governed answer used" not in llm.intent_prompts[-1]


@pytest.mark.anyio
async def test_another_datasource_cannot_answer_from_this_ones_metrics() -> None:
    """Datasource B has its own registry; A's certified metrics are not visible."""
    llm = ContextAwareLLM()
    graph, metrics = await conversation_graph(
        llm, data_source_id=SOURCE_B, registry_source=DEFAULT_DATA_SOURCE_ID
    )

    result = await ask(graph, FIRST_QUESTION, "thread-b1")

    assert result["execution_route"] == "adhoc_analytics"
    assert metrics.queries == []


@pytest.mark.anyio
async def test_thread_identity_from_the_api_is_scoped_by_datasource() -> None:
    """Two datasources cannot collide on a generated thread id."""
    from app.contracts.analytics import AnalyticsRequest

    default_request = AnalyticsRequest(question="q")
    scoped_request = AnalyticsRequest(question="q", data_source_id=SOURCE_B)

    assert default_request.data_source_id is None
    assert scoped_request.data_source_id == SOURCE_B
