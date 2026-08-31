"""PostgreSQL-backed semantic model persistence.

One source of truth. The review screens and `EntityResolver` read the same rows,
so a mapping a reviewer confirmed is immediately the mapping runtime resolves
against — there is no separate "review state" that can drift from "runtime
state".

Writes are whole-model: `save` replaces one datasource's proposals and
confirmations in a single transaction. A semantic model is small and internally
consistent, and a partial write could leave an attribute pointing at an entity
that no longer exists.
"""

from __future__ import annotations

import logging
from typing import Any, cast
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.knowledge.contracts import (
    ApprovalStatus,
    SemanticAttribute,
    SemanticEntity,
    SemanticRelationship,
)
from app.knowledge.discovery import SemanticModel

logger = logging.getLogger(__name__)


class PostgresSemanticRepository:
    """Persisted semantic entities, attributes, and relationships."""

    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def save(self, model: SemanticModel) -> None:
        """Replace one datasource's semantic model, atomically.

        Deletion is scoped to the datasource, so saving one database's model
        can never disturb another's.
        """
        async with (
            self._pool.connection() as connection,
            connection.transaction(),
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            # Children first: composite foreign keys make the order matter.
            for table in (
                "semantic_relationships",
                "semantic_attributes",
                "semantic_entities",
            ):
                await cursor.execute(
                    f"DELETE FROM knowledge.{table}"
                    " WHERE data_source_id = %(data_source_id)s",
                    {"data_source_id": model.data_source_id},
                )
            for entity in model.entities:
                await cursor.execute(
                    "INSERT INTO knowledge.semantic_entities"
                    " (id, data_source_id, source_schema, source_table,"
                    "  entity_name, description, confidence, reason_code,"
                    "  status, schema_fingerprint)"
                    " VALUES (%(id)s, %(data_source_id)s, %(schema)s, %(table)s,"
                    "  %(name)s, %(description)s, %(confidence)s, %(reason)s,"
                    "  %(status)s, %(fingerprint)s)",
                    {
                        "id": entity.id,
                        "data_source_id": entity.data_source_id,
                        "schema": entity.source_schema,
                        "table": entity.source_table,
                        "name": entity.entity_name,
                        "description": entity.description,
                        "confidence": entity.confidence,
                        "reason": entity.reason_code,
                        "status": entity.status.value,
                        "fingerprint": entity.schema_fingerprint,
                    },
                )
            for attribute in model.attributes:
                await cursor.execute(
                    "INSERT INTO knowledge.semantic_attributes"
                    " (id, data_source_id, entity_id, source_column,"
                    "  concept_name, description, data_type, is_identifier,"
                    "  confidence, status)"
                    " VALUES (%(id)s, %(data_source_id)s, %(entity_id)s,"
                    "  %(column)s, %(concept)s, %(description)s, %(data_type)s,"
                    "  %(is_identifier)s, %(confidence)s, %(status)s)",
                    {
                        "id": attribute.id,
                        "data_source_id": attribute.data_source_id,
                        "entity_id": attribute.entity_id,
                        "column": attribute.source_column,
                        "concept": attribute.concept_name,
                        "description": attribute.description,
                        "data_type": attribute.data_type,
                        "is_identifier": attribute.is_identifier,
                        "confidence": attribute.confidence,
                        "status": attribute.status.value,
                    },
                )
            for relationship in model.relationships:
                await cursor.execute(
                    "INSERT INTO knowledge.semantic_relationships"
                    " (id, data_source_id, from_entity_id, to_entity_id,"
                    "  from_column, to_column, relationship_name, cardinality,"
                    "  confidence, status)"
                    " VALUES (%(id)s, %(data_source_id)s, %(from_entity)s,"
                    "  %(to_entity)s, %(from_column)s, %(to_column)s, %(name)s,"
                    "  %(cardinality)s, %(confidence)s, %(status)s)",
                    {
                        "id": relationship.id,
                        "data_source_id": relationship.data_source_id,
                        "from_entity": relationship.from_entity_id,
                        "to_entity": relationship.to_entity_id,
                        "from_column": relationship.from_column,
                        "to_column": relationship.to_column,
                        "name": relationship.relationship_name,
                        "cardinality": relationship.cardinality,
                        "confidence": relationship.confidence,
                        "status": relationship.status.value,
                    },
                )

    async def load(
        self, data_source_id: UUID, *, schema_fingerprint: str = ""
    ) -> SemanticModel:
        """The persisted model for one datasource.

        Returns an empty model rather than raising when nothing was discovered
        yet: an un-onboarded datasource is a normal state, not an error.
        """
        entities = [
            SemanticEntity(
                id=row["id"],
                data_source_id=row["data_source_id"],
                source_schema=row["source_schema"],
                source_table=row["source_table"],
                entity_name=row["entity_name"],
                description=row["description"],
                confidence=row["confidence"],
                reason_code=row["reason_code"],
                status=ApprovalStatus(row["status"]),
                schema_fingerprint=row["schema_fingerprint"],
            )
            for row in await self._fetch(
                "SELECT id, data_source_id, source_schema, source_table,"
                " entity_name, description, confidence, reason_code, status,"
                " schema_fingerprint FROM knowledge.semantic_entities"
                " WHERE data_source_id = %(data_source_id)s"
                " ORDER BY source_table",
                data_source_id,
            )
        ]
        attributes = [
            SemanticAttribute(
                id=row["id"],
                data_source_id=row["data_source_id"],
                entity_id=row["entity_id"],
                source_column=row["source_column"],
                concept_name=row["concept_name"],
                description=row["description"],
                data_type=row["data_type"],
                is_identifier=row["is_identifier"],
                confidence=row["confidence"],
                status=ApprovalStatus(row["status"]),
            )
            for row in await self._fetch(
                "SELECT id, data_source_id, entity_id, source_column,"
                " concept_name, description, data_type, is_identifier,"
                " confidence, status FROM knowledge.semantic_attributes"
                " WHERE data_source_id = %(data_source_id)s"
                " ORDER BY source_column",
                data_source_id,
            )
        ]
        relationships = [
            SemanticRelationship(
                id=row["id"],
                data_source_id=row["data_source_id"],
                from_entity_id=row["from_entity_id"],
                to_entity_id=row["to_entity_id"],
                from_column=row["from_column"],
                to_column=row["to_column"],
                relationship_name=row["relationship_name"],
                cardinality=row["cardinality"],
                confidence=row["confidence"],
                status=ApprovalStatus(row["status"]),
            )
            for row in await self._fetch(
                "SELECT id, data_source_id, from_entity_id, to_entity_id,"
                " from_column, to_column, relationship_name, cardinality,"
                " confidence, status FROM knowledge.semantic_relationships"
                " WHERE data_source_id = %(data_source_id)s"
                " ORDER BY relationship_name",
                data_source_id,
            )
        ]
        fingerprint = schema_fingerprint or next(
            (
                entity.schema_fingerprint
                for entity in entities
                if entity.schema_fingerprint
            ),
            "",
        )
        return SemanticModel(
            data_source_id=data_source_id,
            schema_fingerprint=fingerprint,
            entities=tuple(entities),
            attributes=tuple(attributes),
            relationships=tuple(relationships),
        )

    async def _fetch(
        self, query: str, data_source_id: UUID
    ) -> list[dict[str, Any]]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(query, {"data_source_id": data_source_id})
            return cast("list[dict[str, Any]]", await cursor.fetchall())
