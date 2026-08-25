from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.data.gateway import TableMetadata
from app.governance.gateway import (
    GovernanceColumnMetadata,
    GovernanceFreshness,
    GovernanceGateway,
    GovernanceLineage,
    GovernanceMetadataNotFoundError,
    GovernanceOwner,
    GovernanceProviderUnavailableError,
    GovernanceSnapshot,
    GovernanceTableMetadata,
    GovernanceTag,
    InvalidGovernanceMetadataError,
)


class _EntityReference(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str | None = None
    name: str = "unknown"
    displayName: str | None = None
    type: str | None = None
    fullyQualifiedName: str | None = None


class _TagLabel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    tagFQN: str
    source: str | None = None
    labelType: str | None = None


class _Column(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    description: str | None = None
    tags: tuple[_TagLabel, ...] = ()


class _Table(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    fullyQualifiedName: str
    description: str | None = None
    columns: tuple[_Column, ...] = ()
    owners: tuple[_EntityReference, ...] = ()
    domains: tuple[_EntityReference, ...] = ()
    tags: tuple[_TagLabel, ...] = ()
    updatedAt: int | None = None


class _Lineage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    entity: _EntityReference
    nodes: tuple[_EntityReference, ...] = ()
    upstreamEdges: tuple[dict[str, Any], ...] = ()
    downstreamEdges: tuple[dict[str, Any], ...] = ()


class OpenMetadataGovernanceGateway(GovernanceGateway):
    """Read catalog metadata from current OpenMetadata v1 REST endpoints."""

    def __init__(
        self,
        *,
        api_url: str,
        fqn_prefix: str,
        timeout_seconds: float,
        jwt_token: str | None = None,
        include_lineage: bool = True,
        sensitivity_classifications: tuple[str, ...] = ("PII", "PersonalData", "Sensitive"),
        client: httpx.AsyncClient | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {jwt_token}"} if jwt_token else None
        self._client = client or httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            timeout=timeout_seconds,
            headers=headers,
        )
        self._owns_client = client is None
        self._fqn_prefix = fqn_prefix.strip(".")
        self._include_lineage = include_lineage
        self._sensitivity_prefixes = tuple(
            value.casefold() for value in sensitivity_classifications
        )

    async def get_metadata(self, tables: list[TableMetadata]) -> GovernanceSnapshot:
        started = perf_counter()
        if not tables:
            return GovernanceSnapshot(provider="openmetadata")
        allowed_fqns = {
            self._source_fqn(table): table.identifier for table in tables
        }
        results = await asyncio.gather(
            *(self._get_table(table, allowed_fqns) for table in tables)
        )
        return GovernanceSnapshot(
            provider="openmetadata",
            tables={item.physical_identifier: item for item in results},
            retrieval_latency_ms=round((perf_counter() - started) * 1000, 3),
        )

    async def _get_table(
        self,
        physical: TableMetadata,
        allowed_fqns: dict[str, str],
    ) -> GovernanceTableMetadata:
        source_fqn = self._source_fqn(physical)
        payload = await self._get_json(
            f"/v1/tables/name/{quote(source_fqn, safe='.')}",
            params={"fields": "columns,owners,tags,domains,lifeCycle"},
            missing_is_empty=False,
        )
        try:
            table = _Table.model_validate(payload)
        except ValidationError as exc:
            raise InvalidGovernanceMetadataError(
                "OpenMetadata returned invalid table metadata."
            ) from exc
        allowed_columns = set(physical.columns)
        columns = {
            column.name: self._column_metadata(column)
            for column in table.columns
            if column.name in allowed_columns
        }
        lineage = (
            await self._get_lineage(table.id, allowed_fqns)
            if self._include_lineage
            else GovernanceLineage()
        )
        tags = tuple(self._tag(tag) for tag in table.tags)
        return GovernanceTableMetadata(
            physical_identifier=physical.identifier,
            source_id=table.id,
            source_fqn=table.fullyQualifiedName,
            description=table.description,
            owners=tuple(self._owner(owner) for owner in table.owners),
            domains=tuple(
                sorted(
                    domain.fullyQualifiedName or domain.displayName or domain.name
                    for domain in table.domains
                )
            ),
            tags=tuple(tag for tag in tags if not _is_glossary(tag)),
            glossary_terms=tuple(
                sorted(tag.fully_qualified_name for tag in tags if _is_glossary(tag))
            ),
            sensitivity=self._sensitivity(tags),
            columns=columns,
            lineage=lineage,
            freshness=GovernanceFreshness(
                catalog_updated_at=_millis_to_datetime(table.updatedAt),
            ),
        )

    async def _get_lineage(
        self,
        table_id: str,
        allowed_fqns: dict[str, str],
    ) -> GovernanceLineage:
        payload = await self._get_json(
            f"/v1/lineage/table/{quote(table_id, safe='')}",
            params={"upstreamDepth": 1, "downstreamDepth": 1},
            missing_is_empty=True,
        )
        if not payload:
            return GovernanceLineage()
        try:
            lineage = _Lineage.model_validate(payload)
        except ValidationError as exc:
            raise InvalidGovernanceMetadataError(
                "OpenMetadata returned invalid lineage metadata."
            ) from exc
        nodes_by_id = {
            node.id: allowed_fqns[node.fullyQualifiedName]
            for node in lineage.nodes
            if node.id is not None and node.fullyQualifiedName in allowed_fqns
        }
        return GovernanceLineage(
            upstream=_edge_nodes(lineage.upstreamEdges, "fromEntity", nodes_by_id),
            downstream=_edge_nodes(lineage.downstreamEdges, "toEntity", nodes_by_id),
        )

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any],
        missing_is_empty: bool,
    ) -> dict[str, Any]:
        try:
            response = await self._client.get(path, params=params)
            if response.status_code == 404:
                if missing_is_empty:
                    return {}
                raise GovernanceMetadataNotFoundError(
                    "Required OpenMetadata table metadata was not found."
                )
            response.raise_for_status()
        except GovernanceMetadataNotFoundError:
            raise
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise GovernanceProviderUnavailableError(
                "The configured OpenMetadata service is unavailable."
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise InvalidGovernanceMetadataError(
                "OpenMetadata returned an invalid JSON response."
            ) from exc
        if not isinstance(payload, dict):
            raise InvalidGovernanceMetadataError(
                "OpenMetadata returned an invalid response contract."
            )
        return payload

    def _source_fqn(self, table: TableMetadata) -> str:
        return f"{self._fqn_prefix}.{table.schema_name}.{table.table_name}"

    def _column_metadata(self, column: _Column) -> GovernanceColumnMetadata:
        tags = tuple(self._tag(tag) for tag in column.tags)
        return GovernanceColumnMetadata(
            name=column.name,
            description=column.description,
            tags=tuple(tag for tag in tags if not _is_glossary(tag)),
            glossary_terms=tuple(
                sorted(tag.fully_qualified_name for tag in tags if _is_glossary(tag))
            ),
            sensitivity=self._sensitivity(tags),
        )

    def _sensitivity(self, tags: tuple[GovernanceTag, ...]) -> tuple[str, ...]:
        return tuple(
            sorted(
                tag.fully_qualified_name
                for tag in tags
                if tag.fully_qualified_name.partition(".")[0].casefold()
                in self._sensitivity_prefixes
            )
        )

    @staticmethod
    def _tag(tag: _TagLabel) -> GovernanceTag:
        return GovernanceTag(
            fully_qualified_name=tag.tagFQN,
            source=tag.source,
            label_type=tag.labelType,
        )

    @staticmethod
    def _owner(owner: _EntityReference) -> GovernanceOwner:
        return GovernanceOwner(
            id=owner.id,
            name=owner.name,
            display_name=owner.displayName,
            entity_type=owner.type,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _is_glossary(tag: GovernanceTag) -> bool:
    return (tag.source or "").casefold() == "glossary"


def _millis_to_datetime(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value / 1000, tz=UTC) if value is not None else None


def _edge_nodes(
    edges: tuple[dict[str, Any], ...],
    key: str,
    nodes_by_id: dict[str, str],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                nodes_by_id[node_id]
                for edge in edges
                if isinstance((node_id := edge.get(key)), str) and node_id in nodes_by_id
            }
        )
    )
