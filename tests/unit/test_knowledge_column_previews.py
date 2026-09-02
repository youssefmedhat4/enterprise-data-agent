from uuid import uuid4

from app.api.knowledge_routes import _previewable_columns
from app.data.gateway import ColumnMetadata, TableMetadata
from app.governance.gateway import (
    GovernanceColumnMetadata,
    GovernanceSnapshot,
    GovernanceTableMetadata,
)
from app.knowledge.contracts import (
    ApprovalStatus,
    SemanticAttribute,
    SemanticEntity,
)
from app.knowledge.discovery import SemanticModel


def test_previews_include_only_proposed_authorized_non_sensitive_columns() -> None:
    source_id = uuid4()
    entity_id = uuid4()
    entity = SemanticEntity(
        id=entity_id,
        data_source_id=source_id,
        source_schema="analytics",
        source_table="employees",
        entity_name="Employee",
    )
    attributes = tuple(
        SemanticAttribute(
            id=uuid4(),
            data_source_id=source_id,
            entity_id=entity_id,
            source_column=column,
            concept_name=concept,
            status=status,
        )
        for column, concept, status in (
            ("arabic_name", "Arabic Name", ApprovalStatus.PROPOSED),
            ("salary", "Annual Salary", ApprovalStatus.PROPOSED),
            ("private_note", "Private Note", ApprovalStatus.PROPOSED),
            ("status", "Employment Status", ApprovalStatus.CONFIRMED),
        )
    )
    model = SemanticModel(
        data_source_id=source_id,
        schema_fingerprint="fixture",
        entities=(entity,),
        attributes=attributes,
    )
    authorized = [
        TableMetadata(
            schema_name="analytics",
            table_name="employees",
            columns=["arabic_name", "salary", "status"],
            description="Employees",
            column_metadata=[
                ColumnMetadata(name=name, data_type="text", nullable=True)
                for name in ("arabic_name", "salary", "status")
            ],
        )
    ]
    governance = GovernanceSnapshot(
        provider="fixture",
        tables={
            "analytics.employees": GovernanceTableMetadata(
                physical_identifier="analytics.employees",
                source_id="employees",
                source_fqn="company.analytics.employees",
                columns={
                    "salary": GovernanceColumnMetadata(
                        name="salary",
                        sensitivity=("Sensitive",),
                    )
                },
            )
        },
    )

    assert _previewable_columns(model, authorized, governance) == {
        "analytics.employees.arabic_name"
    }
