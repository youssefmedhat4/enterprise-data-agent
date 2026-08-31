"""Dependency-aware staleness when a schema changes.

The requirement that matters is the negative one: an unrelated table changing
must not invalidate reviewed knowledge. Marking everything stale on any change
would destroy human review work and train reviewers to re-approve without
reading, which is worse than not tracking staleness at all.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.data.gateway import ColumnMetadata, TableMetadata
from app.knowledge.contracts import ApprovalStatus
from app.knowledge.discovery import (
    AttributeProposal,
    EntityProposal,
    SemanticProposals,
    SemanticReview,
    build_semantic_model,
    reconcile_with_schema,
)
from app.knowledge.guidance import (
    ApprovedQueryExample,
    BusinessInstruction,
    InMemoryGuidanceStore,
)
from app.knowledge.scanner import SchemaScanner

SOURCE = uuid4()


def column(name: str, data_type: str = "VARCHAR") -> ColumnMetadata:
    return ColumnMetadata(
        name=name, data_type=data_type, nullable=False, description=""
    )


def table(name: str, columns: list[ColumnMetadata]) -> TableMetadata:
    return TableMetadata(
        schema_name="analytics",
        table_name=name,
        columns=[c.name for c in columns],
        description="",
        column_metadata=columns,
    )


EMPLOYEES = table("employees", [column("id"), column("salary", "NUMERIC")])
DEPARTMENTS = table("departments", [column("id"), column("name")])
#: Deliberately unrelated to anything reviewed below.
AUDIT_LOG = table("audit_log", [column("id"), column("event")])


def confirmed_model(tables: list[TableMetadata]) -> object:
    snapshot = SchemaScanner().scan(tables)
    model = build_semantic_model(
        data_source_id=SOURCE,
        snapshot=snapshot,
        proposals=SemanticProposals(
            entities=[
                EntityProposal(
                    table_identifier="analytics.employees",
                    entity_name="Employee",
                    confidence=0.95,
                ),
                EntityProposal(
                    table_identifier="analytics.departments",
                    entity_name="Department",
                    confidence=0.95,
                ),
            ],
            attributes=[
                AttributeProposal(
                    table_identifier="analytics.employees",
                    column_name="salary",
                    concept_name="Annual Base Salary",
                    confidence=0.9,
                ),
                AttributeProposal(
                    table_identifier="analytics.departments",
                    column_name="name",
                    concept_name="Department Name",
                    confidence=0.9,
                ),
            ],
        ),
    )
    review = SemanticReview()
    for entity in model.entities:
        model = review.approve_entity(model, entity.id)
    for attribute in model.attributes:
        model = review.approve_attribute(model, attribute.id)
    return model


def statuses(model: object) -> dict[str, ApprovalStatus]:
    return {
        f"{entity.source_table}": entity.status
        for entity in model.entities  # type: ignore[attr-defined]
    }


def attribute_statuses(model: object) -> dict[str, ApprovalStatus]:
    return {
        attribute.source_column: attribute.status
        for attribute in model.attributes  # type: ignore[attr-defined]
    }


def test_an_unrelated_new_table_leaves_confirmed_semantics_alone() -> None:
    model = confirmed_model([EMPLOYEES, DEPARTMENTS])

    rescanned = SchemaScanner().scan([EMPLOYEES, DEPARTMENTS, AUDIT_LOG])
    reconciled = reconcile_with_schema(model, rescanned)  # type: ignore[arg-type]

    assert set(statuses(reconciled).values()) == {ApprovalStatus.CONFIRMED}
    assert set(attribute_statuses(reconciled).values()) == {ApprovalStatus.CONFIRMED}


def test_only_the_mapping_whose_column_disappeared_becomes_stale() -> None:
    model = confirmed_model([EMPLOYEES, DEPARTMENTS])

    # `salary` is dropped; everything else is untouched.
    rescanned = SchemaScanner().scan(
        [table("employees", [column("id")]), DEPARTMENTS]
    )
    reconciled = reconcile_with_schema(model, rescanned)  # type: ignore[arg-type]

    attributes = attribute_statuses(reconciled)
    assert attributes["salary"] is ApprovalStatus.STALE
    assert attributes["name"] is ApprovalStatus.CONFIRMED, (
        "an unrelated confirmed mapping was invalidated"
    )


def test_a_removed_table_makes_its_entity_stale_but_not_its_neighbour() -> None:
    model = confirmed_model([EMPLOYEES, DEPARTMENTS])

    rescanned = SchemaScanner().scan([DEPARTMENTS])
    reconciled = reconcile_with_schema(model, rescanned)  # type: ignore[arg-type]

    entities = statuses(reconciled)
    assert entities["employees"] is ApprovalStatus.STALE
    assert entities["departments"] is ApprovalStatus.CONFIRMED


def test_stale_knowledge_is_preserved_rather_than_deleted() -> None:
    """A reviewer has to be able to see what broke and re-map it."""
    model = confirmed_model([EMPLOYEES, DEPARTMENTS])

    rescanned = SchemaScanner().scan([DEPARTMENTS])
    reconciled = reconcile_with_schema(model, rescanned)  # type: ignore[arg-type]

    assert len(reconciled.entities) == len(model.entities)  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_only_examples_bound_to_the_old_schema_go_stale() -> None:
    store = InMemoryGuidanceStore()
    await store.approve_example(
        ApprovedQueryExample(
            data_source_id=SOURCE,
            question="payroll by department",
            query_pattern="SELECT 1 FROM analytics.employees",
            schema_fingerprint="fp-old",
        ),
        was_successful=True,
        was_validated=True,
    )
    # No fingerprint recorded: not bound to a schema version, so not stale.
    await store.approve_example(
        ApprovedQueryExample(
            data_source_id=SOURCE,
            question="departments list",
            query_pattern="SELECT 1 FROM analytics.departments",
        ),
        was_successful=True,
        was_validated=True,
    )

    marked = await store.mark_stale_for_schema(SOURCE, new_schema_fingerprint="fp-new")

    assert marked == 1
    by_question = {
        example.question: example.status for example in await store.examples(SOURCE)
    }
    assert by_question["payroll by department"] is ApprovalStatus.STALE
    assert by_question["departments list"] is ApprovalStatus.CONFIRMED


@pytest.mark.anyio
async def test_a_business_definition_survives_an_unrelated_schema_change() -> None:
    """What a figure means usually outlives the table layout that produced it."""
    store = InMemoryGuidanceStore()
    await store.approve_instruction(
        BusinessInstruction(
            data_source_id=SOURCE,
            title="Payroll roster scope",
            instruction="Annual base payroll includes all roster employees.",
            semantic_concepts=("payroll",),
        )
    )

    await store.mark_stale_for_schema(SOURCE, new_schema_fingerprint="fp-new")

    instructions = await store.instructions(SOURCE)
    assert instructions[0].status is ApprovalStatus.CONFIRMED
    assert await store.relevant_instructions(SOURCE, "what is our payroll commitment?")


@pytest.mark.anyio
async def test_a_stale_example_is_withheld_from_reasoning() -> None:
    store = InMemoryGuidanceStore()
    await store.approve_example(
        ApprovedQueryExample(
            data_source_id=SOURCE,
            question="payroll by department",
            query_pattern="SELECT 1 FROM analytics.employees",
            schema_fingerprint="fp-old",
        ),
        was_successful=True,
        was_validated=True,
    )

    await store.mark_stale_for_schema(SOURCE, new_schema_fingerprint="fp-new")

    assert await store.relevant_examples(SOURCE, "payroll by department") == []
