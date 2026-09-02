"""The learning loop end to end, with deterministic fakes.

events -> cluster -> candidate -> PROPOSED -> review -> CERTIFIED -> retrievable

The important assertions are the negative ones: a PROPOSED candidate must be
invisible to governed runtime, a rejected one must not come back, and approval
must refuse anything that cannot execute safely.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.embeddings.fake import HashingEmbeddingGateway
from app.knowledge.candidates import (
    BusinessRuleProposal,
    CandidateGeneration,
    CandidateGenerator,
    CandidateReview,
    CandidateReviewError,
    CandidateStatus,
    CandidateType,
    InMemoryCandidateStore,
    KnowledgeCandidate,
    MetricProposal,
)
from app.knowledge.expressions import BinaryOp, MetricRef, evaluate
from app.knowledge.fingerprints import governed_fingerprint
from app.knowledge.guidance import InMemoryGuidanceStore
from app.knowledge.memory import InMemoryQuestionMemory, QuestionEvent
from app.knowledge.metrics import InMemoryMetricRegistry, MetricStatus
from app.knowledge.retrieval import MetricRetriever
from app.knowledge.seed import registered_metrics_for_default_datasource
from app.llm.gateway import LLMGateway, ResponseModelT

SOURCE_A = uuid4()
SOURCE_B = uuid4()

REVENUE_PER_EMPLOYEE_PARAPHRASES = [
    "payroll per employee",
    "how much base pay do we commit per active worker?",
    "payroll divided by headcount",
]

#: All three paraphrases resolve to the same analytical structure.
SHARED_FINGERPRINT = governed_fingerprint(
    metric_keys=["annual_base_payroll", "active_headcount"],
    dimensions=["department"],
)

#: Both inputs offer `department`, so this is genuinely composable at that
#: grain. `invoice_amount` deliberately is not used here: it offers customer and
#: project but not department, and a derived metric over it could not be grouped
#: by department -- see the refusal test below.
PROPOSED_EXPRESSION = BinaryOp(
    operator="divide",
    left=MetricRef(metric_key="annual_base_payroll"),
    right=MetricRef(metric_key="active_headcount"),
)


class ScriptedGenerator(LLMGateway):
    """Returns one fixed candidate proposal."""

    def __init__(self, generation: CandidateGeneration) -> None:
        self._generation = generation
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
        return self._generation  # type: ignore[return-value]


def payroll_per_employee_generation(
    *, metric_key: str = "annual_payroll_per_active_employee"
) -> CandidateGeneration:
    return CandidateGeneration(
        proposes=True,
        metric=MetricProposal(
            metric_key=metric_key,
            display_name="Annual Payroll Per Active Employee",
            description="Annual base payroll divided by active employee count.",
            business_meaning="Base pay committed per active employee.",
            expression=PROPOSED_EXPRESSION,
            grain="department",
            dimensions=["department"],
        ),
    )


async def accumulate_cluster(
    memory: InMemoryQuestionMemory, source: Any, *, trustworthy: bool = True
) -> Any:
    cluster = None
    for question in REVENUE_PER_EMPLOYEE_PARAPHRASES:
        cluster = await memory.record(
            QuestionEvent(
                data_source_id=source,
                question_text=question,
                structural_fingerprint=SHARED_FINGERPRINT,
                route="governed_metric",
                metric_keys=("annual_base_payroll", "active_headcount"),
                success=trustworthy,
                validated=trustworthy,
                grounded=trustworthy,
            )
        )
    assert cluster is not None
    return cluster


@pytest.mark.anyio
async def test_full_learning_loop_from_repetition_to_governed_retrieval() -> None:
    memory = InMemoryQuestionMemory()
    registry = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(SOURCE_A)
    )
    store = InMemoryCandidateStore()

    # 1. Repetition forms one datasource-scoped cluster.
    cluster = await accumulate_cluster(memory, SOURCE_A)
    assert cluster.occurrence_count == 3
    assert cluster.successful_count == 3
    assert cluster.is_eligible_for_proposal(min_occurrences=3, min_successful=3)

    # 2. One bounded generation call proposes a candidate.
    llm = ScriptedGenerator(payroll_per_employee_generation())
    generator = CandidateGenerator(llm=llm, store=store, registry=registry)
    candidate = await generator.propose_for_cluster(
        data_source_id=SOURCE_A,
        cluster=cluster,
        example_questions=REVENUE_PER_EMPLOYEE_PARAPHRASES,
    )
    assert candidate is not None
    assert candidate.status is CandidateStatus.PROPOSED
    assert llm.calls == 1

    # 3. PROPOSED is invisible to governed runtime.
    certified_keys = {
        metric.metric_key for metric in await registry.certified(SOURCE_A)
    }
    assert "annual_payroll_per_active_employee" not in certified_keys

    retriever = MetricRetriever(HashingEmbeddingGateway())
    await retriever.index(SOURCE_A, await registry.certified(SOURCE_A))
    before = await retriever.retrieve(
        data_source_id=SOURCE_A,
        question="payroll per employee",
        authorized_metrics=await registry.certified(SOURCE_A),
        limit=10,
    )
    assert "annual_payroll_per_active_employee" not in {c.metric_key for c in before}

    # 4. Approval validates, then certifies.
    review = CandidateReview(store=store, registry=registry)
    promoted = await review.approve_metric(
        SOURCE_A, candidate.id, reviewed_by="reviewer"
    )
    assert promoted.status is MetricStatus.CERTIFIED
    assert set(promoted.dependencies) == {"active_headcount", "annual_base_payroll"}
    assert promoted.semantic_expression == "(annual_base_payroll / active_headcount)"
    assert promoted.approved_by == "reviewer"
    assert promoted.source_candidate_id == candidate.id
    reviewed = await store.by_id(SOURCE_A, candidate.id)
    assert reviewed is not None
    assert reviewed.promoted_to_type == "METRIC"
    assert reviewed.promoted_to_id == promoted.id

    # 5. Reindexing makes it retrievable to future requests.
    await retriever.index(SOURCE_A, await registry.certified(SOURCE_A))
    after = await retriever.retrieve(
        data_source_id=SOURCE_A,
        question="payroll per employee",
        authorized_metrics=await registry.certified(SOURCE_A),
        limit=10,
    )
    assert "annual_payroll_per_active_employee" in {c.metric_key for c in after}

    # 6. The certified expression computes from governed measures, not SQL.
    # Engineering's real figures from the demo database: 710000 / 4.
    assert evaluate(
        PROPOSED_EXPRESSION,
        {"annual_base_payroll": Decimal("710000"), "active_headcount": Decimal("4")},
    ) == Decimal("177500")


@pytest.mark.anyio
async def test_a_rejected_candidate_is_not_regenerated_from_the_same_evidence() -> None:
    memory = InMemoryQuestionMemory()
    registry = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(SOURCE_A)
    )
    store = InMemoryCandidateStore()
    cluster = await accumulate_cluster(memory, SOURCE_A)

    llm = ScriptedGenerator(payroll_per_employee_generation())
    generator = CandidateGenerator(llm=llm, store=store, registry=registry)
    candidate = await generator.propose_for_cluster(
        data_source_id=SOURCE_A,
        cluster=cluster,
        example_questions=REVENUE_PER_EMPLOYEE_PARAPHRASES,
    )
    assert candidate is not None

    review = CandidateReview(store=store, registry=registry)
    rejected = await review.reject(
        SOURCE_A, candidate.id, reason="Not a durable business metric."
    )
    assert rejected.status is CandidateStatus.REJECTED

    # Same evidence again: no second proposal, and no second model call.
    calls_before = llm.calls
    again = await generator.propose_for_cluster(
        data_source_id=SOURCE_A,
        cluster=cluster,
        example_questions=REVENUE_PER_EMPLOYEE_PARAPHRASES,
    )
    assert again is None
    assert llm.calls == calls_before

    # And it never becomes certified.
    with pytest.raises(CandidateReviewError):
        await review.approve_metric(SOURCE_A, candidate.id)
    assert "annual_payroll_per_active_employee" not in {
        metric.metric_key for metric in await registry.certified(SOURCE_A)
    }


@pytest.mark.anyio
async def test_approval_refuses_a_dependency_that_is_not_certified() -> None:
    registry = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(SOURCE_A)
    )
    store = InMemoryCandidateStore()
    memory = InMemoryQuestionMemory()
    cluster = await accumulate_cluster(memory, SOURCE_A)

    generator = CandidateGenerator(
        llm=ScriptedGenerator(payroll_per_employee_generation()),
        store=store,
        registry=registry,
    )
    candidate = await generator.propose_for_cluster(
        data_source_id=SOURCE_A,
        cluster=cluster,
        example_questions=REVENUE_PER_EMPLOYEE_PARAPHRASES,
    )
    assert candidate is not None

    # A dependency is deprecated between proposal and approval.
    await registry.set_status(
        SOURCE_A, "annual_base_payroll", MetricStatus.DEPRECATED
    )

    review = CandidateReview(store=store, registry=registry)
    with pytest.raises(CandidateReviewError, match="not certified"):
        await review.approve_metric(SOURCE_A, candidate.id)


@pytest.mark.anyio
async def test_reviewed_business_rule_is_persisted_and_retrieved_by_relevance() -> None:
    store = InMemoryCandidateStore()
    registry = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(SOURCE_A)
    )
    guidance = InMemoryGuidanceStore()
    candidate = KnowledgeCandidate(
        data_source_id=SOURCE_A,
        candidate_type=CandidateType.BUSINESS_RULE,
        display_name="Current annual payroll population",
        structural_fingerprint="business-rule-payroll-population",
        proposal=BusinessRuleProposal(
            display_name="Current annual payroll population",
            instruction=(
                "Current annual payroll represents all current compensation records. "
                "Employee employment status does not restrict payroll unless the user "
                "explicitly asks for active-employee payroll."
            ),
            semantic_concepts=["current annual payroll", "compensation"],
            metric_keys=["annual_base_payroll"],
        ),
    )
    await store.upsert(candidate)

    review = CandidateReview(store=store, registry=registry, guidance=guidance)
    approved = await review.approve_business_rule(
        SOURCE_A, candidate.id, reviewed_by="reviewer"
    )

    assert approved.source_candidate_id == candidate.id
    assert approved.approved_by == "reviewer"
    reviewed = await store.by_id(SOURCE_A, candidate.id)
    assert reviewed is not None
    assert reviewed.promoted_to_type == "BUSINESS_RULE"
    assert reviewed.promoted_to_id == approved.id
    payroll = await guidance.relevant_instructions(
        SOURCE_A, "What is our current annual payroll?"
    )
    headcount = await guidance.relevant_instructions(
        SOURCE_A, "How many active employees do we have?"
    )
    margin = await guidance.relevant_instructions(
        SOURCE_A, "Which customer has the highest project margin?"
    )
    assert [item.title for item in payroll] == ["Current annual payroll population"]
    assert headcount == []
    assert margin == []


@pytest.mark.anyio
async def test_a_proposal_referencing_an_unknown_metric_is_never_stored() -> None:
    registry = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(SOURCE_A)
    )
    store = InMemoryCandidateStore()
    memory = InMemoryQuestionMemory()
    cluster = await accumulate_cluster(memory, SOURCE_A)

    bogus = CandidateGeneration(
        proposes=True,
        metric=MetricProposal(
            metric_key="fabricated_ratio",
            display_name="Fabricated Ratio",
            expression=BinaryOp(
                operator="divide",
                left=MetricRef(metric_key="does_not_exist"),
                right=MetricRef(metric_key="active_headcount"),
            ),
        ),
    )
    generator = CandidateGenerator(
        llm=ScriptedGenerator(bogus), store=store, registry=registry
    )

    assert (
        await generator.propose_for_cluster(
            data_source_id=SOURCE_A,
            cluster=cluster,
            example_questions=REVENUE_PER_EMPLOYEE_PARAPHRASES,
        )
        is None
    )
    assert await store.list(SOURCE_A) == []


@pytest.mark.anyio
async def test_candidates_do_not_cross_datasources() -> None:
    registry_a = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(SOURCE_A)
    )
    store = InMemoryCandidateStore()
    memory = InMemoryQuestionMemory()
    cluster_a = await accumulate_cluster(memory, SOURCE_A)

    generator = CandidateGenerator(
        llm=ScriptedGenerator(payroll_per_employee_generation()),
        store=store,
        registry=registry_a,
    )
    candidate = await generator.propose_for_cluster(
        data_source_id=SOURCE_A,
        cluster=cluster_a,
        example_questions=REVENUE_PER_EMPLOYEE_PARAPHRASES,
    )
    assert candidate is not None

    assert await store.list(SOURCE_B) == []
    review = CandidateReview(store=store, registry=registry_a)
    with pytest.raises(CandidateReviewError, match="No such candidate"):
        await review.approve_metric(SOURCE_B, candidate.id)


@pytest.mark.anyio
async def test_an_edit_stays_proposed_and_must_still_pass_validation() -> None:
    registry = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(SOURCE_A)
    )
    store = InMemoryCandidateStore()
    memory = InMemoryQuestionMemory()
    cluster = await accumulate_cluster(memory, SOURCE_A)

    generator = CandidateGenerator(
        llm=ScriptedGenerator(payroll_per_employee_generation()),
        store=store,
        registry=registry,
    )
    candidate = await generator.propose_for_cluster(
        data_source_id=SOURCE_A,
        cluster=cluster,
        example_questions=REVENUE_PER_EMPLOYEE_PARAPHRASES,
    )
    assert candidate is not None

    review = CandidateReview(store=store, registry=registry)
    edited = await review.edit(
        SOURCE_A,
        candidate.id,
        proposal=MetricProposal(
            metric_key="annual_payroll_per_active_employee",
            display_name="Revenue Per Head",
            expression=BinaryOp(
                operator="divide",
                left=MetricRef(metric_key="annual_base_payroll"),
                right=MetricRef(metric_key="nonexistent_metric"),
            ),
        ),
        reviewed_by="reviewer",
    )
    assert edited.status is CandidateStatus.PROPOSED
    assert edited.version == candidate.version + 1

    with pytest.raises(CandidateReviewError, match="not certified"):
        await review.approve_metric(SOURCE_A, edited.id)


@pytest.mark.anyio
async def test_a_derived_metric_cannot_offer_a_dimension_its_inputs_lack() -> None:
    registry = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(SOURCE_A)
    )
    store = InMemoryCandidateStore()
    memory = InMemoryQuestionMemory()
    cluster = await accumulate_cluster(memory, SOURCE_A)

    generation = CandidateGeneration(
        proposes=True,
        metric=MetricProposal(
            metric_key="annual_payroll_per_active_employee",
            display_name="Annual Payroll Per Active Employee",
            expression=PROPOSED_EXPRESSION,
            dimensions=["invoice_status"],
        ),
    )
    generator = CandidateGenerator(
        llm=ScriptedGenerator(generation), store=store, registry=registry
    )
    candidate = await generator.propose_for_cluster(
        data_source_id=SOURCE_A,
        cluster=cluster,
        example_questions=REVENUE_PER_EMPLOYEE_PARAPHRASES,
    )
    assert candidate is not None

    review = CandidateReview(store=store, registry=registry)
    with pytest.raises(CandidateReviewError, match="not offered by dependency"):
        await review.approve_metric(SOURCE_A, candidate.id)


@pytest.mark.anyio
async def test_revenue_per_employee_by_department_is_refused_as_uncomposable() -> None:
    """A real constraint of the demo catalog, not a limitation of review.

    `invoice_amount` is dimensioned by customer, project and invoice status but
    not by department, so revenue per employee cannot be grouped by department
    without inventing a mapping. Approval refuses rather than certifying a
    metric whose execution would have to guess.
    """
    registry = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(SOURCE_A)
    )
    store = InMemoryCandidateStore()
    memory = InMemoryQuestionMemory()
    cluster = await accumulate_cluster(memory, SOURCE_A)

    generation = CandidateGeneration(
        proposes=True,
        metric=MetricProposal(
            metric_key="revenue_per_active_employee",
            display_name="Revenue Per Active Employee",
            expression=BinaryOp(
                operator="divide",
                left=MetricRef(metric_key="invoice_amount"),
                right=MetricRef(metric_key="active_headcount"),
            ),
            dimensions=["department"],
        ),
    )
    generator = CandidateGenerator(
        llm=ScriptedGenerator(generation), store=store, registry=registry
    )
    candidate = await generator.propose_for_cluster(
        data_source_id=SOURCE_A,
        cluster=cluster,
        example_questions=REVENUE_PER_EMPLOYEE_PARAPHRASES,
    )
    assert candidate is not None

    review = CandidateReview(store=store, registry=registry)
    with pytest.raises(CandidateReviewError, match="not offered by dependency"):
        await review.approve_metric(SOURCE_A, candidate.id)

    assert "revenue_per_active_employee" not in {
        metric.metric_key for metric in await registry.certified(SOURCE_A)
    }
