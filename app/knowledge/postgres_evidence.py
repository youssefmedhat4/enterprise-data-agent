"""PostgreSQL-backed execution evidence.

Evidence has to outlive the process that observed the run: the worker that
proposes a query example runs later, and a reviewer looks at it later still.

One row per cluster, replaced on each qualifying run. The composite foreign key
to `question_clusters (id, data_source_id)` is what makes cross-datasource
evidence impossible rather than merely unlikely.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.knowledge.evidence import ExecutionEvidence, ExecutionEvidenceStore

_UPSERT = """
    INSERT INTO knowledge.query_execution_evidence
        (data_source_id, cluster_id, question_text, validated_sql,
         schema_fingerprint, recorded_at)
    VALUES
        (%(data_source_id)s, %(cluster_id)s, %(question_text)s, %(sql)s,
         %(fingerprint)s, %(recorded_at)s)
    ON CONFLICT (data_source_id, cluster_id)
    DO UPDATE SET
        question_text = EXCLUDED.question_text,
        validated_sql = EXCLUDED.validated_sql,
        schema_fingerprint = EXCLUDED.schema_fingerprint,
        recorded_at = EXCLUDED.recorded_at
"""

_SELECT = """
    SELECT data_source_id, cluster_id, question_text, validated_sql,
           schema_fingerprint, recorded_at
    FROM knowledge.query_execution_evidence
    WHERE data_source_id = %(data_source_id)s AND cluster_id = %(cluster_id)s
"""


class PostgresExecutionEvidenceStore(ExecutionEvidenceStore):
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def record(self, evidence: ExecutionEvidence) -> ExecutionEvidence:
        async with (
            self._pool.connection() as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(
                _UPSERT,
                {
                    "data_source_id": evidence.data_source_id,
                    "cluster_id": evidence.cluster_id,
                    "question_text": evidence.question_text,
                    "sql": evidence.validated_sql,
                    "fingerprint": evidence.schema_fingerprint,
                    "recorded_at": evidence.recorded_at,
                },
            )
        return evidence

    async def for_cluster(
        self, data_source_id: UUID, cluster_id: UUID
    ) -> ExecutionEvidence | None:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                _SELECT,
                {"data_source_id": data_source_id, "cluster_id": cluster_id},
            )
            row = cast("dict[str, Any] | None", await cursor.fetchone())
        if row is None:
            return None
        return ExecutionEvidence(
            data_source_id=row["data_source_id"],
            cluster_id=row["cluster_id"],
            question_text=row["question_text"],
            validated_sql=row["validated_sql"],
            schema_fingerprint=row["schema_fingerprint"],
            recorded_at=row["recorded_at"],
        )
