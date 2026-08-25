from datetime import UTC, datetime

import pytest

from app.data.gateway import ColumnMetadata, TableMetadata
from app.governance.disabled import DisabledGovernanceGateway
from app.governance.gateway import (
    GovernanceColumnMetadata,
    GovernanceFreshness,
    GovernanceLineage,
    GovernanceSnapshot,
    GovernanceTableMetadata,
    enrich_authorized_schema,
    filter_authorized_governance,
)


def _employees() -> TableMetadata:
    return TableMetadata(
        schema_name="analytics",
        table_name="employees",
        columns=["id", "full_name"],
        description="Physical description",
        column_metadata=[
            ColumnMetadata("id", "integer", False, "Physical ID", primary_key=True),
            ColumnMetadata("full_name", "text", False, "Physical name"),
        ],
        primary_key=("id",),
    )


@pytest.mark.asyncio
async def test_disabled_governance_preserves_existing_schema() -> None:
    tables = [_employees()]
    snapshot = await DisabledGovernanceGateway().get_metadata(tables)

    assert snapshot.provider == "disabled"
    assert snapshot.tables == {}
    assert enrich_authorized_schema(tables, snapshot) == tables


def test_governance_enrichment_changes_descriptions_not_physical_schema() -> None:
    physical = _employees()
    snapshot = GovernanceSnapshot(
        provider="openmetadata",
        tables={
            physical.identifier: GovernanceTableMetadata(
                physical_identifier=physical.identifier,
                source_id="om-employees",
                source_fqn="service.database.analytics.employees",
                description="Governed employee directory",
                columns={
                    "full_name": GovernanceColumnMetadata(
                        name="full_name",
                        description="Governed display name",
                    )
                },
                freshness=GovernanceFreshness(
                    catalog_updated_at=datetime(2026, 1, 1, tzinfo=UTC)
                ),
            )
        },
    )

    enriched = enrich_authorized_schema([physical], snapshot)[0]

    assert enriched.description == "Governed employee directory"
    assert enriched.columns == physical.columns
    assert enriched.primary_key == physical.primary_key
    assert enriched.column_metadata[0] == physical.column_metadata[0]
    assert enriched.column_metadata[1].description == "Governed display name"


def test_provider_metadata_is_reduced_to_authorized_tables_columns_and_lineage() -> None:
    physical = _employees()
    snapshot = GovernanceSnapshot(
        provider="openmetadata",
        tables={
            physical.identifier: GovernanceTableMetadata(
                physical_identifier=physical.identifier,
                source_id="om-employees",
                source_fqn="service.database.analytics.employees",
                columns={
                    "full_name": GovernanceColumnMetadata(name="full_name"),
                    "salary": GovernanceColumnMetadata(
                        name="salary",
                        description="Restricted compensation metadata",
                    ),
                },
                lineage=GovernanceLineage(
                    upstream=("analytics.departments",),
                    downstream=("analytics.secret_payroll",),
                ),
            ),
            "analytics.secret_payroll": GovernanceTableMetadata(
                physical_identifier="analytics.secret_payroll",
                source_id="om-secret",
                source_fqn="service.database.analytics.secret_payroll",
            ),
        },
    )

    filtered = filter_authorized_governance(snapshot, [physical])

    assert set(filtered.tables) == {"analytics.employees"}
    assert set(filtered.tables[physical.identifier].columns) == {"full_name"}
    assert filtered.tables[physical.identifier].lineage == GovernanceLineage()
    assert "salary" not in filtered.model_dump_json()
    assert "secret_payroll" not in filtered.model_dump_json()
