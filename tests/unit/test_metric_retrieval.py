from uuid import uuid4

import pytest

from app.embeddings.fake import HashingEmbeddingGateway
from app.knowledge.metrics import (
    InMemoryMetricRegistry,
    MetricStatus,
    RegisteredMetric,
)
from app.knowledge.retrieval import MetricRetriever
from app.knowledge.seed import registered_metrics_for_default_datasource

SOURCE_A = uuid4()
SOURCE_B = uuid4()

#: The question this architecture exists to answer. It is deliberately phrased
#: the way a business user would and shares no configured alias with any metric.
PARAPHRASE = (
    "How much money does the organization commit to employee base "
    "compensation each year?"
)


async def retriever_for(
    data_source_id: uuid4,  # type: ignore[valid-type]
    metrics: list[RegisteredMetric],
) -> MetricRetriever:
    retriever = MetricRetriever(HashingEmbeddingGateway())
    await retriever.index(data_source_id, metrics)
    return retriever


# --------------------------------------------------------------------------
# The main goal: retrieval by meaning, not by alias
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_broad_paraphrase_retrieves_the_right_metric_without_any_alias() -> None:
    metrics = registered_metrics_for_default_datasource(SOURCE_A)
    payroll = next(m for m in metrics if m.metric_key == "annual_base_payroll")

    # Precondition: the question must not contain any configured alias, or the
    # test would prove nothing beyond substring matching.
    lowered = PARAPHRASE.casefold()
    assert not [c for c in payroll.concepts if c.casefold() in lowered]
    assert "payroll" not in lowered
    assert "salary" not in lowered

    retriever = await retriever_for(SOURCE_A, metrics)
    results = await retriever.retrieve(
        data_source_id=SOURCE_A, question=PARAPHRASE, authorized_metrics=metrics
    )

    assert results[0].metric_key == "annual_base_payroll"
    # A clear win, not a coin flip between the top two.
    assert results[0].score > results[1].score * 1.5


@pytest.mark.anyio
async def test_retrieval_uses_both_signals() -> None:
    metrics = registered_metrics_for_default_datasource(SOURCE_A)
    retriever = await retriever_for(SOURCE_A, metrics)

    top = (
        await retriever.retrieve(
            data_source_id=SOURCE_A, question=PARAPHRASE, authorized_metrics=metrics
        )
    )[0]

    assert top.vector_similarity > 0.0
    assert top.lexical_score > 0.0
    assert 0.0 < top.score <= 1.0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Which projects are making us the most profit?", "project_margin"),
        ("How many people work in each team?", "active_headcount"),
        ("What have we billed our customers?", "invoice_amount"),
    ],
)
async def test_other_paraphrases_retrieve_their_metric(
    question: str, expected: str
) -> None:
    metrics = registered_metrics_for_default_datasource(SOURCE_A)
    retriever = await retriever_for(SOURCE_A, metrics)

    results = await retriever.retrieve(
        data_source_id=SOURCE_A, question=question, authorized_metrics=metrics
    )

    assert results[0].metric_key == expected


# --------------------------------------------------------------------------
# Authorization and status gate retrieval
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unauthorized_metrics_are_never_retrieved() -> None:
    """Retrieval must not reveal that a metric the caller cannot use exists."""
    metrics = registered_metrics_for_default_datasource(SOURCE_A)
    retriever = await retriever_for(SOURCE_A, metrics)
    authorized = [m for m in metrics if m.metric_key != "annual_base_payroll"]

    results = await retriever.retrieve(
        data_source_id=SOURCE_A, question=PARAPHRASE, authorized_metrics=authorized
    )

    assert "annual_base_payroll" not in {c.metric_key for c in results}


@pytest.mark.anyio
async def test_only_certified_metrics_are_indexed_or_retrieved() -> None:
    """A proposed candidate must not influence a live answer."""
    metrics = registered_metrics_for_default_datasource(SOURCE_A)
    proposed = [
        m.model_copy(update={"status": MetricStatus.PROPOSED})
        if m.metric_key == "annual_base_payroll"
        else m
        for m in metrics
    ]

    retriever = await retriever_for(SOURCE_A, proposed)
    results = await retriever.retrieve(
        data_source_id=SOURCE_A, question=PARAPHRASE, authorized_metrics=proposed
    )

    assert "annual_base_payroll" not in {c.metric_key for c in results}


@pytest.mark.anyio
async def test_rejected_metrics_are_excluded() -> None:
    metrics = [
        m.model_copy(update={"status": MetricStatus.REJECTED})
        for m in registered_metrics_for_default_datasource(SOURCE_A)
    ]

    retriever = await retriever_for(SOURCE_A, metrics)
    results = await retriever.retrieve(
        data_source_id=SOURCE_A, question=PARAPHRASE, authorized_metrics=metrics
    )

    assert results == []


# --------------------------------------------------------------------------
# Datasource isolation
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_metrics_never_leak_between_datasources() -> None:
    a_metrics = registered_metrics_for_default_datasource(SOURCE_A)
    b_metrics = registered_metrics_for_default_datasource(SOURCE_B)

    retriever = MetricRetriever(HashingEmbeddingGateway())
    await retriever.index(SOURCE_A, a_metrics)
    await retriever.index(SOURCE_B, b_metrics)

    # Asking B while offering A's metric objects must return nothing: the
    # objects belong to another datasource.
    leaked = await retriever.retrieve(
        data_source_id=SOURCE_B, question=PARAPHRASE, authorized_metrics=a_metrics
    )
    assert leaked == []

    own = await retriever.retrieve(
        data_source_id=SOURCE_B, question=PARAPHRASE, authorized_metrics=b_metrics
    )
    assert own[0].metric.data_source_id == SOURCE_B


@pytest.mark.anyio
async def test_an_unindexed_datasource_retrieves_nothing() -> None:
    metrics = registered_metrics_for_default_datasource(SOURCE_A)
    retriever = await retriever_for(SOURCE_A, metrics)

    assert (
        await retriever.retrieve(
            data_source_id=SOURCE_B, question=PARAPHRASE, authorized_metrics=metrics
        )
        == []
    )


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_registry_is_datasource_scoped() -> None:
    registry = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(SOURCE_A)
        + registered_metrics_for_default_datasource(SOURCE_B)
    )

    a_certified = await registry.certified(SOURCE_A)
    b_certified = await registry.certified(SOURCE_B)

    assert {m.data_source_id for m in a_certified} == {SOURCE_A}
    assert {m.data_source_id for m in b_certified} == {SOURCE_B}
    assert await registry.get(SOURCE_A, "annual_base_payroll") is not None
    assert await registry.get(uuid4(), "annual_base_payroll") is None


@pytest.mark.anyio
async def test_seeded_demo_metrics_are_certified_so_current_behaviour_survives() -> None:
    registry = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(SOURCE_A)
    )

    certified = await registry.certified(SOURCE_A)

    assert {m.metric_key for m in certified} == {
        "active_headcount",
        "annual_base_payroll",
        "budget_utilization",
        "invoice_amount",
        "net_payroll",
        "project_cost",
        "project_margin",
    }
    assert all(m.status is MetricStatus.CERTIFIED for m in certified)


@pytest.mark.anyio
async def test_proposed_metric_is_invisible_until_certified() -> None:
    proposal = RegisteredMetric(
        data_source_id=SOURCE_A,
        metric_key="revenue_per_active_employee",
        display_name="Revenue Per Active Employee",
        status=MetricStatus.PROPOSED,
    )
    registry = InMemoryMetricRegistry([proposal])

    assert await registry.certified(SOURCE_A) == []

    promoted = await registry.set_status(
        SOURCE_A,
        "revenue_per_active_employee",
        MetricStatus.CERTIFIED,
        approved_by="reviewer",
    )

    assert promoted.status is MetricStatus.CERTIFIED
    assert promoted.approved_by == "reviewer"
    assert promoted.approved_at is not None
    assert len(await registry.certified(SOURCE_A)) == 1


@pytest.mark.anyio
async def test_registry_refuses_status_change_for_unknown_metric() -> None:
    registry = InMemoryMetricRegistry([])

    with pytest.raises(KeyError):
        await registry.set_status(SOURCE_A, "nope", MetricStatus.CERTIFIED)


def test_retrieval_document_leads_with_meaning() -> None:
    payroll = next(
        m
        for m in registered_metrics_for_default_datasource(SOURCE_A)
        if m.metric_key == "annual_base_payroll"
    )

    document = payroll.retrieval_document()

    assert document.startswith("Metric: Annual Base Payroll")
    assert "Business meaning:" in document
    assert "compensation" in document.casefold()
    assert "Example questions:" in document
    # The raw key is not what carries retrieval; meaning is.
    assert "annual_base_payroll" not in document
