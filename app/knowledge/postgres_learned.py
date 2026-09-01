"""PostgreSQL storage for the knowledge each new candidate type promotes into.

Every table is scoped by `data_source_id`, and the foreign keys are what make
that real rather than a convention: an alias points at a semantic entity, a join
rule at two semantic attributes, and neither can reference a row belonging to
another database.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.knowledge.learned import (
    ApprovedEntityAlias,
    ApprovedFilter,
    ApprovedJoinRule,
    ApprovedSynonym,
    DescriptionRevision,
    LearnedKnowledgeStore,
)


class PostgresLearnedKnowledgeStore(LearnedKnowledgeStore):
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def add_filter(self, item: ApprovedFilter) -> ApprovedFilter:
        await self._write(
            "INSERT INTO knowledge.approved_filters"
            " (id, data_source_id, name, description, predicate,"
            "  source_candidate_id, approved_by, approved_at)"
            " VALUES (%(id)s, %(data_source_id)s, %(name)s, %(description)s,"
            "  %(predicate)s, %(candidate)s, %(approved_by)s, %(approved_at)s)"
            " ON CONFLICT (data_source_id, name) DO UPDATE SET"
            "  description = EXCLUDED.description,"
            "  predicate = EXCLUDED.predicate,"
            "  approved_at = EXCLUDED.approved_at",
            {
                "id": item.id,
                "data_source_id": item.data_source_id,
                "name": item.name,
                "description": item.description,
                "predicate": Jsonb(item.predicate),
                "candidate": item.source_candidate_id,
                "approved_by": item.approved_by,
                "approved_at": item.approved_at,
            },
        )
        return item

    async def filters(self, data_source_id: UUID) -> list[ApprovedFilter]:
        rows = await self._read("approved_filters", data_source_id)
        return [
            ApprovedFilter(
                id=row["id"],
                data_source_id=row["data_source_id"],
                name=row["name"],
                description=row["description"],
                predicate=row["predicate"] or {},
                source_candidate_id=row["source_candidate_id"],
                approved_by=row["approved_by"],
                approved_at=row["approved_at"],
            )
            for row in rows
        ]

    async def add_synonym(self, item: ApprovedSynonym) -> ApprovedSynonym:
        await self._write(
            "INSERT INTO knowledge.approved_synonyms"
            " (id, data_source_id, target_kind, target, phrases,"
            "  source_candidate_id, approved_by, approved_at)"
            " VALUES (%(id)s, %(data_source_id)s, %(target_kind)s, %(target)s,"
            "  %(phrases)s, %(candidate)s, %(approved_by)s, %(approved_at)s)"
            " ON CONFLICT (data_source_id, target_kind, target) DO UPDATE SET"
            "  phrases = EXCLUDED.phrases,"
            "  approved_at = EXCLUDED.approved_at",
            {
                "id": item.id,
                "data_source_id": item.data_source_id,
                "target_kind": item.target_kind,
                "target": item.target,
                "phrases": list(item.phrases),
                "candidate": item.source_candidate_id,
                "approved_by": item.approved_by,
                "approved_at": item.approved_at,
            },
        )
        return item

    async def synonyms(self, data_source_id: UUID) -> list[ApprovedSynonym]:
        rows = await self._read("approved_synonyms", data_source_id)
        return [
            ApprovedSynonym(
                id=row["id"],
                data_source_id=row["data_source_id"],
                target_kind=row["target_kind"],
                target=row["target"],
                phrases=tuple(row["phrases"] or ()),
                source_candidate_id=row["source_candidate_id"],
                approved_by=row["approved_by"],
                approved_at=row["approved_at"],
            )
            for row in rows
        ]

    async def add_alias(self, item: ApprovedEntityAlias) -> ApprovedEntityAlias:
        await self._write(
            "INSERT INTO knowledge.approved_entity_aliases"
            " (id, data_source_id, entity_id, alias, canonical_key,"
            "  source_candidate_id, approved_by, approved_at)"
            " VALUES (%(id)s, %(data_source_id)s, %(entity_id)s, %(alias)s,"
            "  %(canonical_key)s, %(candidate)s, %(approved_by)s, %(approved_at)s)"
            " ON CONFLICT (data_source_id, entity_id, alias) DO UPDATE SET"
            "  canonical_key = EXCLUDED.canonical_key,"
            "  approved_at = EXCLUDED.approved_at",
            {
                "id": item.id,
                "data_source_id": item.data_source_id,
                "entity_id": item.entity_id,
                "alias": item.alias,
                "canonical_key": item.canonical_key,
                "candidate": item.source_candidate_id,
                "approved_by": item.approved_by,
                "approved_at": item.approved_at,
            },
        )
        return item

    async def aliases(self, data_source_id: UUID) -> list[ApprovedEntityAlias]:
        rows = await self._read("approved_entity_aliases", data_source_id)
        return [
            ApprovedEntityAlias(
                id=row["id"],
                data_source_id=row["data_source_id"],
                entity_id=row["entity_id"],
                alias=row["alias"],
                canonical_key=row["canonical_key"],
                source_candidate_id=row["source_candidate_id"],
                approved_by=row["approved_by"],
                approved_at=row["approved_at"],
            )
            for row in rows
        ]

    async def add_join_rule(self, item: ApprovedJoinRule) -> ApprovedJoinRule:
        await self._write(
            "INSERT INTO knowledge.approved_join_rules"
            " (id, data_source_id, left_attribute_id, right_attribute_id,"
            "  cardinality, source_candidate_id, approved_by, approved_at)"
            " VALUES (%(id)s, %(data_source_id)s, %(left_id)s, %(right_id)s,"
            "  %(cardinality)s, %(candidate)s, %(approved_by)s, %(approved_at)s)"
            " ON CONFLICT (data_source_id, left_attribute_id, right_attribute_id)"
            " DO UPDATE SET cardinality = EXCLUDED.cardinality,"
            "  approved_at = EXCLUDED.approved_at",
            {
                "id": item.id,
                "data_source_id": item.data_source_id,
                "left_id": item.left_attribute_id,
                "right_id": item.right_attribute_id,
                "cardinality": item.cardinality,
                "candidate": item.source_candidate_id,
                "approved_by": item.approved_by,
                "approved_at": item.approved_at,
            },
        )
        return item

    async def join_rules(self, data_source_id: UUID) -> list[ApprovedJoinRule]:
        rows = await self._read("approved_join_rules", data_source_id)
        return [
            ApprovedJoinRule(
                id=row["id"],
                data_source_id=row["data_source_id"],
                left_attribute_id=row["left_attribute_id"],
                right_attribute_id=row["right_attribute_id"],
                cardinality=row["cardinality"],
                source_candidate_id=row["source_candidate_id"],
                approved_by=row["approved_by"],
                approved_at=row["approved_at"],
            )
            for row in rows
        ]

    async def add_description(self, item: DescriptionRevision) -> DescriptionRevision:
        await self._write(
            "INSERT INTO knowledge.semantic_description_revisions"
            " (id, data_source_id, subject_kind, subject_id,"
            "  previous_description, description, source_candidate_id,"
            "  approved_by, approved_at)"
            " VALUES (%(id)s, %(data_source_id)s, %(subject_kind)s, %(subject_id)s,"
            "  %(previous)s, %(description)s, %(candidate)s, %(approved_by)s,"
            "  %(approved_at)s)",
            {
                "id": item.id,
                "data_source_id": item.data_source_id,
                "subject_kind": item.subject_kind,
                "subject_id": item.subject_id,
                "previous": item.previous_description,
                "description": item.description,
                "candidate": item.source_candidate_id,
                "approved_by": item.approved_by,
                "approved_at": item.approved_at,
            },
        )
        return item

    async def descriptions(self, data_source_id: UUID) -> list[DescriptionRevision]:
        rows = await self._read(
            "semantic_description_revisions", data_source_id, order="approved_at DESC"
        )
        return [
            DescriptionRevision(
                id=row["id"],
                data_source_id=row["data_source_id"],
                subject_kind=row["subject_kind"],
                subject_id=row["subject_id"],
                previous_description=row["previous_description"],
                description=row["description"],
                source_candidate_id=row["source_candidate_id"],
                approved_by=row["approved_by"],
                approved_at=row["approved_at"],
            )
            for row in rows
        ]

    async def _write(self, sql: str, parameters: dict[str, Any]) -> None:
        async with (
            self._pool.connection() as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(sql, parameters)

    async def _read(
        self, table: str, data_source_id: UUID, *, order: str = "approved_at"
    ) -> list[dict[str, Any]]:
        # The table name is a literal from this module, never a caller value.
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                f"SELECT * FROM knowledge.{table}"
                " WHERE data_source_id = %(data_source_id)s"
                f" ORDER BY {order}",
                {"data_source_id": data_source_id},
            )
            return cast("list[dict[str, Any]]", await cursor.fetchall())
