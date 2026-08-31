"""PostgresMetricRegistry against a real pgvector PostgreSQL.

Marked `postgres`, so it runs only under --run-postgres. A skipped test is not
verification, and this file exists because an earlier migration was committed
as "verified" while every one of its tests was silently skipping.
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from typing import Any, cast
from uuid import UUID

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.knowledge.metrics import MetricStatus
from app.knowledge.migrations import apply_migrations
from app.knowledge.postgres_metrics import MetricRegistryError, PostgresMetricRegistry
from app.knowledge.seed import registered_metrics_for_default_datasource
from tests.support.knowledge_database import ensure_test_database

pytestmark = pytest.mark.postgres


def test_postgres_metric_registry() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_windows_selector_loop) as runner:
            runner.run(_exercise_registry())
        return
    asyncio.run(_exercise_registry())


def _windows_selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def _insert_data_source(
    conn: psycopg.AsyncConnection[Any], name: str
) -> UUID:
    async with conn.cursor() as cursor:
        await cursor.execute(
            "INSERT INTO knowledge.data_sources"
            " (name, database_type, connection_ref) "
            "VALUES (%s, 'postgres', 'DATABASE_URL') RETURNING id",
            (name,),
        )
        row = cast("tuple[Any, ...] | None", await cursor.fetchone())
    assert row is not None
    return cast(UUID, row[0])


async def _exercise_registry() -> None:
    settings = Settings()
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    dsn = await ensure_test_database()

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("DROP SCHEMA IF EXISTS knowledge CASCADE")
        applied = await apply_migrations(conn)
        assert applied, "expected migrations to apply to a clean database"

        # Idempotence: a second run must apply nothing and must not error.
        assert await apply_migrations(conn) == []

        source_a = await _insert_data_source(conn, "registry-source-a")
        source_b = await _insert_data_source(conn, "registry-source-b")

    async with AsyncConnectionPool(dsn, min_size=1, max_size=2, open=False) as pool:
        await pool.open(wait=True)
        registry = PostgresMetricRegistry(pool)
        await _assert_seeding_and_isolation(registry, source_a, source_b)
        await _assert_status_gates_governed_visibility(registry, source_a)
        await _assert_unknown_metric_update_is_refused(registry, source_a)


async def _assert_seeding_and_isolation(
    registry: PostgresMetricRegistry, source_a: UUID, source_b: UUID
) -> None:
    seeded = registered_metrics_for_default_datasource(source_a)
    for metric in seeded:
        await registry.upsert(metric)

    certified = await registry.certified(source_a)
    keys = {metric.metric_key for metric in certified}
    # The demo catalog must survive the move to persistent storage.
    for expected in ("active_headcount", "annual_base_payroll", "project_margin"):
        assert expected in keys, f"{expected} was not seeded as CERTIFIED"
    assert all(m.status is MetricStatus.CERTIFIED for m in certified)

    # Round-trip fidelity: children must survive storage, or a governed query
    # would silently lose the dimensions it is allowed to group by.
    payroll = await registry.get(source_a, "annual_base_payroll")
    assert payroll is not None
    assert payroll.dimensions, "dimensions were lost in storage"
    assert payroll.business_meaning, "business meaning was lost in storage"
    assert {d.dimension_key for d in payroll.dimensions} >= {"department"}

    # Datasource B was never seeded and must see nothing.
    assert await registry.certified(source_b) == []
    assert await registry.get(source_b, "annual_base_payroll") is None


async def _assert_status_gates_governed_visibility(
    registry: PostgresMetricRegistry, source_a: UUID
) -> None:
    for excluded in (
        MetricStatus.PROPOSED,
        MetricStatus.REJECTED,
        MetricStatus.DEPRECATED,
        MetricStatus.STALE,
    ):
        await registry.set_status(source_a, "project_margin", excluded)
        keys = {m.metric_key for m in await registry.certified(source_a)}
        assert "project_margin" not in keys, f"{excluded} metric reached runtime"

    restored = await registry.set_status(
        source_a, "project_margin", MetricStatus.CERTIFIED, approved_by="reviewer"
    )
    assert restored.status is MetricStatus.CERTIFIED
    assert restored.approved_at is not None
    assert "project_margin" in {
        m.metric_key for m in await registry.certified(source_a)
    }


async def _assert_unknown_metric_update_is_refused(
    registry: PostgresMetricRegistry, source_a: UUID
) -> None:
    with pytest.raises(MetricRegistryError):
        await registry.set_status(source_a, "no_such_metric", MetricStatus.CERTIFIED)
