"""Portability across two deliberately different schemas.

Datasource A is named the way the demo is. Datasource B shares none of those
names: `staff`, `business_units`, `engagements`, `billing_documents`. Nothing
in B's physical naming resembles the business language a user would use, so
only confirmed semantic mappings can connect the two.

The point is that the same human wording produces datasource-specific results,
and that neither database's knowledge is visible to the other.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.data.gateway import ColumnMetadata, TableMetadata
from app.embeddings.fake import HashingEmbeddingGateway
from app.knowledge.discovery import (
    AttributeProposal,
    EntityProposal,
    RelationshipProposal,
    SemanticProposals,
    SemanticReview,
    build_semantic_model,
)
from app.knowledge.memory import InMemoryQuestionMemory, QuestionEvent
from app.knowledge.metrics import InMemoryMetricRegistry
from app.knowledge.retrieval import MetricRetriever
from app.knowledge.scanner import SchemaScanner
from app.knowledge.seed import registered_metrics_for_default_datasource
from app.semantic.entities import EntityResolver, confirmed_bindings

SOURCE_A = uuid4()
SOURCE_B = uuid4()

COMPENSATION_QUESTION = (
    "How much money does the organization commit to employee base "
    "compensation each year?"
)


def column(
    name: str,
    *,
    data_type: str = "VARCHAR",
    values: tuple[str, ...] = (),
    primary_key: bool = False,
) -> ColumnMetadata:
    return ColumnMetadata(
        name=name,
        data_type=data_type,
        nullable=False,
        description="",
        primary_key=primary_key,
        observed_values=values,
        observed_values_source="fixture" if values else None,
    )


def table(name: str, columns: list[ColumnMetadata]) -> TableMetadata:
    return TableMetadata(
        schema_name="analytics",
        table_name=name,
        columns=[c.name for c in columns],
        description=f"{name} fixture",
        column_metadata=columns,
        primary_key=tuple(c.name for c in columns if c.primary_key),
    )


# Datasource B: nothing here is named like the business concepts it holds.
STAFF = table(
    "staff",
    [
        column("staff_id", primary_key=True),
        column("full_name"),
        column("unit_id"),
        column("annual_compensation", data_type="NUMERIC"),
        column("employment_state", values=("active", "terminated")),
    ],
)
BUSINESS_UNITS = table(
    "business_units",
    [
        column("unit_id", primary_key=True, values=("BU-1", "BU-2", "BU-3")),
        column("unit_name", values=("Platform", "Revenue Ops", "People")),
    ],
)
ENGAGEMENTS = table(
    "engagements",
    [column("engagement_id", primary_key=True), column("unit_id"), column("title")],
)
BILLING_DOCUMENTS = table(
    "billing_documents",
    [
        column("document_id", primary_key=True),
        column("engagement_id"),
        column("amount", data_type="NUMERIC"),
    ],
)

DATASOURCE_B_TABLES = [STAFF, BUSINESS_UNITS, ENGAGEMENTS, BILLING_DOCUMENTS]


def proposals_for_b() -> SemanticProposals:
    """What semantic discovery would propose for B, as fixed fixtures.

    Written as fixtures rather than generated so the test asserts on the review
    and resolution machinery deterministically, without a model call.
    """
    return SemanticProposals(
        entities=[
            EntityProposal(
                table_identifier="analytics.staff",
                entity_name="Employee",
                description="A person on the roster.",
                confidence=0.97,
            ),
            EntityProposal(
                table_identifier="analytics.business_units",
                entity_name="Organizational Unit",
                description="A division of the company.",
                confidence=0.94,
            ),
        ],
        attributes=[
            AttributeProposal(
                table_identifier="analytics.business_units",
                column_name="unit_id",
                concept_name="Organizational Unit Key",
                is_identifier=True,
                confidence=0.96,
            ),
            AttributeProposal(
                table_identifier="analytics.business_units",
                column_name="unit_name",
                concept_name="Organizational Unit Name",
                confidence=0.95,
            ),
            AttributeProposal(
                table_identifier="analytics.staff",
                column_name="annual_compensation",
                concept_name="Annual Base Salary",
                confidence=0.93,
            ),
            AttributeProposal(
                table_identifier="analytics.staff",
                column_name="employment_state",
                concept_name="Employment Status",
                confidence=0.9,
            ),
        ],
        relationships=[
            RelationshipProposal(
                from_table="analytics.staff",
                from_column="unit_id",
                to_table="analytics.business_units",
                to_column="unit_id",
                relationship_name="Employee belongs to Organizational Unit",
                cardinality="many_to_one",
                confidence=0.92,
            )
        ],
    )


def confirmed_model_for_b() -> object:
    snapshot = SchemaScanner().scan(DATASOURCE_B_TABLES)
    model = build_semantic_model(
        data_source_id=SOURCE_B, snapshot=snapshot, proposals=proposals_for_b()
    )
    review = SemanticReview()
    for entity in model.entities:
        model = review.approve_entity(model, entity.id)
    for attribute in model.attributes:
        model = review.approve_attribute(model, attribute.id)
    for relationship in model.relationships:
        model = review.approve_relationship(model, relationship.id)
    return model


# --- Discovery and review -------------------------------------------------


def test_discovery_maps_unfamiliar_names_to_business_concepts() -> None:
    snapshot = SchemaScanner().scan(DATASOURCE_B_TABLES)
    model = build_semantic_model(
        data_source_id=SOURCE_B, snapshot=snapshot, proposals=proposals_for_b()
    )

    names = {entity.source_table: entity.entity_name for entity in model.entities}
    assert names["staff"] == "Employee"
    assert names["business_units"] == "Organizational Unit"
    # Nothing is truth until reviewed.
    assert all(entity.status.value == "PROPOSED" for entity in model.entities)


def test_only_confirmed_mappings_become_runtime_truth() -> None:
    model = confirmed_model_for_b()

    bindings = confirmed_bindings(model, "Organizational Unit")  # type: ignore[arg-type]
    columns = {binding.qualified_column for binding in bindings}

    assert "analytics.business_units.unit_name" in columns
    assert all(
        binding.canonical_column == "analytics.business_units.unit_id"
        for binding in bindings
    )


# --- Entity resolution ----------------------------------------------------


def test_entity_resolution_follows_the_confirmed_mapping_not_the_name() -> None:
    model = confirmed_model_for_b()

    resolution = EntityResolver().resolve(
        user_text="what is the margin for Platform",
        authorized_tables=DATASOURCE_B_TABLES,
        concept="Organizational Unit",
        semantic_model=model,  # type: ignore[arg-type]
    )

    match = resolution.resolved
    assert match is not None
    assert match.value == "Platform"
    assert match.qualified_column == "analytics.business_units.unit_name"
    assert match.canonical_column == "analytics.business_units.unit_id"


def test_the_same_concept_is_unreachable_without_the_confirmed_mapping() -> None:
    """Contrast: no mapping, and B's naming gives nothing to guess from."""
    resolution = EntityResolver().resolve(
        user_text="what is the margin for Platform",
        authorized_tables=DATASOURCE_B_TABLES,
        concept="Organizational Unit",
    )

    assert resolution.is_unresolved


# --- Retrieval and memory isolation --------------------------------------


@pytest.mark.anyio
async def test_the_same_wording_retrieves_only_its_own_datasources_metrics() -> None:
    registry_a = InMemoryMetricRegistry(
        registered_metrics_for_default_datasource(SOURCE_A)
    )
    retriever = MetricRetriever(HashingEmbeddingGateway())
    await retriever.index(SOURCE_A, await registry_a.certified(SOURCE_A))

    from_a = await retriever.retrieve(
        data_source_id=SOURCE_A,
        question=COMPENSATION_QUESTION,
        authorized_metrics=await registry_a.certified(SOURCE_A),
        limit=3,
    )
    assert from_a and from_a[0].metric_key == "annual_base_payroll"

    # B was never indexed. The identical question must retrieve nothing.
    from_b = await retriever.retrieve(
        data_source_id=SOURCE_B,
        question=COMPENSATION_QUESTION,
        authorized_metrics=await registry_a.certified(SOURCE_A),
        limit=3,
    )
    assert from_b == []


@pytest.mark.anyio
async def test_question_memory_does_not_leak_between_the_two_databases() -> None:
    memory = InMemoryQuestionMemory()
    fingerprint = "v1|route=governed|metrics=annual_base_payroll|dimensions=department"

    for source in (SOURCE_A, SOURCE_B):
        for _ in range(3):
            await memory.record(
                QuestionEvent(
                    data_source_id=source,
                    question_text=COMPENSATION_QUESTION,
                    structural_fingerprint=fingerprint,
                    route="governed_metric",
                    success=True,
                    validated=True,
                    grounded=True,
                )
            )

    clusters_a = await memory.clusters(SOURCE_A)
    clusters_b = await memory.clusters(SOURCE_B)

    assert len(clusters_a) == 1
    assert len(clusters_b) == 1
    assert clusters_a[0].id != clusters_b[0].id
    # Identical wording and identical structure, still counted separately.
    assert clusters_a[0].occurrence_count == 3
    assert clusters_b[0].occurrence_count == 3
