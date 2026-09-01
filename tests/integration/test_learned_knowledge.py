"""The five new kinds of learned knowledge, and what approval does with them.

Two things are being protected. Nothing a model writes becomes logic: a filter
is structure over confirmed concepts rather than a SQL fragment, and a join rule
names two reviewed attributes rather than a join clause. And approval has an
effect: it writes to the store the runtime reads, rather than flipping a status
on a row nobody consults.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.knowledge.candidates import (
    CandidateReview,
    CandidateReviewError,
    CandidateStatus,
    CandidateType,
    InMemoryCandidateStore,
    KnowledgeCandidate,
)
from app.knowledge.contracts import (
    ApprovalStatus,
    SemanticAttribute,
    SemanticEntity,
)
from app.knowledge.discovery import SemanticModel
from app.knowledge.learned import (
    AttributePredicate,
    DescriptionProposal,
    EntityAliasProposal,
    FilterOperator,
    FilterProposal,
    InMemoryLearnedKnowledgeStore,
    JoinRuleProposal,
    PredicateGroup,
    SynonymProposal,
    describe_predicate,
    predicate_depth,
    referenced_concepts,
)
from app.knowledge.metrics import InMemoryMetricRegistry

SOURCE_A = uuid4()
SOURCE_B = uuid4()
COST_ENTITY = uuid4()
PROJECT_ENTITY = uuid4()
POSTED = uuid4()
REVERSED = uuid4()
COST_PROJECT = uuid4()
PROJECT_KEY = uuid4()


def _model() -> SemanticModel:
    return SemanticModel(
        data_source_id=SOURCE_A,
        schema_fingerprint="fp",
        entities=(
            SemanticEntity(
                id=COST_ENTITY,
                data_source_id=SOURCE_A,
                source_schema="erp",
                source_table="gl_cost_txn",
                entity_name="Cost Transaction",
                status=ApprovalStatus.CONFIRMED,
            ),
            SemanticEntity(
                id=PROJECT_ENTITY,
                data_source_id=SOURCE_A,
                source_schema="erp",
                source_table="prj_hdr",
                entity_name="Project",
                description="A project.",
                status=ApprovalStatus.CONFIRMED,
            ),
        ),
        attributes=(
            SemanticAttribute(
                id=POSTED,
                data_source_id=SOURCE_A,
                entity_id=COST_ENTITY,
                source_column="posted_flg",
                concept_name="Posting Status",
                status=ApprovalStatus.CONFIRMED,
            ),
            SemanticAttribute(
                id=REVERSED,
                data_source_id=SOURCE_A,
                entity_id=COST_ENTITY,
                source_column="reversal_flg",
                concept_name="Reversal Flag",
                status=ApprovalStatus.CONFIRMED,
            ),
            SemanticAttribute(
                id=COST_PROJECT,
                data_source_id=SOURCE_A,
                entity_id=COST_ENTITY,
                source_column="prj_no",
                concept_name="Cost Project Reference",
                status=ApprovalStatus.CONFIRMED,
            ),
            SemanticAttribute(
                id=PROJECT_KEY,
                data_source_id=SOURCE_A,
                entity_id=PROJECT_ENTITY,
                source_column="prj_no",
                concept_name="Project Key",
                is_identifier=True,
                status=ApprovalStatus.CONFIRMED,
            ),
        ),
    )


def _valid_posted_costs() -> PredicateGroup:
    return PredicateGroup(
        operator="AND",
        children=[
            AttributePredicate(
                concept="Posting Status", operator=FilterOperator.EQ, values=["Y"]
            ),
            AttributePredicate(
                concept="Reversal Flag", operator=FilterOperator.EQ, values=["N"]
            ),
        ],
    )


async def _candidate(
    store: InMemoryCandidateStore, candidate_type: CandidateType, proposal: object
) -> KnowledgeCandidate:
    return await store.upsert(
        KnowledgeCandidate(
            data_source_id=SOURCE_A,
            candidate_type=candidate_type,
            display_name=getattr(proposal, "display_name", "proposal"),
            structural_fingerprint=f"fp-{candidate_type.value}",
            proposal=proposal,  # type: ignore[arg-type]
        )
    )


def _review(
    store: InMemoryCandidateStore, learned: InMemoryLearnedKnowledgeStore
) -> CandidateReview:
    return CandidateReview(
        store=store,
        registry=InMemoryMetricRegistry([]),
        learned=learned,
    )


# --- the filter contract -----------------------------------------------------


def test_a_predicate_is_structure_over_concepts_never_sql() -> None:
    predicate = _valid_posted_costs()

    assert referenced_concepts(predicate) == {"Posting Status", "Reversal Flag"}
    assert predicate_depth(predicate) == 2
    assert describe_predicate(predicate) == (
        "(Posting Status is Y AND Reversal Flag is N)"
    )


@pytest.mark.parametrize(
    ("operator", "values"),
    [
        (FilterOperator.EQ, []),
        (FilterOperator.EQ, ["A", "B"]),
        (FilterOperator.IN, []),
        (FilterOperator.IS_NULL, ["A"]),
    ],
)
def test_a_predicate_whose_values_contradict_its_operator_is_refused(
    operator: FilterOperator, values: list[str]
) -> None:
    with pytest.raises(ValidationError):
        AttributePredicate(concept="Posting Status", operator=operator, values=values)


def test_a_group_of_one_is_not_a_group() -> None:
    with pytest.raises(ValidationError):
        PredicateGroup(
            operator="AND",
            children=[
                AttributePredicate(
                    concept="Posting Status", operator=FilterOperator.EQ, values=["Y"]
                )
            ],
        )


# --- promotion ---------------------------------------------------------------


@pytest.mark.anyio
async def test_an_approved_filter_reaches_the_store_the_runtime_reads() -> None:
    store = InMemoryCandidateStore()
    learned = InMemoryLearnedKnowledgeStore()
    candidate = await _candidate(
        store,
        CandidateType.FILTER,
        FilterProposal(
            display_name="Valid posted costs",
            description="Postings that count.",
            predicate=_valid_posted_costs(),
        ),
    )

    approved = await _review(store, learned).approve_learned(
        SOURCE_A, candidate.id, semantic_model=_model(), reviewed_by="reviewer"
    )

    assert approved.status is CandidateStatus.APPROVED
    stored = await learned.filters(SOURCE_A)
    assert [item.name for item in stored] == ["Valid posted costs"]
    assert stored[0].source_candidate_id == candidate.id
    assert stored[0].predicate["operator"] == "AND"


@pytest.mark.anyio
async def test_a_filter_naming_an_unconfirmed_concept_is_refused() -> None:
    """A concept nobody confirmed is a guess with a reviewer's signature on it."""
    store = InMemoryCandidateStore()
    learned = InMemoryLearnedKnowledgeStore()
    candidate = await _candidate(
        store,
        CandidateType.FILTER,
        FilterProposal(
            display_name="Made up",
            predicate=AttributePredicate(
                concept="Invented Concept",
                operator=FilterOperator.EQ,
                values=["Y"],
            ),
        ),
    )

    with pytest.raises(CandidateReviewError, match="Not confirmed"):
        await _review(store, learned).approve_learned(
            SOURCE_A, candidate.id, semantic_model=_model()
        )
    assert await learned.filters(SOURCE_A) == []


@pytest.mark.anyio
async def test_a_synonym_must_point_at_meaning_that_already_exists() -> None:
    store = InMemoryCandidateStore()
    learned = InMemoryLearnedKnowledgeStore()
    good = await _candidate(
        store,
        CandidateType.SYNONYM,
        SynonymProposal(
            display_name="Posting status wording",
            target_kind="concept",
            target="Posting Status",
            phrases=["posted", "has been posted"],
        ),
    )
    bad = await store.upsert(
        KnowledgeCandidate(
            data_source_id=SOURCE_A,
            candidate_type=CandidateType.SYNONYM,
            display_name="Invented",
            structural_fingerprint="fp-synonym-bad",
            proposal=SynonymProposal(
                display_name="Invented",
                target_kind="concept",
                target="Nothing Confirmed",
                phrases=["whatever"],
            ),
        )
    )
    review = _review(store, learned)

    await review.approve_learned(SOURCE_A, good.id, semantic_model=_model())
    with pytest.raises(CandidateReviewError):
        await review.approve_learned(SOURCE_A, bad.id, semantic_model=_model())

    stored = await learned.synonyms(SOURCE_A)
    assert [item.target for item in stored] == ["Posting Status"]
    assert stored[0].phrases == ("posted", "has been posted")


@pytest.mark.anyio
async def test_an_entity_alias_names_the_entity_and_not_a_row_by_default() -> None:
    """Binding a row identity belongs to the live lookup, not to an alias."""
    store = InMemoryCandidateStore()
    learned = InMemoryLearnedKnowledgeStore()
    candidate = await _candidate(
        store,
        CandidateType.ENTITY_ALIAS,
        EntityAliasProposal(
            display_name="Project naming",
            entity_name="Project",
            alias="engagement",
        ),
    )

    await _review(store, learned).approve_learned(
        SOURCE_A, candidate.id, semantic_model=_model()
    )

    stored = await learned.aliases(SOURCE_A)
    assert stored[0].entity_id == PROJECT_ENTITY
    assert stored[0].canonical_key is None


@pytest.mark.anyio
async def test_a_join_rule_names_two_confirmed_attributes() -> None:
    store = InMemoryCandidateStore()
    learned = InMemoryLearnedKnowledgeStore()
    candidate = await _candidate(
        store,
        CandidateType.JOIN_RULE,
        JoinRuleProposal(
            display_name="Costs belong to projects",
            left_concept="Cost Project Reference",
            right_concept="Project Key",
        ),
    )

    await _review(store, learned).approve_learned(
        SOURCE_A, candidate.id, semantic_model=_model()
    )

    stored = await learned.join_rules(SOURCE_A)
    assert stored[0].left_attribute_id == COST_PROJECT
    assert stored[0].right_attribute_id == PROJECT_KEY
    assert stored[0].cardinality == "MANY_TO_ONE"


@pytest.mark.anyio
async def test_a_join_rule_onto_itself_is_refused() -> None:
    store = InMemoryCandidateStore()
    learned = InMemoryLearnedKnowledgeStore()
    candidate = await _candidate(
        store,
        CandidateType.JOIN_RULE,
        JoinRuleProposal(
            display_name="Nonsense",
            left_concept="Project Key",
            right_concept="Project Key",
        ),
    )

    with pytest.raises(CandidateReviewError, match="two different"):
        await _review(store, learned).approve_learned(
            SOURCE_A, candidate.id, semantic_model=_model()
        )


@pytest.mark.anyio
async def test_a_description_change_keeps_what_it_replaced() -> None:
    """An improvement has to stay distinguishable from a mistake."""
    store = InMemoryCandidateStore()
    learned = InMemoryLearnedKnowledgeStore()
    candidate = await _candidate(
        store,
        CandidateType.DESCRIPTION_IMPROVEMENT,
        DescriptionProposal(
            display_name="Clearer project wording",
            subject_kind="entity",
            subject="Project",
            description="A unit of client work with its own budget and invoices.",
        ),
    )

    await _review(store, learned).approve_learned(
        SOURCE_A, candidate.id, semantic_model=_model(), reviewed_by="reviewer"
    )

    stored = await learned.descriptions(SOURCE_A)
    assert stored[0].subject_id == PROJECT_ENTITY
    assert stored[0].previous_description == "A project."
    assert "budget and invoices" in stored[0].description


# --- isolation ---------------------------------------------------------------


@pytest.mark.anyio
async def test_learned_knowledge_never_crosses_datasources() -> None:
    """A synonym learned about one database must not change another."""
    store = InMemoryCandidateStore()
    learned = InMemoryLearnedKnowledgeStore()
    candidate = await _candidate(
        store,
        CandidateType.SYNONYM,
        SynonymProposal(
            display_name="Posting status wording",
            target_kind="concept",
            target="Posting Status",
            phrases=["posted"],
        ),
    )
    await _review(store, learned).approve_learned(
        SOURCE_A, candidate.id, semantic_model=_model()
    )

    assert len(await learned.synonyms(SOURCE_A)) == 1
    assert await learned.synonyms(SOURCE_B) == []
    assert await learned.filters(SOURCE_B) == []
    assert await learned.aliases(SOURCE_B) == []
    assert await learned.join_rules(SOURCE_B) == []


@pytest.mark.anyio
async def test_a_rejected_candidate_cannot_be_promoted_afterwards() -> None:
    store = InMemoryCandidateStore()
    learned = InMemoryLearnedKnowledgeStore()
    candidate = await _candidate(
        store,
        CandidateType.FILTER,
        FilterProposal(
            display_name="Valid posted costs", predicate=_valid_posted_costs()
        ),
    )
    review = _review(store, learned)
    await review.reject(SOURCE_A, candidate.id, reason="Not general enough.")

    with pytest.raises(CandidateReviewError, match="rejected"):
        await review.approve_learned(
            SOURCE_A, candidate.id, semantic_model=_model()
        )
    assert await learned.filters(SOURCE_A) == []


@pytest.mark.anyio
async def test_a_datasource_with_no_confirmed_semantics_promotes_nothing() -> None:
    store = InMemoryCandidateStore()
    learned = InMemoryLearnedKnowledgeStore()
    candidate = await _candidate(
        store,
        CandidateType.FILTER,
        FilterProposal(
            display_name="Valid posted costs", predicate=_valid_posted_costs()
        ),
    )

    with pytest.raises(CandidateReviewError, match="no confirmed semantics"):
        await _review(store, learned).approve_learned(
            SOURCE_A, candidate.id, semantic_model=None
        )
