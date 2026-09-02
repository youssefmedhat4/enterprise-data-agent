"""PostgreSQL-backed governed metric registry.

Runtime authority for governed metric definitions. `InMemoryMetricRegistry`
remains the reference implementation for tests and single-process runs; this is
what a deployed instance reads.

Isolation is structural rather than conventional. Every statement here is
parameterised by `data_source_id` and every table carries that column, so a
lookup cannot reach another datasource's metric even if a caller passes the
wrong key. The database enforces the same rule through composite foreign keys,
so a child row can never point at a parent in a different datasource.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import AsyncConnectionPool

from app.knowledge.metrics import (
    MetricDimensionSpec,
    MetricStatus,
    RegisteredMetric,
    TemporalBehavior,
)


class MetricRegistryError(RuntimeError):
    """Raised when a registry operation cannot be completed."""


_SELECT_METRIC = """
    SELECT id, data_source_id, metric_key, display_name, description,
           business_meaning, version, status, semantic_expression, grain,
           unit, null_behavior, owner, temporal_behavior,
           temporal_dimension_id, approved_at, approved_by, source_candidate_id
      FROM knowledge.metric_definitions
     WHERE data_source_id = %(data_source_id)s
"""

_UPSERT_METRIC = """
    INSERT INTO knowledge.metric_definitions
        (id, data_source_id, metric_key, display_name, description,
         business_meaning, version, status, semantic_expression, grain,
         unit, null_behavior, owner, temporal_behavior,
         temporal_dimension_id, approved_at, approved_by, source_candidate_id)
    VALUES
        (%(id)s, %(data_source_id)s, %(metric_key)s, %(display_name)s,
         %(description)s, %(business_meaning)s, %(version)s, %(status)s,
         %(semantic_expression)s, %(grain)s, %(unit)s, %(null_behavior)s,
         %(owner)s, %(temporal_behavior)s, %(temporal_dimension_id)s,
         %(approved_at)s, %(approved_by)s, %(source_candidate_id)s)
    ON CONFLICT (data_source_id, metric_key, version)
    DO UPDATE SET
        display_name = EXCLUDED.display_name,
        description = EXCLUDED.description,
        business_meaning = EXCLUDED.business_meaning,
        status = EXCLUDED.status,
        semantic_expression = EXCLUDED.semantic_expression,
        grain = EXCLUDED.grain,
        unit = EXCLUDED.unit,
        null_behavior = EXCLUDED.null_behavior,
        owner = EXCLUDED.owner,
        temporal_behavior = EXCLUDED.temporal_behavior,
        temporal_dimension_id = EXCLUDED.temporal_dimension_id,
        approved_at = EXCLUDED.approved_at,
        approved_by = EXCLUDED.approved_by,
        source_candidate_id = EXCLUDED.source_candidate_id,
        updated_at = now()
    RETURNING id
"""

#: `status` is bound twice, once against the enum column and once compared as
#: text. Without the explicit casts PostgreSQL cannot deduce one type for the
#: parameter and rejects the statement as ambiguous.
_SET_STATUS = """
    UPDATE knowledge.metric_definitions
       SET status = %(status)s::knowledge.metric_status,
           approved_by = COALESCE(%(approved_by)s, approved_by),
           approved_at = CASE WHEN %(status)s::text = 'CERTIFIED'
                              THEN now() ELSE approved_at END,
           updated_at = now()
     WHERE data_source_id = %(data_source_id)s
       AND metric_key = %(metric_key)s
"""


class PostgresMetricRegistry:
    """Datasource-scoped metric storage backed by the internal database."""

    def __init__(self, pool: AsyncConnectionPool[AsyncConnection[Any]]) -> None:
        self._pool = pool

    async def certified(self, data_source_id: UUID) -> list[RegisteredMetric]:
        """Every CERTIFIED metric for one datasource.

        Filtering happens in SQL rather than in Python so a PROPOSED, REJECTED,
        DEPRECATED or STALE definition is never even loaded into a process that
        is about to answer a live question.
        """
        return await self._load(
            _SELECT_METRIC + " AND status = 'CERTIFIED' ORDER BY metric_key",
            {"data_source_id": data_source_id},
        )

    async def get(
        self, data_source_id: UUID, metric_key: str
    ) -> RegisteredMetric | None:
        metrics = await self._load(
            _SELECT_METRIC
            + " AND metric_key = %(metric_key)s ORDER BY version DESC LIMIT 1",
            {"data_source_id": data_source_id, "metric_key": metric_key},
        )
        return metrics[0] if metrics else None

    async def upsert(self, metric: RegisteredMetric) -> RegisteredMetric:
        """Insert or replace one metric and its children, atomically.

        Children are deleted and rewritten rather than diffed: a metric
        definition is small, and a partial update that left a stale dimension
        behind would silently widen what a governed query may group by.
        """
        async with (
            self._pool.connection() as connection,
            connection.transaction(),
            connection.cursor(row_factory=tuple_row) as cursor,
        ):
            await cursor.execute(
                _UPSERT_METRIC,
                {
                    "id": metric.id,
                    "data_source_id": metric.data_source_id,
                    "metric_key": metric.metric_key,
                    "display_name": metric.display_name,
                    "description": metric.description,
                    "business_meaning": metric.business_meaning,
                    "version": metric.version,
                    "status": metric.status.value,
                    "semantic_expression": metric.semantic_expression,
                    "grain": metric.grain,
                    "unit": metric.unit,
                    "null_behavior": metric.null_behavior,
                    "owner": metric.owner,
                    "temporal_behavior": metric.temporal_behavior.value,
                    "temporal_dimension_id": metric.temporal_dimension_id,
                    "approved_at": metric.approved_at,
                    "approved_by": metric.approved_by,
                    "source_candidate_id": metric.source_candidate_id,
                },
            )
            row = await cursor.fetchone()
            if row is None:  # pragma: no cover - RETURNING always yields
                raise MetricRegistryError("Metric upsert returned no id.")
            await self._replace_children(cursor, metric, cast(UUID, row[0]))

        stored = await self.get(metric.data_source_id, metric.metric_key)
        if stored is None:  # pragma: no cover - just written
            raise MetricRegistryError("Metric disappeared immediately after upsert.")
        return stored

    async def set_status(
        self,
        data_source_id: UUID,
        metric_key: str,
        status: MetricStatus,
        *,
        approved_by: str | None = None,
    ) -> RegisteredMetric:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=tuple_row) as cursor,
        ):
            await cursor.execute(
                _SET_STATUS,
                {
                    "status": status.value,
                    "approved_by": approved_by,
                    "data_source_id": data_source_id,
                    "metric_key": metric_key,
                },
            )
            if cursor.rowcount == 0:
                raise MetricRegistryError(
                    f"No metric {metric_key!r} in this datasource to update."
                )
        stored = await self.get(data_source_id, metric_key)
        if stored is None:  # pragma: no cover - just updated
            raise MetricRegistryError("Metric disappeared immediately after update.")
        return stored

    async def _replace_children(
        self,
        cursor: Any,
        metric: RegisteredMetric,
        metric_id: UUID,
    ) -> None:
        scope = {"metric_id": metric_id, "data_source_id": metric.data_source_id}
        for table in ("metric_dimensions", "metric_concepts", "metric_dependencies"):
            await cursor.execute(
                "DELETE FROM knowledge."
                + table
                + " WHERE metric_id = %(metric_id)s"
                " AND data_source_id = %(data_source_id)s",
                scope,
            )
        for dimension in metric.dimensions:
            await cursor.execute(
                "INSERT INTO knowledge.metric_dimensions"
                " (data_source_id, metric_id, dimension_key, display_name,"
                "  description, data_type, is_time_dimension,"
                "  allowed_operators, semantic_attribute_id)"
                " VALUES (%(data_source_id)s, %(metric_id)s, %(dimension_key)s,"
                "  %(display_name)s, %(description)s, %(data_type)s,"
                "  %(is_time_dimension)s, %(allowed_operators)s,"
                "  %(semantic_attribute_id)s)",
                {
                    **scope,
                    "dimension_key": dimension.dimension_key,
                    "display_name": dimension.display_name,
                    "description": dimension.description,
                    "data_type": dimension.data_type,
                    "is_time_dimension": dimension.is_time_dimension,
                    "allowed_operators": list(dimension.allowed_operators),
                    "semantic_attribute_id": dimension.semantic_attribute_id,
                },
            )
        for concept in metric.concepts:
            await cursor.execute(
                "INSERT INTO knowledge.metric_concepts"
                " (data_source_id, metric_id, concept)"
                " VALUES (%(data_source_id)s, %(metric_id)s, %(concept)s)",
                {**scope, "concept": concept},
            )
        for dependency in metric.dependencies:
            await cursor.execute(
                "INSERT INTO knowledge.metric_dependencies"
                " (data_source_id, metric_id, depends_on_metric_key)"
                " VALUES (%(data_source_id)s, %(metric_id)s, %(depends_on)s)",
                {**scope, "depends_on": dependency},
            )

    async def _load(self, query: str, params: dict[str, Any]) -> list[RegisteredMetric]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(query, params)
            definitions = await cursor.fetchall()
            if not definitions:
                return []
            ids = [definition["id"] for definition in definitions]
            children = await self._load_children(cursor, ids)
        return [_to_metric(definition, children) for definition in definitions]

    async def _load_children(
        self, cursor: Any, metric_ids: list[UUID]
    ) -> dict[str, dict[UUID, list[Any]]]:
        dimensions: dict[UUID, list[Any]] = {}
        await cursor.execute(
            "SELECT metric_id, dimension_key, display_name, description,"
            " data_type, is_time_dimension, allowed_operators,"
            " semantic_attribute_id"
            " FROM knowledge.metric_dimensions WHERE metric_id = ANY(%s)"
            " ORDER BY dimension_key",
            (metric_ids,),
        )
        for row in await cursor.fetchall():
            dimensions.setdefault(row["metric_id"], []).append(
                MetricDimensionSpec(
                    dimension_key=row["dimension_key"],
                    display_name=row["display_name"],
                    description=row["description"],
                    data_type=row["data_type"],
                    is_time_dimension=row["is_time_dimension"],
                    allowed_operators=tuple(row["allowed_operators"]),
                    semantic_attribute_id=row["semantic_attribute_id"],
                )
            )

        concepts: dict[UUID, list[Any]] = {}
        await cursor.execute(
            "SELECT metric_id, concept FROM knowledge.metric_concepts"
            " WHERE metric_id = ANY(%s) ORDER BY concept",
            (metric_ids,),
        )
        for row in cast("list[dict[str, Any]]", await cursor.fetchall()):
            concepts.setdefault(row["metric_id"], []).append(row["concept"])

        dependencies: dict[UUID, list[Any]] = {}
        await cursor.execute(
            "SELECT metric_id, depends_on_metric_key"
            " FROM knowledge.metric_dependencies"
            " WHERE metric_id = ANY(%s) ORDER BY depends_on_metric_key",
            (metric_ids,),
        )
        for row in cast("list[dict[str, Any]]", await cursor.fetchall()):
            dependencies.setdefault(row["metric_id"], []).append(
                row["depends_on_metric_key"]
            )

        return {
            "dimensions": dimensions,
            "concepts": concepts,
            "dependencies": dependencies,
        }


def _to_metric(
    definition: dict[str, Any],
    children: dict[str, dict[UUID, list[Any]]],
) -> RegisteredMetric:
    metric_id = definition["id"]
    return RegisteredMetric(
        id=metric_id,
        data_source_id=definition["data_source_id"],
        metric_key=definition["metric_key"],
        display_name=definition["display_name"],
        description=definition["description"],
        business_meaning=definition["business_meaning"],
        version=definition["version"],
        status=MetricStatus(definition["status"]),
        semantic_expression=definition["semantic_expression"],
        grain=definition["grain"],
        unit=definition["unit"],
        null_behavior=definition["null_behavior"],
        owner=definition["owner"],
        temporal_behavior=TemporalBehavior(definition["temporal_behavior"]),
        temporal_dimension_id=definition["temporal_dimension_id"],
        dimensions=tuple(children["dimensions"].get(metric_id, [])),
        concepts=tuple(children["concepts"].get(metric_id, [])),
        dependencies=tuple(children["dependencies"].get(metric_id, [])),
        approved_at=definition["approved_at"],
        approved_by=definition["approved_by"],
        source_candidate_id=definition["source_candidate_id"],
    )
