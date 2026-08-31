"""Onboarding a second datasource end to end, against real PostgreSQL.

register -> scan -> persisted proposals -> approve -> a *new* EntityResolver
resolves through the persisted mapping.

The last step is the point: the resolver is constructed after the model has been
reloaded from the database, so nothing is handed between them in memory. If
review state and runtime state could drift, this fails.
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.data.gateway import ColumnMetadata, TableMetadata
from app.knowledge.contracts import ApprovalStatus
from app.knowledge.datasources import (
    DataSourceConnectionResolver,
    DataSourceError,
    PostgresDataSourceRegistry,
)
from app.knowledge.discovery import (
    AttributeProposal,
    EntityProposal,
    SemanticProposals,
    SemanticReview,
    build_semantic_model,
)
from app.knowledge.migrations import apply_migrations
from app.knowledge.onboarding import DataSourceOnboardingService
from app.knowledge.postgres_semantics import PostgresSemanticRepository
from app.knowledge.scanner import SchemaSnapshot
from app.semantic.entities import EntityResolver

pytestmark = pytest.mark.postgres


def column(
    name: str, *, values: tuple[str, ...] = (), primary_key: bool = False
) -> ColumnMetadata:
    return ColumnMetadata(
        name=name,
        data_type="VARCHAR",
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
        description="",
        column_metadata=columns,
        primary_key=tuple(c.name for c in columns if c.primary_key),
    )


#: Datasource B: nothing here is named like the concepts it holds.
TABLES_B = [
    table(
        "staff",
        [
            column("staff_id", primary_key=True),
            column("unit_id"),
            column("annual_compensation"),
        ],
    ),
    table(
        "business_units",
        [
            column("unit_id", primary_key=True, values=("BU-1", "BU-2")),
            column("unit_name", values=("Engineering", "Revenue Ops")),
        ],
    ),
    table("engagements", [column("engagement_id", primary_key=True)]),
    table("billing_documents", [column("document_id", primary_key=True)]),
]


class FakeDiscovery:
    """Deterministic stand-in for the model, proposing the right meanings."""

    def __init__(self) -> None:
        self.calls = 0

    async def propose(
        self, *, data_source_id: UUID, snapshot: SchemaSnapshot
    ) -> Any:
        self.calls += 1
        return build_semantic_model(
            data_source_id=data_source_id,
            snapshot=snapshot,
            proposals=SemanticProposals(
                entities=[
                    EntityProposal(
                        table_identifier="analytics.staff",
                        entity_name="Employee",
                        confidence=0.97,
                    ),
                    EntityProposal(
                        table_identifier="analytics.business_units",
                        entity_name="Organizational Unit",
                        confidence=0.95,
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
                        confidence=0.94,
                    ),
                    AttributeProposal(
                        table_identifier="analytics.staff",
                        column_name="annual_compensation",
                        concept_name="Annual Base Salary",
                        confidence=0.93,
                    ),
                ],
            ),
        )


def test_onboarding_persists_semantics_that_drive_resolution() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_selector_loop) as runner:
            runner.run(_exercise())
        return
    asyncio.run(_exercise())


def _selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def _exercise() -> None:
    settings = Settings()
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    dsn = settings.checkpoint_database_url.get_secret_value()

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("DROP SCHEMA IF EXISTS knowledge CASCADE")
        await apply_migrations(conn)

    async with AsyncConnectionPool(dsn, min_size=1, max_size=3, open=False) as pool:
        await pool.open(wait=True)
        sources = PostgresDataSourceRegistry(pool)

        # 1. Register using a reference, never a credential.
        registered = await sources.register(
            name="EU Warehouse",
            database_type="postgres",
            connection_ref="DATABASE_URL",
        )
        assert registered.connection_ref == "DATABASE_URL"
        assert "://" not in registered.connection_ref

        # A pasted DSN is refused by the contract before it reaches the database.
        with pytest.raises(ValueError):
            await sources.register(
                name="Bad",
                database_type="postgres",
                connection_ref="postgresql://user:secret@host/db",
            )
        # An unlisted reference cannot be resolved.
        resolver = DataSourceConnectionResolver(settings)
        with pytest.raises(DataSourceError):
            resolver.resolve("SOME_OTHER_SECRET")

        # 2. Scan: proposals are persisted, all PROPOSED.
        semantics = PostgresSemanticRepository(pool)
        discovery = FakeDiscovery()
        service = DataSourceOnboardingService(
            discovery=discovery,  # type: ignore[arg-type]
            semantics=semantics,
        )
        summary = await service.scan(
            data_source_id=registered.id, tables=TABLES_B
        )
        await sources.record_scan(
            registered.id, schema_fingerprint=summary.schema_fingerprint
        )

        assert summary.table_count == 4
        assert summary.proposed_entities == 2
        assert summary.proposed_attributes == 3

        stored = await semantics.load(registered.id)
        assert {e.entity_name for e in stored.entities} == {
            "Employee",
            "Organizational Unit",
        }
        assert all(e.status is ApprovalStatus.PROPOSED for e in stored.entities)
        assert stored.confirmed_entities() == (), "nothing is truth before review"

        # 3. Approve through the review lifecycle, and persist.
        review = SemanticReview()
        approved = stored
        for entity in approved.entities:
            approved = review.approve_entity(approved, entity.id)
        for attribute in approved.attributes:
            approved = review.approve_attribute(approved, attribute.id)
        await semantics.save(approved)

    # ---- fresh pool, fresh repository, fresh resolver ----
    async with AsyncConnectionPool(dsn, min_size=1, max_size=2, open=False) as pool:
        await pool.open(wait=True)
        reloaded = await PostgresSemanticRepository(pool).load(registered.id)
        assert len(reloaded.confirmed_entities()) == 2

        resolution = EntityResolver().resolve(
            user_text="what is the margin for Engineering",
            authorized_tables=TABLES_B,
            concept="Organizational Unit",
            semantic_model=reloaded,
        )
        match = resolution.resolved
        assert match is not None
        assert match.value == "Engineering"
        assert match.qualified_column == "analytics.business_units.unit_name"
        assert match.canonical_column == "analytics.business_units.unit_id"

        # The same concept is unreachable without the persisted mapping.
        assert EntityResolver().resolve(
            user_text="what is the margin for Engineering",
            authorized_tables=TABLES_B,
            concept="Organizational Unit",
        ).is_unresolved


def test_rescan_preserves_confirmations_and_marks_only_what_broke() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_selector_loop) as runner:
            runner.run(_exercise_rescan())
        return
    asyncio.run(_exercise_rescan())


async def _exercise_rescan() -> None:
    settings = Settings()
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    dsn = settings.checkpoint_database_url.get_secret_value()

    async with AsyncConnectionPool(dsn, min_size=1, max_size=2, open=False) as pool:
        await pool.open(wait=True)
        sources = PostgresDataSourceRegistry(pool)
        registered = await sources.register(
            name="Rescan Target",
            database_type="postgres",
            connection_ref="DATABASE_URL",
        )
        semantics = PostgresSemanticRepository(pool)
        service = DataSourceOnboardingService(
            discovery=FakeDiscovery(),  # type: ignore[arg-type]
            semantics=semantics,
        )
        await service.scan(data_source_id=registered.id, tables=TABLES_B)

        model = await semantics.load(registered.id)
        review = SemanticReview()
        for entity in model.entities:
            model = review.approve_entity(model, entity.id)
        for attribute in model.attributes:
            model = review.approve_attribute(model, attribute.id)
        await semantics.save(model)

        # `staff` disappears; business_units is untouched.
        reduced = [t for t in TABLES_B if t.table_name != "staff"]
        summary = await service.scan(
            data_source_id=registered.id, tables=reduced
        )

        after = await semantics.load(registered.id)
        by_table = {e.source_table: e.status for e in after.entities}
        assert by_table["staff"] is ApprovalStatus.STALE
        assert by_table["business_units"] is ApprovalStatus.CONFIRMED, (
            "an unrelated confirmed mapping was invalidated"
        )
        assert summary.marked_stale >= 1
        # Nothing approved was deleted; a reviewer can still see what broke.
        assert len(after.entities) == len(model.entities)
