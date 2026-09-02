from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.api.routes import _knowledge_used
from app.contracts.analytics import AnswerTraceView, InternalProvenance, ResultMetadata
from app.knowledge.candidates import (
    CandidateStatus,
    CandidateType,
    InMemoryCandidateStore,
    KnowledgeCandidate,
    QueryExampleProposal,
)
from app.knowledge.guidance import (
    ApprovedQueryExample,
    BusinessInstruction,
    InMemoryGuidanceStore,
)
from app.knowledge.memory import InMemoryQuestionMemory, QuestionEvent
from app.knowledge.metrics import InMemoryMetricRegistry
from app.knowledge.runtime import KnowledgeRuntime
from app.knowledge.seed import registered_metrics_for_default_datasource

SOURCE = uuid4()
OTHER_SOURCE = uuid4()


def _provenance(*, example_id: UUID, instruction_id: UUID) -> InternalProvenance:
    return InternalProvenance(
        request_id="request",
        trace_id="trace",
        source="test",
        tables=[],
        columns=[],
        result=ResultMetadata(row_count=0, columns=[]),
        applied_example_ids=[str(example_id)],
        applied_instruction_ids=[str(instruction_id)],
        applied_instruction_titles=["Current payroll population"],
        metric_id="annual_base_payroll",
    )


async def _runtime() -> tuple[KnowledgeRuntime, ApprovedQueryExample, BusinessInstruction]:
    candidates = InMemoryCandidateStore()
    guidance = InMemoryGuidanceStore()
    memory = InMemoryQuestionMemory()
    cluster = await memory.record(
        QuestionEvent(
            data_source_id=SOURCE,
            question_text="Current and previous compensation",
            structural_fingerprint="compensation-history",
            route="adhoc_analytics",
            success=True,
            validated=True,
            grounded=True,
        )
    )
    example_id = uuid4()
    candidate = KnowledgeCandidate(
        data_source_id=SOURCE,
        candidate_type=CandidateType.QUERY_EXAMPLE,
        display_name="Current and previous compensation",
        structural_fingerprint="compensation-history",
        proposal=QueryExampleProposal(
            display_name="Current and previous compensation",
            question="Show current and previous compensation",
        ),
        cluster_id=cluster.id,
        evidence_count=2,
        successful_evidence_count=2,
        status=CandidateStatus.APPROVED,
        reviewed_at=datetime.now(UTC),
        reviewed_by="reviewer",
        promoted_to_type="QUERY_EXAMPLE",
        promoted_to_id=example_id,
    )
    await candidates.upsert(candidate)
    example = await guidance.approve_example(
        ApprovedQueryExample(
            id=example_id,
            data_source_id=SOURCE,
            question="Current and previous compensation",
            query_pattern="SELECT employee_id FROM analytics.compensation",
            source_candidate_id=candidate.id,
            source_cluster_id=cluster.id,
            approved_by="reviewer",
        ),
        was_successful=True,
        was_validated=True,
    )
    # Available but not selected: a trace must not become an inventory dump.
    await guidance.approve_example(
        ApprovedQueryExample(
            data_source_id=SOURCE,
            question="Unrelated invoice example",
            query_pattern="SELECT invoice_id FROM analytics.invoices",
        ),
        was_successful=True,
        was_validated=True,
    )
    instruction = await guidance.approve_instruction(
        BusinessInstruction(
            data_source_id=SOURCE,
            title="Current payroll population",
            instruction="Use current compensation records for annual payroll.",
        )
    )
    # A rejected proposal has no authoritative destination and cannot appear.
    await candidates.upsert(
        replace(
            candidate,
            id=uuid4(),
            display_name="Active employee count",
            structural_fingerprint="active-employee-rejected",
            status=CandidateStatus.REJECTED,
            promoted_to_type=None,
            promoted_to_id=None,
        )
    )
    runtime = cast(
        KnowledgeRuntime,
        SimpleNamespace(
            candidates=candidates,
            guidance=guidance,
            memory=memory,
            registry=InMemoryMetricRegistry(
                registered_metrics_for_default_datasource(SOURCE)
            ),
        ),
    )
    return runtime, example, instruction


@pytest.mark.anyio
async def test_trace_contains_only_selected_authoritative_knowledge_and_origins() -> None:
    runtime, example, instruction = await _runtime()

    used = await _knowledge_used(
        knowledge=runtime,
        data_source_id=SOURCE,
        provenance=_provenance(example_id=example.id, instruction_id=instruction.id),
        include_details=True,
    )

    assert [item.kind for item in used] == [
        "QUERY_EXAMPLE",
        "BUSINESS_RULE",
        "CERTIFIED_METRIC",
    ]
    assert used[0].usage == "PLANNING_CONTEXT"
    assert used[0].origin.type == "LEARNED"
    assert used[0].origin.candidate_status == "APPROVED"
    assert used[0].origin.evidence_count == 2
    assert used[1].origin.type == "MANUAL"
    assert used[2].origin.type == "SEEDED"
    assert "Unrelated" not in {item.name for item in used}
    assert all("active employee" not in item.name.casefold() for item in used)
    serialized = AnswerTraceView(
        data_source="test",
        route="adhoc_analytics",
        execution_source="database",
        knowledge_used=used,
    ).model_dump(mode="json")
    assert serialized["knowledge_used"][0]["origin"]["candidate_id"] is not None


@pytest.mark.anyio
async def test_candidate_and_cluster_details_are_redacted_without_review_permission() -> None:
    runtime, example, instruction = await _runtime()

    used = await _knowledge_used(
        knowledge=runtime,
        data_source_id=SOURCE,
        provenance=_provenance(example_id=example.id, instruction_id=instruction.id),
        include_details=False,
    )

    learned = used[0].origin
    assert learned.type == "LEARNED"
    assert learned.candidate_id is None
    assert learned.cluster_id is None
    assert learned.candidate_name is None
    assert learned.evidence_count is None


@pytest.mark.anyio
async def test_a_rejected_candidate_is_never_reported_as_a_learned_origin() -> None:
    """Rejection is a decision, and it outlives whatever the store still holds.

    A promoted row keeps its `source_candidate_id` even if that candidate is
    later rejected. Reading the link alone would then describe the knowledge as
    human-approved learning, which is the opposite of what happened, so the
    approval state is checked rather than assumed.
    """
    runtime, _, _ = await _runtime()
    rejected = KnowledgeCandidate(
        data_source_id=SOURCE,
        candidate_type=CandidateType.QUERY_EXAMPLE,
        display_name="Active employee count",
        structural_fingerprint="active-employee-count",
        proposal=QueryExampleProposal(
            display_name="Active employee count",
            question="How many employees are active?",
        ),
        status=CandidateStatus.REJECTED,
    )
    await runtime.candidates.upsert(rejected)
    example = await runtime.guidance.approve_example(
        ApprovedQueryExample(
            data_source_id=SOURCE,
            question="Active employee count",
            query_pattern="SELECT count(*) FROM analytics.employees",
            source_candidate_id=rejected.id,
        ),
        was_successful=True,
        was_validated=True,
    )
    provenance = _provenance(example_id=example.id, instruction_id=uuid4())
    provenance.metric_id = None

    used = await _knowledge_used(
        knowledge=runtime,
        data_source_id=SOURCE,
        provenance=provenance,
        include_details=True,
    )

    assert [item.id for item in used] == [str(example.id)]
    assert used[0].origin.type != "LEARNED"
    assert used[0].origin.candidate_id is None
    assert used[0].origin.candidate_status is None
    assert used[0].origin.review_decision is None


@pytest.mark.anyio
async def test_origin_candidate_from_another_datasource_is_never_joined() -> None:
    runtime, _, _ = await _runtime()
    foreign_candidate = KnowledgeCandidate(
        data_source_id=OTHER_SOURCE,
        candidate_type=CandidateType.QUERY_EXAMPLE,
        display_name="Private foreign candidate",
        structural_fingerprint="foreign",
        proposal=QueryExampleProposal(
            display_name="Private foreign candidate", question="Foreign question"
        ),
        status=CandidateStatus.APPROVED,
    )
    await runtime.candidates.upsert(foreign_candidate)
    example = await runtime.guidance.approve_example(
        ApprovedQueryExample(
            data_source_id=SOURCE,
            question="Local example",
            query_pattern="SELECT 1",
            source_candidate_id=foreign_candidate.id,
        ),
        was_successful=True,
        was_validated=True,
    )
    provenance = _provenance(example_id=example.id, instruction_id=uuid4())
    provenance.metric_id = None

    used = await _knowledge_used(
        knowledge=runtime,
        data_source_id=SOURCE,
        provenance=provenance,
        include_details=True,
    )

    assert len(used) == 1
    assert used[0].origin.type == "UNKNOWN"
    assert used[0].origin.candidate_id is None
