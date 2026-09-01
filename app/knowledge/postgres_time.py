"""PostgreSQL storage for one datasource's calendar and temporal columns.

Both outlive the process by necessity: a calendar someone confirmed is the kind
of fact that must survive a restart, and a temporal mapping is reviewed work.

Temporal dimensions are read back joined to their semantic attribute and entity,
so a caller building SQL already has the physical column and never has to look
it up a second time -- which is also what keeps the resolution of "which column
is this" in one place.
"""

from __future__ import annotations

from typing import Any, Protocol, cast
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.knowledge.contracts import ApprovalStatus
from app.timeintel.dimensions import (
    TemporalDimension,
    TemporalRole,
    TemporalStorage,
)
from app.timeintel.policy import (
    FiscalYearLabel,
    PolicyStatus,
    TimePolicy,
    WeekStart,
    default_policy,
)

_DIMENSION_SELECT = """
    SELECT t.id, t.data_source_id, t.semantic_attribute_id, t.role, t.storage,
           t.is_default_for_entity, t.status, t.schema_fingerprint,
           t.reviewed_by, t.created_at, t.updated_at,
           a.source_column, a.concept_name,
           e.source_schema, e.source_table, e.entity_name
    FROM knowledge.temporal_dimensions t
    JOIN knowledge.semantic_attributes a ON a.id = t.semantic_attribute_id
    JOIN knowledge.semantic_entities e ON e.id = a.entity_id
    WHERE t.data_source_id = %(data_source_id)s
    ORDER BY e.entity_name, a.concept_name
"""


class TimeIntelligenceStore(Protocol):
    async def policy(self, data_source_id: UUID) -> TimePolicy: ...

    async def save_policy(self, policy: TimePolicy) -> TimePolicy: ...

    async def dimensions(self, data_source_id: UUID) -> list[TemporalDimension]: ...

    async def save_dimension(
        self, dimension: TemporalDimension
    ) -> TemporalDimension: ...

    async def mark_dimensions_stale(
        self, data_source_id: UUID, *, attribute_ids: set[UUID]
    ) -> int: ...


class PostgresTimeIntelligenceStore(TimeIntelligenceStore):
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def policy(self, data_source_id: UUID) -> TimePolicy:
        """This datasource's calendar, or the documented default.

        Returning a DEFAULT policy rather than None keeps every caller on one
        path: calendar periods work, and a fiscal question refuses itself
        because the policy says nobody confirmed it.
        """
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT * FROM knowledge.time_policies"
                " WHERE data_source_id = %(data_source_id)s",
                {"data_source_id": data_source_id},
            )
            row = cast("dict[str, Any] | None", await cursor.fetchone())
        return _to_policy(row) if row is not None else default_policy(data_source_id)

    async def save_policy(self, policy: TimePolicy) -> TimePolicy:
        async with (
            self._pool.connection() as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(
                "INSERT INTO knowledge.time_policies"
                " (id, data_source_id, timezone, week_start, fiscal_year_start_month,"
                "  fiscal_year_start_day, fiscal_year_label, status, version,"
                "  updated_by, created_at, updated_at)"
                " VALUES (%(id)s, %(data_source_id)s, %(timezone)s, %(week_start)s,"
                "  %(month)s, %(day)s, %(label)s, %(status)s, %(version)s,"
                "  %(updated_by)s, %(created_at)s, now())"
                " ON CONFLICT (data_source_id) DO UPDATE SET"
                "  timezone = EXCLUDED.timezone,"
                "  week_start = EXCLUDED.week_start,"
                "  fiscal_year_start_month = EXCLUDED.fiscal_year_start_month,"
                "  fiscal_year_start_day = EXCLUDED.fiscal_year_start_day,"
                "  fiscal_year_label = EXCLUDED.fiscal_year_label,"
                "  status = EXCLUDED.status,"
                # A calendar change is a governance event: the version is what
                # lets an answer say which calendar produced it.
                "  version = knowledge.time_policies.version + 1,"
                "  updated_by = EXCLUDED.updated_by,"
                "  updated_at = now()",
                {
                    "id": policy.id,
                    "data_source_id": policy.data_source_id,
                    "timezone": policy.timezone,
                    "week_start": policy.week_start.value,
                    "month": policy.fiscal_year_start_month,
                    "day": policy.fiscal_year_start_day,
                    "label": policy.fiscal_year_label.value,
                    "status": policy.status.value,
                    "version": policy.version,
                    "updated_by": policy.updated_by,
                    "created_at": policy.created_at,
                },
            )
        return await self.policy(policy.data_source_id)

    async def dimensions(self, data_source_id: UUID) -> list[TemporalDimension]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                _DIMENSION_SELECT, {"data_source_id": data_source_id}
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        return [_to_dimension(row) for row in rows]

    async def save_dimension(self, dimension: TemporalDimension) -> TemporalDimension:
        async with (
            self._pool.connection() as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(
                "INSERT INTO knowledge.temporal_dimensions"
                " (id, data_source_id, semantic_attribute_id, role, storage,"
                "  is_default_for_entity, status, schema_fingerprint, reviewed_by,"
                "  created_at, updated_at)"
                " VALUES (%(id)s, %(data_source_id)s, %(attribute_id)s, %(role)s,"
                "  %(storage)s, %(is_default)s, %(status)s, %(fingerprint)s,"
                "  %(reviewed_by)s, %(created_at)s, now())"
                " ON CONFLICT (data_source_id, semantic_attribute_id) DO UPDATE SET"
                "  role = EXCLUDED.role,"
                "  storage = EXCLUDED.storage,"
                "  is_default_for_entity = EXCLUDED.is_default_for_entity,"
                "  status = EXCLUDED.status,"
                "  schema_fingerprint = EXCLUDED.schema_fingerprint,"
                "  reviewed_by = EXCLUDED.reviewed_by,"
                "  updated_at = now()",
                {
                    "id": dimension.id,
                    "data_source_id": dimension.data_source_id,
                    "attribute_id": dimension.semantic_attribute_id,
                    "role": dimension.role.value,
                    "storage": dimension.storage.value,
                    "is_default": dimension.is_default_for_entity,
                    "status": dimension.status.value,
                    "fingerprint": dimension.schema_fingerprint,
                    "reviewed_by": dimension.reviewed_by,
                    "created_at": dimension.created_at,
                },
            )
        stored = await self.dimensions(dimension.data_source_id)
        return next(
            (
                item
                for item in stored
                if item.semantic_attribute_id == dimension.semantic_attribute_id
            ),
            dimension,
        )

    async def mark_dimensions_stale(
        self, data_source_id: UUID, *, attribute_ids: set[UUID]
    ) -> int:
        """Invalidate only the mappings whose attribute actually changed.

        Marking every temporal mapping stale because an unrelated table moved
        would destroy reviewed work and teach reviewers to re-confirm without
        looking.
        """
        if not attribute_ids:
            return 0
        async with (
            self._pool.connection() as connection,
            connection.cursor() as cursor,
        ):
            await cursor.execute(
                "UPDATE knowledge.temporal_dimensions SET status = 'STALE',"
                " updated_at = now()"
                " WHERE data_source_id = %(data_source_id)s"
                "   AND status = 'CONFIRMED'"
                "   AND semantic_attribute_id = ANY(%(attribute_ids)s)",
                {
                    "data_source_id": data_source_id,
                    "attribute_ids": list(attribute_ids),
                },
            )
            return int(cursor.rowcount)


class InMemoryTimeIntelligenceStore(TimeIntelligenceStore):
    """Development storage, datasource-scoped like the persistent one."""

    def __init__(self) -> None:
        self._policies: dict[UUID, TimePolicy] = {}
        self._dimensions: dict[UUID, list[TemporalDimension]] = {}

    async def policy(self, data_source_id: UUID) -> TimePolicy:
        return self._policies.get(data_source_id) or default_policy(data_source_id)

    async def save_policy(self, policy: TimePolicy) -> TimePolicy:
        self._policies[policy.data_source_id] = policy
        return policy

    async def dimensions(self, data_source_id: UUID) -> list[TemporalDimension]:
        return list(self._dimensions.get(data_source_id, []))

    async def save_dimension(self, dimension: TemporalDimension) -> TemporalDimension:
        existing = self._dimensions.setdefault(dimension.data_source_id, [])
        remaining = [
            item
            for item in existing
            if item.semantic_attribute_id != dimension.semantic_attribute_id
        ]
        remaining.append(dimension)
        self._dimensions[dimension.data_source_id] = remaining
        return dimension

    async def mark_dimensions_stale(
        self, data_source_id: UUID, *, attribute_ids: set[UUID]
    ) -> int:
        from dataclasses import replace as dataclass_replace

        updated = 0
        refreshed: list[TemporalDimension] = []
        for item in self._dimensions.get(data_source_id, []):
            if (
                item.semantic_attribute_id in attribute_ids
                and item.status is ApprovalStatus.CONFIRMED
            ):
                refreshed.append(dataclass_replace(item, status=ApprovalStatus.STALE))
                updated += 1
            else:
                refreshed.append(item)
        self._dimensions[data_source_id] = refreshed
        return updated


def _to_policy(row: dict[str, Any]) -> TimePolicy:
    return TimePolicy(
        id=row["id"],
        data_source_id=row["data_source_id"],
        timezone=row["timezone"],
        week_start=WeekStart(row["week_start"]),
        fiscal_year_start_month=row["fiscal_year_start_month"],
        fiscal_year_start_day=row["fiscal_year_start_day"],
        fiscal_year_label=FiscalYearLabel(row["fiscal_year_label"]),
        status=PolicyStatus(row["status"]),
        version=row["version"],
        updated_by=row["updated_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _to_dimension(row: dict[str, Any]) -> TemporalDimension:
    return TemporalDimension(
        id=row["id"],
        data_source_id=row["data_source_id"],
        semantic_attribute_id=row["semantic_attribute_id"],
        role=TemporalRole(row["role"]),
        storage=TemporalStorage(row["storage"]),
        schema_name=row["source_schema"],
        table_name=row["source_table"],
        column_name=row["source_column"],
        concept_name=row["concept_name"],
        entity_name=row["entity_name"],
        is_default_for_entity=row["is_default_for_entity"],
        status=ApprovalStatus(row["status"]),
        schema_fingerprint=row["schema_fingerprint"],
        reviewed_by=row["reviewed_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
