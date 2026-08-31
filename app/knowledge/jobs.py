"""Bounded generation jobs for knowledge candidates.

Candidate generation calls a model, so it must not run inline on an analytics
request and must not run twice for the same cluster. A job row is a claim:
`claim_next` takes one with `FOR UPDATE SKIP LOCKED`, so several API workers can
poll concurrently and exactly one proceeds. An in-process guard would neither
survive a restart nor coordinate between workers, which is precisely the failure
this exists to prevent.

Enqueueing is idempotent by construction: a partial unique index allows only one
PENDING or RUNNING job per cluster, so a threshold crossed by several events in
quick succession still produces one job. Finished rows are exempt, which keeps an
audit trail and lets a cluster be reconsidered later on new evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from psycopg import errors
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

#: Bounded retries. A quota outage should not spin, and a proposal that cannot
#: be generated after a few tries is a reviewer's problem, not a retry loop's.
MAX_GENERATION_ATTEMPTS = 3


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class GenerationJob:
    id: UUID
    data_source_id: UUID
    cluster_id: UUID
    status: JobStatus
    attempt_count: int
    last_error_code: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PostgresGenerationJobQueue:
    """Claim-once job queue for candidate generation."""

    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def enqueue(
        self, *, data_source_id: UUID, cluster_id: UUID
    ) -> GenerationJob | None:
        """Queue generation for a cluster, or return None if already queued.

        The unique index does the deduplication, so this is safe to call on
        every event without checking first; a duplicate is an expected outcome
        rather than an error.
        """
        try:
            async with (
                self._pool.connection() as connection,
                connection.cursor(row_factory=dict_row) as cursor,
            ):
                await cursor.execute(
                    "INSERT INTO knowledge.knowledge_generation_jobs"
                    " (data_source_id, cluster_id) VALUES (%s, %s)"
                    " RETURNING id, data_source_id, cluster_id, status,"
                    " attempt_count, last_error_code, created_at, started_at,"
                    " completed_at",
                    (data_source_id, cluster_id),
                )
                row = cast("dict[str, Any] | None", await cursor.fetchone())
        except errors.UniqueViolation:
            logger.info(
                "generation already queued: data_source=%s cluster=%s",
                data_source_id,
                cluster_id,
            )
            return None
        return None if row is None else _to_job(row)

    async def claim_next(self) -> GenerationJob | None:
        """Take one pending job, or None when nothing is claimable.

        `SKIP LOCKED` is what makes this safe under several workers: a row
        another worker is already holding is passed over rather than waited on,
        so pollers never serialise behind each other.
        """
        async with (
            self._pool.connection() as connection,
            connection.transaction(),
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT id FROM knowledge.knowledge_generation_jobs"
                " WHERE status = 'PENDING'"
                "   AND attempt_count < %(max_attempts)s"
                " ORDER BY created_at"
                " FOR UPDATE SKIP LOCKED"
                " LIMIT 1",
                {"max_attempts": MAX_GENERATION_ATTEMPTS},
            )
            claimed = cast("dict[str, Any] | None", await cursor.fetchone())
            if claimed is None:
                return None
            await cursor.execute(
                "UPDATE knowledge.knowledge_generation_jobs"
                " SET status = 'RUNNING', started_at = now(),"
                "     attempt_count = attempt_count + 1"
                " WHERE id = %(id)s"
                " RETURNING id, data_source_id, cluster_id, status,"
                " attempt_count, last_error_code, created_at, started_at,"
                " completed_at",
                {"id": claimed["id"]},
            )
            row = cast("dict[str, Any] | None", await cursor.fetchone())
        return None if row is None else _to_job(row)

    async def complete(self, job_id: UUID) -> None:
        await self._finish(job_id, JobStatus.SUCCEEDED, None)

    async def fail(self, job_id: UUID, *, error_code: str) -> None:
        """Record a sanitized failure.

        `error_code` is a short machine-readable token such as
        `llm_rate_limited`. Provider messages are never stored: they can quote
        request content, and a quota message in particular carries account
        detail that has no business in this table.
        """
        await self._finish(job_id, JobStatus.FAILED, error_code)

    async def release_for_retry(self, job_id: UUID, *, error_code: str) -> None:
        """Return a job to PENDING so a later poll may retry it.

        Used for transient failures such as a quota outage. `attempt_count`
        already advanced during the claim, so retries remain bounded without any
        extra bookkeeping.
        """
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "UPDATE knowledge.knowledge_generation_jobs"
                " SET status = CASE WHEN attempt_count >= %(max_attempts)s"
                "                   THEN 'FAILED'::knowledge.job_status"
                "                   ELSE 'PENDING'::knowledge.job_status END,"
                "     last_error_code = %(error_code)s,"
                "     completed_at = CASE WHEN attempt_count >= %(max_attempts)s"
                "                         THEN now() ELSE NULL END"
                " WHERE id = %(id)s",
                {
                    "id": job_id,
                    "error_code": error_code,
                    "max_attempts": MAX_GENERATION_ATTEMPTS,
                },
            )

    async def jobs_for(self, data_source_id: UUID) -> list[GenerationJob]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT id, data_source_id, cluster_id, status, attempt_count,"
                " last_error_code, created_at, started_at, completed_at"
                " FROM knowledge.knowledge_generation_jobs"
                " WHERE data_source_id = %(data_source_id)s"
                " ORDER BY created_at DESC",
                {"data_source_id": data_source_id},
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        return [_to_job(row) for row in rows]

    async def _finish(
        self, job_id: UUID, status: JobStatus, error_code: str | None
    ) -> None:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "UPDATE knowledge.knowledge_generation_jobs"
                " SET status = %(status)s, completed_at = now(),"
                "     last_error_code = %(error_code)s"
                " WHERE id = %(id)s",
                {"id": job_id, "status": status.value, "error_code": error_code},
            )


def _to_job(row: dict[str, Any]) -> GenerationJob:
    return GenerationJob(
        id=row["id"],
        data_source_id=row["data_source_id"],
        cluster_id=row["cluster_id"],
        status=JobStatus(row["status"]),
        attempt_count=row["attempt_count"],
        last_error_code=row["last_error_code"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )
