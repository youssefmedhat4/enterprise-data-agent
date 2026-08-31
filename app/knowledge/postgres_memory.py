"""PostgreSQL-backed question memory and clustering.

Recording an event and updating its cluster happen in one transaction. Without
that, two events arriving together could both find no cluster and both insert
one; the unique key on `(data_source_id, structural_fingerprint)` turns that
race into an error rather than a duplicate, and the upsert turns the error into
the correct outcome.

`occurrence_count` and `successful_count` are incremented in SQL rather than
read-modify-written in Python, so concurrent events cannot lose an increment.
"""

from __future__ import annotations

import logging
from typing import Any, cast
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.knowledge.memory import QuestionCluster, QuestionEvent

logger = logging.getLogger(__name__)

_UPSERT_CLUSTER = """
    INSERT INTO knowledge.question_clusters
        (data_source_id, structural_fingerprint, canonical_summary,
         occurrence_count, successful_count, first_seen_at, last_seen_at,
         embedding_provider, embedding_model, embedding_dimension)
    VALUES
        (%(data_source_id)s, %(fingerprint)s, %(summary)s, 1, %(successful)s,
         %(seen_at)s, %(seen_at)s, %(provider)s, %(model)s, %(dimension)s)
    ON CONFLICT (data_source_id, structural_fingerprint)
    DO UPDATE SET
        occurrence_count = knowledge.question_clusters.occurrence_count + 1,
        successful_count =
            knowledge.question_clusters.successful_count + %(successful)s,
        last_seen_at = GREATEST(
            knowledge.question_clusters.last_seen_at, EXCLUDED.last_seen_at
        )
    RETURNING id, data_source_id, structural_fingerprint, canonical_summary,
              occurrence_count, successful_count, first_seen_at, last_seen_at,
              status
"""

_INSERT_EVENT = """
    INSERT INTO knowledge.question_events
        (data_source_id, question_text, normalized_question, route, success,
         grounded, validated, semantic_plan_summary, structural_fingerprint,
         cluster_id, thread_id, model_profile, metric_keys, created_at,
         embedding_provider, embedding_model, embedding_dimension)
    VALUES
        (%(data_source_id)s, %(question_text)s, %(normalized_question)s,
         %(route)s, %(success)s, %(grounded)s, %(validated)s, %(plan)s,
         %(fingerprint)s, %(cluster_id)s, %(thread_id)s, %(model_profile)s,
         %(metric_keys)s, %(created_at)s, %(provider)s, %(model)s, %(dimension)s)
    RETURNING id
"""

_SELECT_CLUSTERS = """
    SELECT id, data_source_id, structural_fingerprint, canonical_summary,
           occurrence_count, successful_count, first_seen_at, last_seen_at,
           status
      FROM knowledge.question_clusters
     WHERE data_source_id = %(data_source_id)s
     ORDER BY last_seen_at DESC
"""


class PostgresQuestionMemory:
    """Datasource-scoped question memory persisted in the knowledge database."""

    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def record(self, event: QuestionEvent) -> QuestionCluster:
        """Persist one event and fold it into its cluster, atomically.

        Nothing about the answer is written: the statement has no column for
        rows, measures, or answer text, matching the contract.
        """
        successful = 1 if event.is_trustworthy_evidence else 0
        async with (
            self._pool.connection() as connection,
            connection.transaction(),
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                _UPSERT_CLUSTER,
                {
                    "data_source_id": event.data_source_id,
                    "fingerprint": event.structural_fingerprint,
                    "summary": event.normalized_question,
                    "successful": successful,
                    "seen_at": event.created_at,
                    # A vector is only interpretable with the model that
                    # produced it, so the two travel together.
                    "provider": _provider_of(event),
                    "model": _model_of(event),
                    "dimension": (
                        event.embedding.dimension
                        if event.embedding is not None
                        else None
                    ),
                },
            )
            row = cast("dict[str, Any] | None", await cursor.fetchone())
            if row is None:  # pragma: no cover - RETURNING always yields
                raise RuntimeError("Cluster upsert returned no row.")
            cluster = _to_cluster(row)

            await cursor.execute(
                _INSERT_EVENT,
                {
                    "data_source_id": event.data_source_id,
                    "question_text": event.question_text,
                    "normalized_question": event.normalized_question,
                    "route": event.route,
                    "success": event.success,
                    "grounded": event.grounded,
                    "validated": event.validated,
                    "plan": event.semantic_plan_summary,
                    "fingerprint": event.structural_fingerprint,
                    "cluster_id": cluster.id,
                    "thread_id": event.thread_id,
                    "model_profile": event.model_profile,
                    "metric_keys": list(event.metric_keys),
                    "created_at": event.created_at,
                    # A vector is only interpretable with the model that
                    # produced it, so the two travel together.
                    "provider": _provider_of(event),
                    "model": _model_of(event),
                    "dimension": (
                        event.embedding.dimension
                        if event.embedding is not None
                        else None
                    ),
                },
            )
            event_row = cast("dict[str, Any] | None", await cursor.fetchone())
            if event_row is not None:
                # Membership records that this event was counted once, so a
                # replay cannot inflate the cluster's evidence.
                await cursor.execute(
                    "INSERT INTO knowledge.question_cluster_members"
                    " (cluster_id, event_id, data_source_id)"
                    " VALUES (%(cluster_id)s, %(event_id)s, %(data_source_id)s)"
                    " ON CONFLICT DO NOTHING",
                    {
                        "cluster_id": cluster.id,
                        "event_id": event_row["id"],
                        "data_source_id": event.data_source_id,
                    },
                )
        return cluster

    async def clusters(self, data_source_id: UUID) -> list[QuestionCluster]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                _SELECT_CLUSTERS, {"data_source_id": data_source_id}
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        return [_to_cluster(row) for row in rows]

    async def cluster_for_fingerprint(
        self, data_source_id: UUID, structural_fingerprint: str
    ) -> QuestionCluster | None:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                _SELECT_CLUSTERS.replace(
                    "ORDER BY last_seen_at DESC",
                    "AND structural_fingerprint = %(fingerprint)s",
                ),
                {
                    "data_source_id": data_source_id,
                    "fingerprint": structural_fingerprint,
                },
            )
            row = cast("dict[str, Any] | None", await cursor.fetchone())
        return None if row is None else _to_cluster(row)

    async def events_for_cluster(
        self, data_source_id: UUID, cluster_id: UUID, *, limit: int = 20
    ) -> list[QuestionEvent]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT id, data_source_id, question_text, route,"
                " structural_fingerprint, success, grounded, validated,"
                " semantic_plan_summary, thread_id, model_profile,"
                " metric_keys, created_at"
                " FROM knowledge.question_events"
                " WHERE data_source_id = %(data_source_id)s"
                " AND cluster_id = %(cluster_id)s"
                " ORDER BY created_at DESC LIMIT %(limit)s",
                {
                    "data_source_id": data_source_id,
                    "cluster_id": cluster_id,
                    "limit": limit,
                },
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        return [
            QuestionEvent(
                id=row["id"],
                data_source_id=row["data_source_id"],
                question_text=row["question_text"],
                structural_fingerprint=row["structural_fingerprint"],
                route=row["route"],
                thread_id=row["thread_id"],
                model_profile=row["model_profile"],
                metric_keys=tuple(row["metric_keys"] or ()),
                semantic_plan_summary=row["semantic_plan_summary"],
                success=row["success"],
                validated=row["validated"],
                grounded=row["grounded"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def eligible_clusters(
        self,
        data_source_id: UUID,
        *,
        min_occurrences: int,
        min_successful: int,
    ) -> list[QuestionCluster]:
        """Clusters that meet the configured thresholds, filtered in SQL."""
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                _SELECT_CLUSTERS.replace(
                    "ORDER BY last_seen_at DESC",
                    " AND status = 'ACTIVE'"
                    " AND occurrence_count >= %(min_occurrences)s"
                    " AND successful_count >= %(min_successful)s"
                    " ORDER BY last_seen_at DESC",
                ),
                {
                    "data_source_id": data_source_id,
                    "min_occurrences": min_occurrences,
                    "min_successful": min_successful,
                },
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        return [_to_cluster(row) for row in rows]


def _provider_of(event: QuestionEvent) -> str | None:
    if event.embedding is not None:
        return event.embedding.provider
    return event.embedding_provider


def _model_of(event: QuestionEvent) -> str | None:
    if event.embedding is not None:
        return event.embedding.model
    return event.embedding_model


def _to_cluster(row: dict[str, Any]) -> QuestionCluster:
    return QuestionCluster(
        id=row["id"],
        data_source_id=row["data_source_id"],
        structural_fingerprint=row["structural_fingerprint"],
        canonical_summary=row["canonical_summary"],
        occurrence_count=row["occurrence_count"],
        successful_count=row["successful_count"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        status=row["status"],
    )
