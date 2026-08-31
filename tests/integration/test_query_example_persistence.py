"""Execution evidence and example promotion, against real PostgreSQL.

The point of running these against a real database is the constraints: the
composite foreign key is what makes cross-datasource evidence impossible, and
no in-memory store can demonstrate that.
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
from app.data.gateway import ColumnMetadata, TableMetadata
from app.knowledge.candidates import (
    CandidateReview,
    CandidateStatus,
    CandidateType,
    KnowledgeCandidate,
    QueryExampleProposal,
)
from app.knowledge.evidence import ExecutionEvidence
from app.knowledge.memory import QuestionEvent
from app.knowledge.metrics import InMemoryMetricRegistry
from app.knowledge.migrations import apply_migrations
from app.knowledge.postgres_candidates import PostgresCandidateStore
from app.knowledge.postgres_evidence import PostgresExecutionEvidenceStore
from app.knowledge.postgres_guidance import PostgresGuidanceStore
from app.knowledge.postgres_memory import PostgresQuestionMemory
from app.security.sql_validation import SQLValidator
from tests.support.knowledge_database import (
    assert_is_test_database,
    ensure_test_database,
)

pytestmark = pytest.mark.postgres

EVIDENCE_SQL = (
    "SELECT emp_no, ann_sal_amt FROM erp.emp_comp_hist WHERE curr_flg = 'Y'"
)
QUESTION = "Show each employee's current compensation."
FINGERPRINT = "v1|route=adhoc|tables=erp.emp_comp_hist|aggregates=|grouping="


def _selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def _run(coroutine: Any) -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=_selector_loop) as runner:
            runner.run(coroutine)
        return
    asyncio.run(coroutine)


def _tables() -> list[TableMetadata]:
    return [
        TableMetadata(
            schema_name="erp",
            table_name="emp_comp_hist",
            columns=["emp_no", "ann_sal_amt", "curr_flg"],
            description="compensation history",
            column_metadata=[
                ColumnMetadata(name="emp_no", data_type="integer", nullable=False),
                ColumnMetadata(name="ann_sal_amt", data_type="numeric", nullable=False),
                ColumnMetadata(name="curr_flg", data_type="char", nullable=False),
            ],
        )
    ]


async def _fresh_source(dsn: str, name: str, *, migrate: bool = False) -> UUID:
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        if migrate:
            assert_is_test_database(dsn)
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


def test_evidence_survives_and_promotes_into_an_approved_example() -> None:
    _run(_exercise())


async def _exercise() -> None:
    if Settings().checkpoint_database_url is None:
        pytest.skip("CHECKPOINT_DATABASE_URL is not configured.")
    dsn = await ensure_test_database()
    source = await _fresh_source(dsn, "evidence", migrate=True)
    other = await _fresh_source(dsn, "evidence-other")

    async with AsyncConnectionPool(dsn, min_size=1, max_size=4, open=False) as pool:
        await pool.open(wait=True)
        memory = PostgresQuestionMemory(pool)
        evidence = PostgresExecutionEvidenceStore(pool)

        cluster = await memory.record(
            QuestionEvent(
                data_source_id=source,
                question_text=QUESTION,
                structural_fingerprint=FINGERPRINT,
                route="adhoc_analytics",
                success=True,
                validated=True,
                grounded=True,
            )
        )
        await evidence.record(
            ExecutionEvidence(
                data_source_id=source,
                cluster_id=cluster.id,
                question_text=QUESTION,
                validated_sql=EVIDENCE_SQL,
                schema_fingerprint="fp-1",
            )
        )

        # Question memory still holds no SQL: that is the boundary this design
        # exists to preserve.
        async with (
            pool.connection() as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = 'knowledge' AND table_name = 'question_events'"
            )
            columns = {row[0] for row in await cursor.fetchall()}
        assert not {column for column in columns if "sql" in column}

        # Evidence belongs to one datasource, enforced by the database.
        assert await evidence.for_cluster(other, cluster.id) is None

        candidates = PostgresCandidateStore(pool)
        candidate = await candidates.upsert(
            KnowledgeCandidate(
                data_source_id=source,
                candidate_type=CandidateType.QUERY_EXAMPLE,
                display_name="Current compensation",
                structural_fingerprint=FINGERPRINT,
                proposal=QueryExampleProposal(
                    display_name="Current compensation",
                    question=QUESTION,
                    semantic_plan="Read the row in force for each employee.",
                ),
                cluster_id=cluster.id,
                evidence_sql=EVIDENCE_SQL,
                evidence_schema_fingerprint="fp-1",
            )
        )
        assert candidate.evidence_sql == EVIDENCE_SQL, "evidence did not round-trip"

        guidance = PostgresGuidanceStore(pool)
        review = CandidateReview(
            store=candidates,
            registry=InMemoryMetricRegistry([]),
            guidance=guidance,
        )
        approved = await review.approve_query_example(
            source,
            candidate.id,
            validator=SQLValidator(max_rows=100, allowed_schemas=frozenset({"erp"})),
            authorized_tables=_tables(),
            current_schema_fingerprint="fp-2",
            reviewed_by="reviewer",
        )
        assert approved.query_pattern == EVIDENCE_SQL

    # ---- a fresh pool: nothing is carried in memory between them ----
    async with AsyncConnectionPool(dsn, min_size=1, max_size=2, open=False) as pool:
        await pool.open(wait=True)
        guidance = PostgresGuidanceStore(pool)
        authorized = frozenset({"erp.emp_comp_hist"})

        stored = await guidance.examples(source)
        assert [item.question for item in stored] == [QUESTION]

        for_owner = await guidance.relevant_examples(
            source,
            "show the current compensation of each employee",
            authorized_tables=authorized,
        )
        for_other = await guidance.relevant_examples(
            other,
            "show the current compensation of each employee",
            authorized_tables=authorized,
        )
        assert [item.question for item in for_owner] == [QUESTION]
        assert for_other == [], "an approved example crossed into another datasource"

        reviewed = await PostgresCandidateStore(pool).by_id(source, candidate.id)
        assert reviewed is not None
        assert reviewed.status is CandidateStatus.APPROVED
        assert reviewed.evidence_sql == EVIDENCE_SQL
