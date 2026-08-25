from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.data.gateway import TableMetadata


class GovernanceOwner(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str | None = None
    name: str
    display_name: str | None = None
    entity_type: str | None = None


class GovernanceTag(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fully_qualified_name: str
    source: str | None = None
    label_type: str | None = None


class GovernanceColumnMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str | None = None
    tags: tuple[GovernanceTag, ...] = ()
    glossary_terms: tuple[str, ...] = ()
    sensitivity: tuple[str, ...] = ()


class GovernanceLineage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    upstream: tuple[str, ...] = ()
    downstream: tuple[str, ...] = ()


class GovernanceFreshness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_updated_at: datetime | None = None
    data_freshness_at: datetime | None = None


class GovernanceTableMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    physical_identifier: str
    source_id: str
    source_fqn: str
    description: str | None = None
    owners: tuple[GovernanceOwner, ...] = ()
    domains: tuple[str, ...] = ()
    tags: tuple[GovernanceTag, ...] = ()
    glossary_terms: tuple[str, ...] = ()
    sensitivity: tuple[str, ...] = ()
    columns: dict[str, GovernanceColumnMetadata] = Field(default_factory=dict)
    lineage: GovernanceLineage = Field(default_factory=GovernanceLineage)
    freshness: GovernanceFreshness = Field(default_factory=GovernanceFreshness)


class GovernanceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    tables: dict[str, GovernanceTableMetadata] = Field(default_factory=dict)
    retrieval_latency_ms: float = Field(default=0, ge=0)

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(sorted(table.source_id for table in self.tables.values()))

    @property
    def owner_names(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    owner.display_name or owner.name
                    for table in self.tables.values()
                    for owner in table.owners
                }
            )
        )


class GovernanceGatewayError(RuntimeError):
    """Base error for governance/catalog providers."""


class GovernanceProviderUnavailableError(GovernanceGatewayError):
    """Raised when enabled governance metadata cannot be retrieved."""


class GovernanceMetadataNotFoundError(GovernanceGatewayError):
    """Raised when required governance metadata has not been ingested."""


class InvalidGovernanceMetadataError(GovernanceGatewayError):
    """Raised when a governance provider returns an invalid contract."""


class GovernanceGateway(Protocol):
    async def get_metadata(self, tables: list[TableMetadata]) -> GovernanceSnapshot:
        """Read governance metadata for an already-authorized physical schema snapshot."""

    async def close(self) -> None:
        """Release provider resources."""


def enrich_authorized_schema(
    tables: list[TableMetadata],
    snapshot: GovernanceSnapshot,
) -> list[TableMetadata]:
    enriched: list[TableMetadata] = []
    for table in tables:
        governance = snapshot.tables.get(table.identifier)
        if governance is None:
            enriched.append(table)
            continue
        columns = []
        for column in table.column_metadata:
            governed_column = governance.columns.get(column.name)
            columns.append(
                replace(
                    column,
                    description=(
                        governed_column.description
                        if governed_column is not None and governed_column.description
                        else column.description
                    ),
                )
            )
        enriched.append(
            replace(
                table,
                description=governance.description or table.description,
                column_metadata=columns,
            )
        )
    return enriched


def filter_authorized_governance(
    snapshot: GovernanceSnapshot,
    tables: list[TableMetadata],
) -> GovernanceSnapshot:
    """Reduce provider metadata to the physical scope already authorized by OPA."""
    allowed_columns = {table.identifier: set(table.columns) for table in tables}
    allowed_tables = set(allowed_columns)
    filtered: dict[str, GovernanceTableMetadata] = {}
    for identifier in sorted(allowed_tables):
        table = snapshot.tables.get(identifier)
        if table is None:
            continue
        filtered[identifier] = table.model_copy(
            update={
                "columns": {
                    name: column
                    for name, column in table.columns.items()
                    if name in allowed_columns[identifier]
                },
                "lineage": GovernanceLineage(
                    upstream=tuple(
                        item for item in table.lineage.upstream if item in allowed_tables
                    ),
                    downstream=tuple(
                        item for item in table.lineage.downstream if item in allowed_tables
                    ),
                ),
            }
        )
    return snapshot.model_copy(update={"tables": filtered})
