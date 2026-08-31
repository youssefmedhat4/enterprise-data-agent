"""Datasource onboarding: scan, discover, persist, reconcile.

An admin workflow, never a request path. Scanning reads schema metadata and
calls a model once; doing that per question would be both slow and pointless,
since a schema changes far less often than it is queried.

Rescanning preserves reviewed work. Confirmed mappings whose physical object
still exists keep their status; ones whose table or column disappeared become
STALE so a reviewer can see what broke. Nothing approved is deleted, and
proposals already on record are not duplicated, because a scan that reset review
state would train reviewers to re-approve without reading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.data.gateway import TableMetadata
from app.knowledge.contracts import ApprovalStatus
from app.knowledge.discovery import (
    SemanticDiscoveryService,
    SemanticModel,
    reconcile_with_schema,
)
from app.knowledge.scanner import SchemaScanner, SchemaSnapshot

logger = logging.getLogger(__name__)


class SemanticStore(Protocol):
    """What onboarding needs from semantic persistence."""

    async def load(self, data_source_id: UUID) -> SemanticModel: ...

    async def save(self, model: SemanticModel) -> None: ...


@dataclass(frozen=True, slots=True)
class ScanSummary:
    """What a scan changed. Safe to return: no physical values, no credentials."""

    data_source_id: UUID
    schema_fingerprint: str
    previous_fingerprint: str | None
    table_count: int
    proposed_entities: int
    proposed_attributes: int
    proposed_relationships: int
    confirmed_preserved: int
    marked_stale: int

    @property
    def schema_changed(self) -> bool:
        return self.previous_fingerprint != self.schema_fingerprint


class DataSourceOnboardingService:
    """Scans a datasource and persists the semantic model it implies."""

    def __init__(
        self,
        *,
        discovery: SemanticDiscoveryService,
        semantics: SemanticStore,
        scanner: SchemaScanner | None = None,
    ) -> None:
        self._discovery = discovery
        self._semantics = semantics
        self._scanner = scanner or SchemaScanner()

    async def scan(
        self,
        *,
        data_source_id: UUID,
        tables: list[TableMetadata],
        previous_fingerprint: str | None = None,
    ) -> ScanSummary:
        snapshot = self._scanner.scan(tables)
        existing = await self._load(data_source_id)

        if existing.entities or existing.attributes:
            merged, stale = self._reconcile(existing, snapshot)
        else:
            merged, stale = await self._discover(data_source_id, snapshot), 0

        await self._semantics.save(merged)
        confirmed = len(merged.confirmed_entities()) + len(
            merged.confirmed_attributes()
        )
        logger.info(
            "datasource scanned: id=%s tables=%d stale=%d",
            data_source_id,
            len(snapshot.tables),
            stale,
        )
        return ScanSummary(
            data_source_id=data_source_id,
            schema_fingerprint=snapshot.fingerprint,
            previous_fingerprint=previous_fingerprint,
            table_count=len(snapshot.tables),
            proposed_entities=sum(
                1
                for entity in merged.entities
                if entity.status is ApprovalStatus.PROPOSED
            ),
            proposed_attributes=sum(
                1
                for attribute in merged.attributes
                if attribute.status is ApprovalStatus.PROPOSED
            ),
            proposed_relationships=sum(
                1
                for relationship in merged.relationships
                if relationship.status is ApprovalStatus.PROPOSED
            ),
            confirmed_preserved=confirmed,
            marked_stale=stale,
        )

    async def _discover(
        self, data_source_id: UUID, snapshot: SchemaSnapshot
    ) -> SemanticModel:
        """First scan: propose a model. Everything arrives PROPOSED."""
        return await self._discovery.propose(
            data_source_id=data_source_id, snapshot=snapshot
        )

    def _reconcile(
        self, existing: SemanticModel, snapshot: SchemaSnapshot
    ) -> tuple[SemanticModel, int]:
        """Rescan: keep what still holds, mark what broke.

        Re-running discovery here would replace reviewed mappings with fresh
        proposals and discard the review entirely, so it deliberately does not.
        """
        before = _stale_count(existing)
        reconciled = reconcile_with_schema(existing, snapshot)
        return reconciled, _stale_count(reconciled) - before

    async def _load(self, data_source_id: UUID) -> SemanticModel:
        return await self._semantics.load(data_source_id)


def _stale_count(model: SemanticModel) -> int:
    """How much of this model is invalidated, across all three kinds."""
    stale = 0
    for entity in model.entities:
        stale += entity.status is ApprovalStatus.STALE
    for attribute in model.attributes:
        stale += attribute.status is ApprovalStatus.STALE
    for relationship in model.relationships:
        stale += relationship.status is ApprovalStatus.STALE
    return stale
