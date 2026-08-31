"""The background worker, against real PostgreSQL.

The point of these is that nothing in the test claims a job. Events are
recorded, the worker runs, and a proposal exists — which is the difference
between a queue that works and a loop that actually drains it.
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
from app.knowledge.worker import KnowledgeJobWorker
from app.llm.gateway import LLMGateway, LLMRateLimitError, ResponseModelT
from tests.support.knowledge_database import ensure_test_database

pytestmark = pytest.mark.postgres

FINGERPRINT = "v1|route=governed|metrics=annual_base_payroll|dimensions=department"
THRESHOLDS = {
    "QUESTION_CLUSTER_MIN_OCCURRENCES": 3,
    "QUESTION_CLUSTER_MIN_SUCCESSFUL": 3,
    "KNOWLEDGE_WORKER_ENABLED": True,
    "KNOWLEDGE_WORKER_POLL_SECONDS": 5.0,
}


class ProposingLLM(LLMGateway):
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


class QuotaExhaustedLLM(LLMGateway):
    """Reproduces the known Vertex/Gemini quota outage."""

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
        del model_alias, system, user, response_model
        self.calls += 1
        raise LLMRateLimitError("LiteLLM provider rate limit reached.")


def _selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def _run(coroutine: Any) -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_selector_loop) as runner:
            runner.run(coroutine)
        return
    asyncio.run(coroutine)


async def _fresh_source(dsn: str, name: str, *, migrate: bool = False) -> UUID:
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        if migrate:
            async with conn.cursor() as cursor:
                await cursor.execute("DROP SCHEMA IF EXISTS knowledge CASCADE")
            await apply_migrations(conn)
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO knowledge.data_sources"
                " (name, database_type, connection_ref)"
                " VALUES (%s, 'postgres', 'DATABASE_URL') RETURNING id",
                (f"{name}-{uuid4().hex[:8]}",),
            )
            row = await cursor.fetchone()
    assert row is not None
    return cast(UUID, row[0])


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


def test_the_worker_turns_a_recurring_question_into_a_proposal() -> None:
    _run(_exercise_end_to_end())


async def _exercise_end_to_end() -> None:
    settings = Settings(**THRESHOLDS)  # type: ignore[arg-type]
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    dsn = await ensure_test_database()
    source = await _fresh_source(dsn, "worker", migrate=True)

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
        llm = ProposingLLM()
        worker = KnowledgeJobWorker(
            settings=settings,
            jobs=jobs,
            generator=CandidateGenerator(
                llm=llm, store=candidates, registry=registry
            ),
            memory=memory,
        )

        # Ask the same thing until the cluster is eligible. Enqueueing is the
        # trigger's job; nothing here claims anything.
        for _ in range(3):
            cluster = await _record(memory, source)
            await trigger.consider(data_source_id=source, cluster=cluster)

        assert await candidates.list(source) == [], "nothing proposed before the worker"

        processed = await worker.drain_once()

        assert processed == 1
        assert llm.calls == 1
        stored = await candidates.list(source)
        assert len(stored) == 1
        assert stored[0].status is CandidateStatus.PROPOSED
        assert stored[0].display_name == "Annual Payroll Per Active Employee"

        # Proposal is automatic; certification is not.
        assert "annual_payroll_per_active_employee" not in {
            metric.metric_key for metric in await registry.certified(source)
        }

        # Queue drained: a second tick finds nothing and calls no model.
        assert await worker.drain_once() == 0
        assert llm.calls == 1


def test_the_running_loop_processes_without_being_driven() -> None:
    """Starts the real loop and waits, rather than calling drain_once."""
    _run(_exercise_running_loop())


async def _exercise_running_loop() -> None:
    settings = Settings(**{**THRESHOLDS, "KNOWLEDGE_WORKER_POLL_SECONDS": 5.0})  # type: ignore[arg-type]
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    dsn = await ensure_test_database()
    source = await _fresh_source(dsn, "loop")

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
        llm = ProposingLLM()
        worker = KnowledgeJobWorker(
            settings=settings,
            jobs=jobs,
            generator=CandidateGenerator(
                llm=llm, store=candidates, registry=registry
            ),
            memory=memory,
            # Short poll so the test does not wait on the production cadence.
            poll_seconds=0.05,
        )

        for _ in range(3):
            cluster = await _record(memory, source)
            await trigger.consider(data_source_id=source, cluster=cluster)

        await worker.start()
        try:
            for _ in range(100):
                if await candidates.list(source):
                    break
                await asyncio.sleep(0.05)
        finally:
            await worker.stop()

        stored = await candidates.list(source)
        assert stored, "the running worker never processed the queued job"
        assert stored[0].status is CandidateStatus.PROPOSED


def test_two_workers_do_not_process_the_same_job() -> None:
    _run(_exercise_two_workers())


async def _exercise_two_workers() -> None:
    settings = Settings(**THRESHOLDS)  # type: ignore[arg-type]
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    dsn = await ensure_test_database()
    source = await _fresh_source(dsn, "two-workers")

    async with AsyncConnectionPool(dsn, min_size=2, max_size=6, open=False) as pool:
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
        for _ in range(3):
            cluster = await _record(memory, source)
            await trigger.consider(data_source_id=source, cluster=cluster)

        shared_llm = ProposingLLM()

        def make_worker() -> KnowledgeJobWorker:
            return KnowledgeJobWorker(
                settings=settings,
                jobs=PostgresGenerationJobQueue(pool),
                generator=CandidateGenerator(
                    llm=shared_llm, store=candidates, registry=registry
                ),
                memory=memory,
            )

        processed = await asyncio.gather(
            make_worker().drain_once(), make_worker().drain_once()
        )

        assert sum(processed) == 1, "the job was processed more than once"
        assert shared_llm.calls == 1, "two workers both called the model"


def test_a_quota_outage_retries_within_bounds_and_records_a_safe_code() -> None:
    _run(_exercise_quota())


async def _exercise_quota() -> None:
    settings = Settings(**THRESHOLDS)  # type: ignore[arg-type]
    if settings.checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    dsn = await ensure_test_database()
    source = await _fresh_source(dsn, "quota")

    async with AsyncConnectionPool(dsn, min_size=1, max_size=3, open=False) as pool:
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
        for _ in range(3):
            cluster = await _record(memory, source)
            await trigger.consider(data_source_id=source, cluster=cluster)

        llm = QuotaExhaustedLLM()
        worker = KnowledgeJobWorker(
            settings=settings,
            jobs=jobs,
            generator=CandidateGenerator(
                llm=llm, store=candidates, registry=registry
            ),
            memory=memory,
        )

        for _ in range(MAX_GENERATION_ATTEMPTS + 2):
            if await worker.drain_once() == 0:
                break

        assert llm.calls <= MAX_GENERATION_ATTEMPTS, "retries were not bounded"
        assert await candidates.list(source) == [], "a failure produced a proposal"

        recorded = await jobs.jobs_for(source)
        assert recorded[0].status is JobStatus.FAILED
        assert recorded[0].last_error_code == "llm_rate_limited"
        # A short code only: no quota text, no project, no credential.
        for leaked in ("quota", "project", "key", "http"):
            assert leaked not in (recorded[0].last_error_code or "")
