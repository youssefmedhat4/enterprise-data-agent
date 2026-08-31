"""End-to-end metric intent planning: retrieval, model selection, validation.

These exercise the seam the architecture depends on -- that the model only ever
chooses among backend-supplied candidates, and that the validator, not the
model, decides what is executable.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.embeddings.fake import HashingEmbeddingGateway
from app.knowledge.metrics import MetricStatus, RegisteredMetric
from app.knowledge.planner import MetricIntentPlanner, MetricSelection
from app.knowledge.retrieval import MetricRetriever
from app.knowledge.seed import registered_metrics_for_default_datasource

SOURCE_A = uuid4()
SOURCE_B = uuid4()

PARAPHRASE = (
    "How much money does the organization commit to employee base "
    "compensation each year?"
)


class ScriptedLLM:
    """Returns a fixed selection and records what it was shown."""

    def __init__(self, selection: MetricSelection) -> None:
        self._selection = selection
        self.calls = 0
        self.last_user_prompt = ""
        self.last_system_prompt = ""

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[Any],
    ) -> Any:
        self.calls += 1
        self.last_system_prompt = system
        self.last_user_prompt = user
        return self._selection


async def planner_for(
    data_source_id: UUID,
    metrics: list[RegisteredMetric],
    selection: MetricSelection,
) -> tuple[MetricIntentPlanner, ScriptedLLM]:
    retriever = MetricRetriever(HashingEmbeddingGateway())
    await retriever.index(data_source_id, metrics)
    llm = ScriptedLLM(selection)
    planner = MetricIntentPlanner(retriever=retriever, llm=llm)
    return planner, llm


@pytest.mark.anyio
async def test_paraphrase_plans_a_governed_metric_without_any_alias() -> None:
    metrics = registered_metrics_for_default_datasource(SOURCE_A)
    planner, llm = await planner_for(
        SOURCE_A,
        metrics,
        MetricSelection(
            intent="governed",
            metrics=["annual_base_payroll"],
            dimensions=["department"],
            confidence=0.95,
        ),
    )

    outcome = await planner.plan(
        data_source_id=SOURCE_A, question=PARAPHRASE, authorized_metrics=metrics
    )

    assert outcome.is_governed
    assert outcome.plan is not None
    assert outcome.plan.metric_keys == ("annual_base_payroll",)
    assert llm.calls == 1


@pytest.mark.anyio
async def test_the_model_is_shown_no_physical_schema() -> None:
    metrics = registered_metrics_for_default_datasource(SOURCE_A)
    planner, llm = await planner_for(
        SOURCE_A,
        metrics,
        MetricSelection(intent="governed", metrics=["annual_base_payroll"]),
    )

    await planner.plan(
        data_source_id=SOURCE_A, question=PARAPHRASE, authorized_metrics=metrics
    )

    prompt = llm.last_user_prompt.lower()
    for leaked in ("select ", "sum(", "analytics.", "join ", "employees.salary"):
        assert leaked not in prompt, f"prompt leaked physical detail: {leaked!r}"


@pytest.mark.anyio
async def test_an_invented_metric_key_falls_back_to_adhoc_not_execution() -> None:
    metrics = registered_metrics_for_default_datasource(SOURCE_A)
    planner, _ = await planner_for(
        SOURCE_A,
        metrics,
        MetricSelection(intent="governed", metrics=["revenue_per_unicorn"]),
    )

    outcome = await planner.plan(
        data_source_id=SOURCE_A, question=PARAPHRASE, authorized_metrics=metrics
    )

    assert outcome.intent == "adhoc"
    assert outcome.plan is None


@pytest.mark.anyio
async def test_a_metric_the_caller_is_not_authorized_for_is_never_planned() -> None:
    metrics = registered_metrics_for_default_datasource(SOURCE_A)
    payroll = next(m for m in metrics if m.metric_key == "annual_base_payroll")
    authorized = [m for m in metrics if m.metric_key != "annual_base_payroll"]

    planner, _ = await planner_for(
        SOURCE_A,
        metrics,
        MetricSelection(intent="governed", metrics=[payroll.metric_key]),
    )

    outcome = await planner.plan(
        data_source_id=SOURCE_A, question=PARAPHRASE, authorized_metrics=authorized
    )

    assert outcome.plan is None


@pytest.mark.anyio
async def test_no_candidates_means_no_model_call_at_all() -> None:
    planner, llm = await planner_for(
        SOURCE_A,
        [],
        MetricSelection(intent="governed", metrics=["annual_base_payroll"]),
    )

    outcome = await planner.plan(
        data_source_id=SOURCE_A, question=PARAPHRASE, authorized_metrics=[]
    )

    assert outcome.intent == "adhoc"
    assert llm.calls == 0


@pytest.mark.anyio
async def test_metrics_from_another_datasource_are_never_planned() -> None:
    metrics_a = registered_metrics_for_default_datasource(SOURCE_A)
    planner, _ = await planner_for(
        SOURCE_A,
        metrics_a,
        MetricSelection(intent="governed", metrics=["annual_base_payroll"]),
    )

    # Same question, but asked of a datasource that was never indexed.
    outcome = await planner.plan(
        data_source_id=SOURCE_B, question=PARAPHRASE, authorized_metrics=metrics_a
    )

    assert outcome.intent == "adhoc"
    assert outcome.plan is None


@pytest.mark.anyio
async def test_clarify_intent_carries_the_question_and_no_plan() -> None:
    metrics = registered_metrics_for_default_datasource(SOURCE_A)
    planner, _ = await planner_for(
        SOURCE_A,
        metrics,
        MetricSelection(
            intent="clarify",
            clarification_question="Base pay only, or total cost?",
        ),
    )

    outcome = await planner.plan(
        data_source_id=SOURCE_A, question=PARAPHRASE, authorized_metrics=metrics
    )

    assert outcome.intent == "clarify"
    assert outcome.plan is None
    assert outcome.clarification_question == "Base pay only, or total cost?"


@pytest.mark.anyio
async def test_an_uncertified_metric_is_neither_retrieved_nor_planned() -> None:
    metrics = registered_metrics_for_default_datasource(SOURCE_A)
    proposed = [
        m.model_copy(update={"status": MetricStatus.PROPOSED})
        if m.metric_key == "annual_base_payroll"
        else m
        for m in metrics
    ]

    planner, _ = await planner_for(
        SOURCE_A,
        proposed,
        MetricSelection(intent="governed", metrics=["annual_base_payroll"]),
    )

    outcome = await planner.plan(
        data_source_id=SOURCE_A, question=PARAPHRASE, authorized_metrics=proposed
    )

    assert outcome.plan is None
