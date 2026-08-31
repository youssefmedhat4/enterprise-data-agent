"""Automatic candidate generation: eligibility, queueing, worker safety.

Uses real PostgreSQL because the guarantees being asserted are database
guarantees: one open job per cluster is a partial unique index, and one claimant
per job is FOR UPDATE SKIP LOCKED. Neither can be demonstrated in memory.
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.knowledge.candidates import (
    CandidateGeneration,
    CandidateGenerator,
    CandidateStatus,
    MetricProposal,
)
from app.knowledge.expressions import BinaryOp, MetricRef
from app.knowledge.jobs import (
    MAX_GENERATION_ATTEMPTS,
    JobStatus,
    PostgresGenerationJobQueue,
)
from app.knowledge.memory import QuestionEvent
from app.knowledge.migrations import apply_migrations
from app.knowledge.postgres_candidates import PostgresCandidateStore
from app.knowledge.postgres_memory import PostgresQuestionMemory
from app.knowledge.postgres_metrics import PostgresMetricRegistry
from app.knowledge.seed import registered_metrics_for_default_datasource
from app.knowledge.triggers import CandidateTrigger
from app.llm.gateway import LLMGateway, ResponseModelT
from tests.support.knowledge_database import ensure_test_database

pytestmark = pytest.mark.postgres

FINGERPRINT = "v1|route=governed|metrics=annual_base_payroll|dimensions=department"

#: Small values so the threshold is crossed deterministically.
THRESHOLD_SETTINGS = {
    "QUESTION_CLUSTER_MIN_OCCURRENCES": 3,
    "QUESTION_CLUSTER_MIN_SUCCESSFUL": 3,
}


class ScriptedGenerator(LLMGateway):
    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system, user
        self.calls += 1
        return CandidateGeneration(  # type: ignore[return-value]
            proposes=True,
            metric=MetricProposal(
                metric_key="annual_payroll_per_active_employee",
                display_name="Annual Payroll Per Active Employee",
                expression=BinaryOp(
                    operator="divide",
                    left=MetricRef(metric_key="annual_base_payroll"),
                    right=MetricRef(metric_key="active_headcount"),
                ),
                grain="department",
                dimensions=["department"],
            ),
        )


def test_candidate_generation_triggers_once_and_survives_restart() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_selector_loop) as runner:
            runner.run(_exercise())
        return
    asyncio.run(_exercise())


def _selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


async def _record(memory: PostgresQuestionMemory, source: UUID) -> Any:
    return await memory.record(
        QuestionEvent(
            data_source_id=source,
            question_text="payroll per employee",
            structural_fingerprint=FINGERPRINT,
            route="governed_metric",
            metric_keys=("annual_base_payroll", "active_headcount"),
            success=True,
            validated=True,
            grounded=True,
        )
    )


async def _exercise() -> None:
    settings = Settings(**THRESHOLD_SETTINGS)  # type: ignore[arg-type]
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    dsn = await ensure_test_database()

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("DROP SCHEMA IF EXISTS knowledge CASCADE")
        await apply_migrations(conn)
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO knowledge.data_sources"
                " (name, database_type, connection_ref)"
                " VALUES ('autotrigger', 'postgres', 'DATABASE_URL') RETURNING id"
            )
            row = await cursor.fetchone()
        assert row is not None
        source = cast(UUID, row[0])

    async with AsyncConnectionPool(dsn, min_size=1, max_size=4, open=False) as pool:
        await pool.open(wait=True)
        registry = PostgresMetricRegistry(pool)
        for metric in registered_metrics_for_default_datasource(source):
            await registry.upsert(metric)

        memory = PostgresQuestionMemory(pool)
        jobs = PostgresGenerationJobQueue(pool)
        candidates = PostgresCandidateStore(pool)
        trigger = CandidateTrigger(
            settings=settings, jobs=jobs, candidates=candidates
        )

        # Below threshold: nothing queued.
        for _ in range(2):
            cluster = await _record(memory, source)
            assert not await trigger.consider(
                data_source_id=source, cluster=cluster
            )
        assert await jobs.jobs_for(source) == []

        # Threshold crossed: exactly one job.
        cluster = await _record(memory, source)
        assert await trigger.consider(data_source_id=source, cluster=cluster)
        assert len(await jobs.jobs_for(source)) == 1

        # A further event must not duplicate it.
        cluster = await _record(memory, source)
        assert not await trigger.consider(data_source_id=source, cluster=cluster)
        assert len(await jobs.jobs_for(source)) == 1

        # Two workers race for the one job; exactly one wins.
        worker_a = PostgresGenerationJobQueue(pool)
        worker_b = PostgresGenerationJobQueue(pool)
        claimed = await asyncio.gather(
            worker_a.claim_next(), worker_b.claim_next()
        )
        winners = [job for job in claimed if job is not None]
        assert len(winners) == 1, "two workers claimed the same job"
        job = winners[0]

        # The winner generates, and the candidate is PROPOSED.
        llm = ScriptedGenerator()
        generator = CandidateGenerator(
            llm=llm, store=candidates, registry=registry
        )
        proposed = await generator.propose_for_cluster(
            data_source_id=source,
            cluster=cluster,
            example_questions=["payroll per employee"],
        )
        await jobs.complete(job.id)

        assert proposed is not None
        assert proposed.status is CandidateStatus.PROPOSED
        assert llm.calls == 1, "generation called the model more than once"

        # It is not yet certified, so governed runtime cannot use it.
        assert "annual_payroll_per_active_employee" not in {
            metric.metric_key for metric in await registry.certified(source)
        }

        # Now represented, so no further job is queued for the same pattern.
        cluster = await _record(memory, source)
        assert not await trigger.consider(data_source_id=source, cluster=cluster)

    # ---- restart: fresh pool and stores ----
    async with AsyncConnectionPool(dsn, min_size=1, max_size=2, open=False) as pool:
        await pool.open(wait=True)
        stored = await PostgresCandidateStore(pool).list(source)
        assert len(stored) == 1
        assert stored[0].status is CandidateStatus.PROPOSED
        assert stored[0].display_name == "Annual Payroll Per Active Employee"

        finished = await PostgresGenerationJobQueue(pool).jobs_for(source)
        assert finished[0].status is JobStatus.SUCCEEDED


def test_quota_failure_is_recorded_without_exposing_provider_detail() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_selector_loop) as runner:
            runner.run(_exercise_quota_failure())
        return
    asyncio.run(_exercise_quota_failure())


async def _exercise_quota_failure() -> None:
    settings = Settings()
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    dsn = await ensure_test_database()

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO knowledge.data_sources"
                " (name, database_type, connection_ref)"
                f" VALUES ('quota-{uuid4().hex[:8]}', 'postgres', 'DATABASE_URL')"
                " RETURNING id"
            )
            row = await cursor.fetchone()
        assert row is not None
        source = cast(UUID, row[0])

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
            )
        )
        jobs = PostgresGenerationJobQueue(pool)
        await jobs.enqueue(data_source_id=source, cluster_id=cluster.id)

        # Simulate the known Gemini quota outage across bounded attempts.
        for _ in range(MAX_GENERATION_ATTEMPTS):
            job = await jobs.claim_next()
            if job is None:
                break
            await jobs.release_for_retry(job.id, error_code="llm_rate_limited")

        assert await jobs.claim_next() is None, "retries were not bounded"
        recorded = await jobs.jobs_for(source)
        assert recorded[0].status is JobStatus.FAILED
        assert recorded[0].last_error_code == "llm_rate_limited"
        # A sanitized code only: no quota message, no project, no key.
        assert "quota" not in (recorded[0].last_error_code or "")
