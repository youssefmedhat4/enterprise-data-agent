"""Persistence of the whole knowledge runtime against real PostgreSQL.

Two properties are asserted that in-memory stores cannot provide.

**Restart.** Every repository and the pool are disposed, entirely fresh
instances are constructed, and the state is still there. Reusing an object in
the same test would prove nothing about persistence.

**Multi-instance.** Two independent store instances over the same database see
each other's writes, which is what a second API worker actually is.
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.knowledge.candidates import (
    CandidateStatus,
    CandidateType,
    KnowledgeCandidate,
    MetricProposal,
)
from app.knowledge.contracts import ApprovalStatus
from app.knowledge.discovery import SemanticModel
from app.knowledge.contracts import SemanticAttribute, SemanticEntity
from app.knowledge.expressions import BinaryOp, MetricRef
from app.knowledge.guidance import ApprovedQueryExample, BusinessInstruction
from app.knowledge.jobs import JobStatus, PostgresGenerationJobQueue
from app.knowledge.memory import QuestionEvent
from app.knowledge.metrics import MetricStatus
from app.knowledge.migrations import apply_migrations
from app.knowledge.postgres_candidates import PostgresCandidateStore
from app.knowledge.postgres_guidance import PostgresGuidanceStore
from app.knowledge.postgres_memory import PostgresQuestionMemory
from app.knowledge.postgres_metrics import PostgresMetricRegistry
from app.knowledge.postgres_semantics import PostgresSemanticRepository
from app.knowledge.seed import registered_metrics_for_default_datasource
from app.semantic.entities import EntityResolver

pytestmark = pytest.mark.postgres

FINGERPRINT = "v1|route=governed|metrics=annual_base_payroll|dimensions=department"


def test_knowledge_state_survives_restart_and_is_shared() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_selector_loop) as runner:
            runner.run(_exercise())
        return
    asyncio.run(_exercise())


def _selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def _dsn() -> str:
    settings = Settings()
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    return settings.checkpoint_database_url.get_secret_value()


async def _insert_source(conn: psycopg.AsyncConnection[Any], name: str) -> UUID:
    async with conn.cursor() as cursor:
        await cursor.execute(
            "INSERT INTO knowledge.data_sources (name, database_type, connection_ref)"
            " VALUES (%s, 'postgres', 'DATABASE_URL') RETURNING id",
            (name,),
        )
        row = cast("tuple[Any, ...] | None", await cursor.fetchone())
    assert row is not None
    return cast(UUID, row[0])


async def _exercise() -> None:
    dsn = await _dsn()

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("DROP SCHEMA IF EXISTS knowledge CASCADE")
        assert await apply_migrations(conn), "expected a clean migration"
        assert await apply_migrations(conn) == [], "rerun must apply nothing"
        source_a = await _insert_source(conn, "persist-a")
        source_b = await _insert_source(conn, "persist-b")

    written = await _write_everything(dsn, source_a)

    # ---- restart: every object above is gone; build entirely fresh ones ----
    await _assert_survives_restart(dsn, source_a, source_b, written)
    await _assert_two_instances_share_state(dsn, source_a)


async def _write_everything(dsn: str, source: UUID) -> dict[str, Any]:
    async with AsyncConnectionPool(dsn, min_size=1, max_size=3, open=False) as pool:
        await pool.open(wait=True)
        registry = PostgresMetricRegistry(pool)
        for metric in registered_metrics_for_default_datasource(source):
            await registry.upsert(metric)

        memory = PostgresQuestionMemory(pool)
        cluster = None
        for _ in range(3):
            cluster = await memory.record(
                QuestionEvent(
                    data_source_id=source,
                    question_text="payroll by department",
                    structural_fingerprint=FINGERPRINT,
                    route="governed_metric",
                    metric_keys=("annual_base_payroll",),
                    success=True,
                    validated=True,
                    grounded=True,
                )
            )
        assert cluster is not None

        candidates = PostgresCandidateStore(pool)
        rejected = KnowledgeCandidate(
            data_source_id=source,
            candidate_type=CandidateType.METRIC,
            display_name="Payroll Per Head",
            structural_fingerprint=FINGERPRINT,
            proposal=MetricProposal(
                metric_key="payroll_per_head",
                display_name="Payroll Per Head",
                expression=BinaryOp(
                    operator="divide",
                    left=MetricRef(metric_key="annual_base_payroll"),
                    right=MetricRef(metric_key="active_headcount"),
                ),
            ),
            cluster_id=cluster.id,
            status=CandidateStatus.REJECTED,
            rejection_reason="Not durable.",
        )
        await candidates.upsert(rejected)

        guidance = PostgresGuidanceStore(pool)
        await guidance.approve_example(
            ApprovedQueryExample(
                data_source_id=source,
                question="payroll by department",
                query_pattern="SELECT 1 FROM analytics.employees",
            ),
            was_successful=True,
            was_validated=True,
        )
        await guidance.approve_instruction(
            BusinessInstruction(
                data_source_id=source,
                title="Payroll roster scope",
                instruction="Payroll includes all roster employees.",
                semantic_concepts=("payroll",),
            )
        )

        entity_id = uuid4()
        await PostgresSemanticRepository(pool).save(
            SemanticModel(
                data_source_id=source,
                schema_fingerprint="fp-1",
                entities=(
                    SemanticEntity(
                        id=entity_id,
                        data_source_id=source,
                        source_schema="analytics",
                        source_table="business_units",
                        entity_name="Organizational Unit",
                        status=ApprovalStatus.CONFIRMED,
                        schema_fingerprint="fp-1",
                    ),
                ),
                attributes=(
                    SemanticAttribute(
                        id=uuid4(),
                        data_source_id=source,
                        entity_id=entity_id,
                        source_column="unit_id",
                        concept_name="Organizational Unit Key",
                        is_identifier=True,
                        status=ApprovalStatus.CONFIRMED,
                    ),
                    SemanticAttribute(
                        id=uuid4(),
                        data_source_id=source,
                        entity_id=entity_id,
                        source_column="unit_name",
                        concept_name="Organizational Unit Name",
                        status=ApprovalStatus.CONFIRMED,
                    ),
                ),
            )
        )

        job = await PostgresGenerationJobQueue(pool).enqueue(
            data_source_id=source, cluster_id=cluster.id
        )
        assert job is not None
        return {"cluster_id": cluster.id, "candidate_id": rejected.id, "job_id": job.id}


async def _assert_survives_restart(
    dsn: str, source: UUID, other: UUID, written: dict[str, Any]
) -> None:
    async with AsyncConnectionPool(dsn, min_size=1, max_size=3, open=False) as pool:
        await pool.open(wait=True)

        certified = await PostgresMetricRegistry(pool).certified(source)
        assert "annual_base_payroll" in {m.metric_key for m in certified}
        assert all(m.status is MetricStatus.CERTIFIED for m in certified)

        memory = PostgresQuestionMemory(pool)
        clusters = await memory.clusters(source)
        assert len(clusters) == 1
        assert clusters[0].occurrence_count == 3
        assert clusters[0].successful_count == 3
        events = await memory.events_for_cluster(source, clusters[0].id)
        assert len(events) == 3
        assert events[0].metric_keys == ("annual_base_payroll",)

        candidate = await PostgresCandidateStore(pool).by_id(
            source, written["candidate_id"]
        )
        assert candidate is not None
        assert candidate.status is CandidateStatus.REJECTED, (
            "rejection suppression did not survive restart"
        )
        assert candidate.rejection_reason == "Not durable."

        guidance = PostgresGuidanceStore(pool)
        assert await guidance.relevant_examples(source, "payroll by department")
        assert await guidance.relevant_instructions(source, "payroll commitment")

        model = await PostgresSemanticRepository(pool).load(source)
        assert {e.entity_name for e in model.confirmed_entities()} == {
            "Organizational Unit"
        }

        # The persisted model drives resolution, with no in-memory handoff.
        from app.data.gateway import ColumnMetadata, TableMetadata

        units = TableMetadata(
            schema_name="analytics",
            table_name="business_units",
            columns=["unit_id", "unit_name"],
            description="",
            column_metadata=[
                ColumnMetadata(
                    name="unit_id",
                    data_type="VARCHAR",
                    nullable=False,
                    description="",
                    observed_values=("BU-1",),
                    observed_values_source="fixture",
                ),
                ColumnMetadata(
                    name="unit_name",
                    data_type="VARCHAR",
                    nullable=False,
                    description="",
                    observed_values=("Engineering",),
                    observed_values_source="fixture",
                ),
            ],
        )
        resolution = EntityResolver().resolve(
            user_text="margin for Engineering",
            authorized_tables=[units],
            concept="Organizational Unit",
            semantic_model=model,
        )
        match = resolution.resolved
        assert match is not None
        assert match.value == "Engineering"
        assert match.canonical_column == "analytics.business_units.unit_id"

        # Nothing leaked into the other datasource.
        assert await PostgresMetricRegistry(pool).certified(other) == []
        assert await memory.clusters(other) == []
        assert await PostgresCandidateStore(pool).list(other) == []
        assert await guidance.examples(other) == []


async def _assert_two_instances_share_state(dsn: str, source: UUID) -> None:
    """Instance A writes, instance B reads — what two API workers really are."""
    async with (
        AsyncConnectionPool(dsn, min_size=1, max_size=2, open=False) as pool_a,
        AsyncConnectionPool(dsn, min_size=1, max_size=2, open=False) as pool_b,
    ):
        await pool_a.open(wait=True)
        await pool_b.open(wait=True)

        registry_a = PostgresMetricRegistry(pool_a)
        registry_b = PostgresMetricRegistry(pool_b)
        await registry_a.set_status(
            source, "project_margin", MetricStatus.DEPRECATED
        )
        assert "project_margin" not in {
            m.metric_key for m in await registry_b.certified(source)
        }

        memory_a = PostgresQuestionMemory(pool_a)
        memory_b = PostgresQuestionMemory(pool_b)
        await memory_a.record(
            QuestionEvent(
                data_source_id=source,
                question_text="payroll by department again",
                structural_fingerprint=FINGERPRINT,
                route="governed_metric",
                success=True,
                validated=True,
                grounded=True,
            )
        )
        clusters = await memory_b.clusters(source)
        assert clusters[0].occurrence_count == 4, "worker B did not see A's write"

        # Only one worker may claim a job.
        queue_a = PostgresGenerationJobQueue(pool_a)
        queue_b = PostgresGenerationJobQueue(pool_b)
        claimed_a = await queue_a.claim_next()
        claimed_b = await queue_b.claim_next()
        assert claimed_a is not None
        assert claimed_b is None, "two workers claimed the same generation job"
        assert claimed_a.status is JobStatus.RUNNING
        assert claimed_a.attempt_count == 1

        # A transient failure returns it for a bounded retry.
        await queue_a.release_for_retry(claimed_a.id, error_code="llm_rate_limited")
        again = await queue_b.claim_next()
        assert again is not None
        assert again.attempt_count == 2
        await queue_b.complete(again.id)
        assert await queue_a.claim_next() is None

        finished = await queue_a.jobs_for(source)
        assert finished[0].status is JobStatus.SUCCEEDED
        # A sanitized code only: never a provider message.
        assert finished[0].last_error_code in {None, "llm_rate_limited"}


def test_a_second_open_job_cannot_be_queued_for_one_cluster() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_selector_loop) as runner:
            runner.run(_exercise_duplicate_guard())
        return
    asyncio.run(_exercise_duplicate_guard())


async def _exercise_duplicate_guard() -> None:
    dsn = await _dsn()
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        source = await _insert_source(conn, f"dup-guard-{uuid4().hex[:8]}")

    async with AsyncConnectionPool(dsn, min_size=1, max_size=2, open=False) as pool:
        await pool.open(wait=True)
        memory = PostgresQuestionMemory(pool)
        cluster = await memory.record(
            QuestionEvent(
                data_source_id=source,
                question_text="q",
                structural_fingerprint=f"fp-{uuid4().hex[:8]}",
                route="governed_metric",
                success=True,
                validated=True,
                grounded=True,
                created_at=datetime.now(UTC),
            )
        )
        queue = PostgresGenerationJobQueue(pool)
        first = await queue.enqueue(data_source_id=source, cluster_id=cluster.id)
        second = await queue.enqueue(data_source_id=source, cluster_id=cluster.id)

        assert first is not None
        assert second is None, "a threshold crossed twice enqueued twice"
