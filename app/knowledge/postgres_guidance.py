"""PostgreSQL-backed approved examples and business instructions.

Approval is a human decision, so it has to survive a restart. Relevance ranking
stays in Python: it is a small deterministic word overlap, and pushing it into
SQL would trade a readable rule for a less predictable one without changing what
the model is shown.

Authorization filtering also stays in Python, applied after the datasource-scoped
read, because it depends on the caller's authorized table set rather than on
anything stored with the example.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.knowledge.contracts import ApprovalStatus
from app.knowledge.guidance import (
    _MUTATING,
    ApprovedQueryExample,
    BusinessInstruction,
    GuidanceError,
    _overlap,
    _tables_in,
)

logger = logging.getLogger(__name__)


class PostgresGuidanceStore:
    """Reviewed reasoning context persisted in the knowledge database."""

    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    # -- approval -----------------------------------------------------------

    async def approve_example(
        self,
        example: ApprovedQueryExample,
        *,
        was_successful: bool,
        was_validated: bool,
        current_schema_fingerprint: str | None = None,
    ) -> ApprovedQueryExample:
        if not was_successful or not was_validated:
            raise GuidanceError(
                "Only a successful, validated request can become an approved example."
            )
        if _MUTATING.search(example.query_pattern):
            raise GuidanceError("An approved example must be a read-only statement.")
        if (
            current_schema_fingerprint is not None
            and example.schema_fingerprint is not None
            and example.schema_fingerprint != current_schema_fingerprint
        ):
            raise GuidanceError(
                "The example was validated against a different schema version."
            )
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "INSERT INTO knowledge.approved_query_examples"
                " (id, data_source_id, question, normalized_question,"
                "  semantic_plan, query_pattern, schema_fingerprint, status,"
                "  origin_query_id, source_cluster_id, approved_at)"
                " VALUES (%(id)s, %(data_source_id)s, %(question)s,"
                "  %(normalized)s, %(plan)s, %(pattern)s, %(fingerprint)s,"
                "  %(status)s, %(origin)s, %(cluster_id)s, %(approved_at)s)"
                " ON CONFLICT (id) DO NOTHING",
                {
                    "id": example.id,
                    "data_source_id": example.data_source_id,
                    "question": example.question,
                    "normalized": example.normalized_question,
                    "plan": example.semantic_plan,
                    "pattern": example.query_pattern,
                    "fingerprint": example.schema_fingerprint,
                    "status": example.status.value,
                    "origin": example.source_query_id,
                    "cluster_id": example.source_cluster_id,
                    "approved_at": example.approved_at,
                },
            )
        return example

    async def approve_instruction(
        self, instruction: BusinessInstruction
    ) -> BusinessInstruction:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "INSERT INTO knowledge.business_instructions"
                " (id, data_source_id, title, instruction, semantic_concepts,"
                "  metric_keys, status, source_candidate_id, schema_fingerprint,"
                "  approved_at)"
                " VALUES (%(id)s, %(data_source_id)s, %(title)s, %(instruction)s,"
                "  %(concepts)s, %(metric_keys)s, %(status)s, %(candidate)s,"
                "  %(fingerprint)s, %(approved_at)s)"
                " ON CONFLICT (data_source_id, title) DO UPDATE SET"
                "  instruction = EXCLUDED.instruction,"
                "  semantic_concepts = EXCLUDED.semantic_concepts,"
                "  metric_keys = EXCLUDED.metric_keys,"
                "  status = EXCLUDED.status,"
                "  updated_at = now()",
                {
                    "id": instruction.id,
                    "data_source_id": instruction.data_source_id,
                    "title": instruction.title,
                    "instruction": instruction.instruction,
                    "concepts": list(instruction.semantic_concepts),
                    "metric_keys": list(instruction.metric_keys),
                    "status": instruction.status.value,
                    "candidate": instruction.source_candidate_id,
                    "fingerprint": instruction.schema_fingerprint,
                    "approved_at": instruction.approved_at,
                },
            )
        return instruction

    # -- retrieval ----------------------------------------------------------

    async def relevant_examples(
        self,
        data_source_id: UUID,
        question: str,
        *,
        authorized_tables: frozenset[str] | None = None,
        limit: int = 3,
    ) -> list[ApprovedQueryExample]:
        usable = [
            example
            for example in await self.examples(data_source_id)
            if example.is_usable
            and (
                authorized_tables is None
                or _tables_in(example.query_pattern) <= authorized_tables
            )
        ]
        scored = [(_overlap(question, item.question), item) for item in usable]
        relevant = [(score, item) for score, item in scored if score > 0]
        relevant.sort(key=lambda pair: (-pair[0], pair[1].normalized_question))
        return [item for _, item in relevant[:limit]]

    async def relevant_instructions(
        self,
        data_source_id: UUID,
        question: str,
        *,
        metric_keys: frozenset[str] = frozenset(),
        limit: int = 3,
    ) -> list[BusinessInstruction]:
        matches: list[tuple[int, BusinessInstruction]] = []
        for instruction in await self.instructions(data_source_id):
            if not instruction.is_usable:
                continue
            score = 0
            if metric_keys & set(instruction.metric_keys):
                score += 10
            for concept in instruction.semantic_concepts:
                score += _overlap(question, concept)
            if score > 0:
                matches.append((score, instruction))
        matches.sort(key=lambda pair: (-pair[0], pair[1].title))
        return [instruction for _, instruction in matches[:limit]]

    # -- staleness ----------------------------------------------------------

    async def mark_stale_for_schema(
        self, data_source_id: UUID, *, new_schema_fingerprint: str
    ) -> int:
        """Mark only examples bound to a different schema version.

        Business instructions are untouched: what a figure means usually
        outlives a table change, and invalidating reviewed definitions because
        an unrelated table moved would destroy human work.
        """
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "UPDATE knowledge.approved_query_examples"
                " SET status = 'STALE', updated_at = now()"
                " WHERE data_source_id = %(data_source_id)s"
                "   AND status = 'CONFIRMED'"
                "   AND schema_fingerprint IS NOT NULL"
                "   AND schema_fingerprint <> %(fingerprint)s",
                {
                    "data_source_id": data_source_id,
                    "fingerprint": new_schema_fingerprint,
                },
            )
            return int(cursor.rowcount)

    # -- listing ------------------------------------------------------------

    async def examples(self, data_source_id: UUID) -> list[ApprovedQueryExample]:
        rows = await self._fetch(
            "SELECT id, data_source_id, question, semantic_plan, query_pattern,"
            " schema_fingerprint, status, origin_query_id, source_cluster_id,"
            " approved_at, created_at"
            " FROM knowledge.approved_query_examples"
            " WHERE data_source_id = %(data_source_id)s"
            " ORDER BY created_at DESC",
            data_source_id,
        )
        return [
            ApprovedQueryExample(
                id=row["id"],
                data_source_id=row["data_source_id"],
                question=row["question"],
                query_pattern=row["query_pattern"],
                semantic_plan=row["semantic_plan"],
                schema_fingerprint=row["schema_fingerprint"],
                status=ApprovalStatus(row["status"]),
                source_query_id=row["origin_query_id"],
                source_cluster_id=row["source_cluster_id"],
                approved_at=row["approved_at"] or row["created_at"],
            )
            for row in rows
        ]

    async def instructions(self, data_source_id: UUID) -> list[BusinessInstruction]:
        rows = await self._fetch(
            "SELECT id, data_source_id, title, instruction, semantic_concepts,"
            " metric_keys, status, source_candidate_id, schema_fingerprint,"
            " approved_at, created_at"
            " FROM knowledge.business_instructions"
            " WHERE data_source_id = %(data_source_id)s"
            " ORDER BY title",
            data_source_id,
        )
        return [
            BusinessInstruction(
                id=row["id"],
                data_source_id=row["data_source_id"],
                title=row["title"],
                instruction=row["instruction"],
                semantic_concepts=tuple(row["semantic_concepts"] or ()),
                metric_keys=tuple(row["metric_keys"] or ()),
                status=ApprovalStatus(row["status"]),
                schema_fingerprint=row["schema_fingerprint"],
                source_candidate_id=row["source_candidate_id"],
                approved_at=row["approved_at"] or row["created_at"],
            )
            for row in rows
        ]

    async def _fetch(
        self, query: str, data_source_id: UUID
    ) -> Sequence[dict[str, Any]]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(query, {"data_source_id": data_source_id})
            return cast("list[dict[str, Any]]", await cursor.fetchall())
