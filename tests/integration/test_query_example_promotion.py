"""Promoting a query example from a run that actually happened.

The lifecycle these cover:

    a successful, validated, grounded execution
    -> evidence of the statement that answered it
    -> a QUERY_EXAMPLE candidate carrying that statement
    -> a human approving it
    -> an approved example retrieved for a paraphrase
    -> fresh SQL, validated and executed on its own merits

The last step is the one worth being careful about: an approved example is
context a model reads, never a statement to run.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.data.gateway import ColumnMetadata, TableMetadata
from app.knowledge.candidates import (
    CandidateGeneration,
    CandidateGenerator,
    CandidateReview,
    CandidateReviewError,
    CandidateStatus,
    CandidateType,
    InMemoryCandidateStore,
    KnowledgeCandidate,
    QueryExampleProposal,
)
from app.knowledge.evidence import (
    ExecutionEvidence,
    InMemoryExecutionEvidenceStore,
    qualifies_as_evidence,
)
from app.knowledge.guidance import InMemoryGuidanceStore
from app.knowledge.memory import QuestionCluster
from app.knowledge.metrics import InMemoryMetricRegistry
from app.llm.gateway import LLMGateway, ResponseModelT
from app.security.sql_validation import SQLValidator

SOURCE_A = uuid4()
SOURCE_B = uuid4()
CLUSTER = uuid4()

#: The statement a real Legacy ERP run validated and executed.
EVIDENCE_SQL = (
    "WITH comp_history AS ("
    "SELECT emp_no, ann_sal_amt, curr_flg,"
    " LAG(ann_sal_amt) OVER (PARTITION BY emp_no ORDER BY eff_dt_chr)"
    " AS previous_compensation FROM erp.emp_comp_hist) "
    "SELECT emp_no, ann_sal_amt AS current_compensation, previous_compensation "
    "FROM comp_history WHERE curr_flg = 'Y'"
)

QUESTION = "Show each employee's current compensation and previous compensation."


def _erp_tables() -> list[TableMetadata]:
    return [
        TableMetadata(
            schema_name="erp",
            table_name="emp_comp_hist",
            columns=["emp_no", "ann_sal_amt", "curr_flg", "eff_dt_chr"],
            description="compensation history",
            column_metadata=[
                ColumnMetadata(name="emp_no", data_type="integer", nullable=False),
                ColumnMetadata(name="ann_sal_amt", data_type="numeric", nullable=False),
                ColumnMetadata(name="curr_flg", data_type="char", nullable=False),
                ColumnMetadata(name="eff_dt_chr", data_type="char", nullable=False),
            ],
        )
    ]


def _validator() -> SQLValidator:
    return SQLValidator(max_rows=1000, allowed_schemas=frozenset({"erp"}))


def _cluster(data_source_id: UUID = SOURCE_A) -> QuestionCluster:
    return QuestionCluster(
        id=CLUSTER,
        data_source_id=data_source_id,
        canonical_summary=QUESTION.casefold(),
        structural_fingerprint="v1|route=adhoc|tables=erp.emp_comp_hist",
        occurrence_count=4,
        successful_count=4,
    )


class _ProposesExample(LLMGateway):
    """A model that proposes the example but supplies no SQL of its own."""

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system, user
        return CandidateGeneration(  # type: ignore[return-value]
            proposes=True,
            reason="asked repeatedly and answered the same way each time",
            query_example=QueryExampleProposal(
                display_name="Current and previous compensation",
                question=QUESTION,
                semantic_plan="Compare each employee's current row to the prior one.",
            ),
        )


# --- what may become evidence -----------------------------------------------


@pytest.mark.parametrize(
    ("route", "sql", "succeeded", "validated", "grounded"),
    [
        # Answered from the governed metric layer, so there is no SQL to learn.
        ("governed_metric", "SELECT 1", True, True, True),
        # Repaired but never actually ran.
        ("adhoc_analytics", None, False, False, False),
        # Ran, but grounding refused the answer built from it.
        ("adhoc_analytics", "SELECT 1", True, True, False),
        # Executed without ever passing validation.
        ("adhoc_analytics", "SELECT 1", True, False, True),
        # Validated but the execution failed.
        ("adhoc_analytics", "SELECT 1", False, True, True),
        ("adhoc_analytics", "   ", True, True, True),
    ],
)
def test_a_run_that_did_not_earn_it_is_not_evidence(
    route: str, sql: str | None, succeeded: bool, validated: bool, grounded: bool
) -> None:
    assert not qualifies_as_evidence(
        route=route,
        validated_sql=sql,
        succeeded=succeeded,
        validated=validated,
        grounded=grounded,
    )


def test_a_successful_validated_grounded_adhoc_run_is_evidence() -> None:
    assert qualifies_as_evidence(
        route="adhoc_analytics",
        validated_sql=EVIDENCE_SQL,
        succeeded=True,
        validated=True,
        grounded=True,
    )


# --- generation --------------------------------------------------------------


@pytest.mark.anyio
async def test_a_candidate_carries_the_statement_a_run_validated() -> None:
    evidence = InMemoryExecutionEvidenceStore()
    await evidence.record(
        ExecutionEvidence(
            data_source_id=SOURCE_A,
            cluster_id=CLUSTER,
            question_text=QUESTION,
            validated_sql=EVIDENCE_SQL,
            schema_fingerprint="fp-1",
        )
    )
    store = InMemoryCandidateStore()
    candidate = await CandidateGenerator(
        llm=_ProposesExample(),
        store=store,
        registry=InMemoryMetricRegistry([]),
        evidence=evidence,
    ).propose_for_cluster(
        data_source_id=SOURCE_A, cluster=_cluster(), example_questions=[QUESTION]
    )

    assert candidate is not None
    assert candidate.candidate_type is CandidateType.QUERY_EXAMPLE
    assert candidate.status is CandidateStatus.PROPOSED
    # The statement comes from the run, not from the model.
    assert candidate.evidence_sql == EVIDENCE_SQL
    assert candidate.evidence_schema_fingerprint == "fp-1"


@pytest.mark.anyio
async def test_an_example_is_not_proposed_without_evidence_to_promote() -> None:
    """A candidate nobody could approve is a dead entry in the review queue."""
    candidate = await CandidateGenerator(
        llm=_ProposesExample(),
        store=InMemoryCandidateStore(),
        registry=InMemoryMetricRegistry([]),
        evidence=InMemoryExecutionEvidenceStore(),
    ).propose_for_cluster(
        data_source_id=SOURCE_A, cluster=_cluster(), example_questions=[QUESTION]
    )

    assert candidate is None


@pytest.mark.anyio
async def test_evidence_from_another_datasource_is_not_visible() -> None:
    evidence = InMemoryExecutionEvidenceStore()
    await evidence.record(
        ExecutionEvidence(
            data_source_id=SOURCE_B,
            cluster_id=CLUSTER,
            question_text=QUESTION,
            validated_sql=EVIDENCE_SQL,
        )
    )

    assert await evidence.for_cluster(SOURCE_A, CLUSTER) is None
    assert await evidence.for_cluster(SOURCE_B, CLUSTER) is not None


# --- approval ----------------------------------------------------------------


async def _proposed(
    store: InMemoryCandidateStore, *, sql: str | None = EVIDENCE_SQL
) -> KnowledgeCandidate:
    return await store.upsert(
        KnowledgeCandidate(
            data_source_id=SOURCE_A,
            candidate_type=CandidateType.QUERY_EXAMPLE,
            display_name="Current and previous compensation",
            structural_fingerprint="v1|route=adhoc|tables=erp.emp_comp_hist",
            proposal=QueryExampleProposal(
                display_name="Current and previous compensation",
                question=QUESTION,
                semantic_plan="Compare each employee's current row to the prior one.",
            ),
            cluster_id=CLUSTER,
            evidence_sql=sql,
            evidence_schema_fingerprint="fp-1",
        )
    )


@pytest.mark.anyio
async def test_approval_stores_the_reviewed_statement_as_an_example() -> None:
    store = InMemoryCandidateStore()
    guidance = InMemoryGuidanceStore()
    candidate = await _proposed(store)
    review = CandidateReview(
        store=store, registry=InMemoryMetricRegistry([]), guidance=guidance
    )

    example = await review.approve_query_example(
        SOURCE_A,
        candidate.id,
        validator=_validator(),
        authorized_tables=_erp_tables(),
        current_schema_fingerprint="fp-2",
        reviewed_by="reviewer",
    )

    assert example.query_pattern == EVIDENCE_SQL
    assert example.question == QUESTION
    assert example.schema_fingerprint == "fp-2"
    stored = await store.by_id(SOURCE_A, candidate.id)
    assert stored is not None
    assert stored.status is CandidateStatus.APPROVED
    assert [item.question for item in await guidance.examples(SOURCE_A)] == [QUESTION]


@pytest.mark.anyio
async def test_approval_is_refused_when_the_statement_no_longer_fits() -> None:
    """The column it selects has been dropped since the run.

    The example was correct when it ran and is wrong to keep now, and nothing
    announces that -- which is why it is re-validated at approval rather than
    trusted because it once passed.
    """
    store = InMemoryCandidateStore()
    guidance = InMemoryGuidanceStore()
    candidate = await _proposed(store)
    review = CandidateReview(
        store=store, registry=InMemoryMetricRegistry([]), guidance=guidance
    )
    narrowed = [
        TableMetadata(
            schema_name="erp",
            table_name="emp_comp_hist",
            columns=["emp_no"],
            description="compensation history, since reduced",
            column_metadata=[
                ColumnMetadata(name="emp_no", data_type="integer", nullable=False)
            ],
        )
    ]

    with pytest.raises(CandidateReviewError, match="current authorized schema"):
        await review.approve_query_example(
            SOURCE_A,
            candidate.id,
            validator=_validator(),
            authorized_tables=narrowed,
            reviewed_by="reviewer",
        )

    assert await guidance.examples(SOURCE_A) == []
    stored = await store.by_id(SOURCE_A, candidate.id)
    assert stored is not None
    assert stored.status is CandidateStatus.PROPOSED, "a refused approval promoted it"


@pytest.mark.anyio
async def test_a_candidate_without_evidence_cannot_be_approved() -> None:
    store = InMemoryCandidateStore()
    candidate = await _proposed(store, sql=None)
    review = CandidateReview(
        store=store,
        registry=InMemoryMetricRegistry([]),
        guidance=InMemoryGuidanceStore(),
    )

    with pytest.raises(CandidateReviewError, match="no validated execution evidence"):
        await review.approve_query_example(
            SOURCE_A,
            candidate.id,
            validator=_validator(),
            authorized_tables=_erp_tables(),
        )


@pytest.mark.anyio
async def test_a_rejected_candidate_cannot_be_approved_afterwards() -> None:
    store = InMemoryCandidateStore()
    guidance = InMemoryGuidanceStore()
    candidate = await _proposed(store)
    review = CandidateReview(
        store=store, registry=InMemoryMetricRegistry([]), guidance=guidance
    )
    await review.reject(SOURCE_A, candidate.id, reason="Not worth keeping.")

    with pytest.raises(CandidateReviewError, match="rejected"):
        await review.approve_query_example(
            SOURCE_A,
            candidate.id,
            validator=_validator(),
            authorized_tables=_erp_tables(),
        )
    assert await guidance.examples(SOURCE_A) == []


@pytest.mark.anyio
async def test_a_mutating_statement_is_refused_even_as_an_example() -> None:
    """Belt and braces: it should never have become evidence in the first place."""
    store = InMemoryCandidateStore()
    candidate = await _proposed(store, sql="DELETE FROM erp.emp_comp_hist")
    review = CandidateReview(
        store=store,
        registry=InMemoryMetricRegistry([]),
        guidance=InMemoryGuidanceStore(),
    )

    with pytest.raises(CandidateReviewError):
        await review.approve_query_example(
            SOURCE_A,
            candidate.id,
            validator=_validator(),
            authorized_tables=_erp_tables(),
        )


# --- retrieval is datasource-scoped ------------------------------------------


@pytest.mark.anyio
async def test_an_approved_example_reaches_only_its_own_datasource() -> None:
    store = InMemoryCandidateStore()
    guidance = InMemoryGuidanceStore()
    candidate = await _proposed(store)
    await CandidateReview(
        store=store, registry=InMemoryMetricRegistry([]), guidance=guidance
    ).approve_query_example(
        SOURCE_A,
        candidate.id,
        validator=_validator(),
        authorized_tables=_erp_tables(),
    )
    authorized = frozenset({"erp.emp_comp_hist"})

    for_owner = await guidance.relevant_examples(
        SOURCE_A,
        "show current and previous compensation for each employee",
        authorized_tables=authorized,
    )
    for_other = await guidance.relevant_examples(
        SOURCE_B,
        "show current and previous compensation for each employee",
        authorized_tables=authorized,
    )

    assert [item.question for item in for_owner] == [QUESTION]
    assert for_other == [], "an example crossed into another datasource"


@pytest.mark.anyio
async def test_an_example_is_withheld_when_its_tables_are_not_authorized() -> None:
    """Otherwise approved knowledge reveals that a table exists."""
    store = InMemoryCandidateStore()
    guidance = InMemoryGuidanceStore()
    candidate = await _proposed(store)
    await CandidateReview(
        store=store, registry=InMemoryMetricRegistry([]), guidance=guidance
    ).approve_query_example(
        SOURCE_A,
        candidate.id,
        validator=_validator(),
        authorized_tables=_erp_tables(),
    )

    assert (
        await guidance.relevant_examples(
            SOURCE_A,
            "show current and previous compensation for each employee",
            authorized_tables=frozenset({"erp.emp_mst"}),
        )
        == []
    )


# --- the statement is context, never execution -------------------------------


@pytest.mark.anyio
async def test_stored_example_sql_is_never_executed(monkeypatch: Any) -> None:
    """The whole point of storing an example, and the whole risk of it.

    The approved statement reaches the model as context. What runs is whatever
    the model writes next, after SQLGlot and the read-only role -- so a stored
    statement can never become an execution path, however it got there.
    """
    from tests.support.example_execution import run_with_approved_example

    executed, prompt = await run_with_approved_example(
        question="show current and previous compensation for each employee",
        approved_sql=EVIDENCE_SQL,
        fresh_sql="SELECT emp_no FROM erp.emp_comp_hist WHERE curr_flg = 'Y'",
    )

    assert EVIDENCE_SQL in prompt, "the approved example never reached the model"
    assert executed == [
        "SELECT emp_no FROM erp.emp_comp_hist WHERE curr_flg = 'Y' LIMIT 1000"
    ]
    for statement in executed:
        assert EVIDENCE_SQL not in statement, "the stored statement was executed"
