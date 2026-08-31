import asyncio
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic, perf_counter
from typing import Any, cast

import asyncpg

from app.config import Settings
from app.data.gateway import (
    ColumnMetadata,
    DatabaseExecutionMetadata,
    DatabaseGateway,
    DatabasePermissionError,
    DatabaseQueryExecutionError,
    DatabaseQueryResult,
    DatabaseQueryTimeoutError,
    DatabaseReadOnlyConfigurationError,
    DatabaseSource,
    DatabaseUnavailableError,
    ForeignKeyMetadata,
    ResultColumnMetadata,
    TableMetadata,
)
from app.data.result_bounds import bounded_rows
from app.security.sql_validation import SQLValidator

SCHEMA_QUERY = """
SELECT
    namespace.nspname AS schema_name,
    relation.relname AS relation_name,
    relation.relkind,
    COALESCE(obj_description(relation.oid, 'pg_class'), '') AS relation_description,
    attribute.attname AS column_name,
    format_type(attribute.atttypid, attribute.atttypmod) AS data_type,
    NOT attribute.attnotnull AS nullable,
    COALESCE(col_description(relation.oid, attribute.attnum), '') AS column_description,
    type.typtype AS type_kind,
    COALESCE((
        SELECT key.ordinal
        FROM pg_constraint primary_key
        CROSS JOIN unnest(primary_key.conkey) WITH ORDINALITY key(attnum, ordinal)
        WHERE primary_key.conrelid = relation.oid
          AND primary_key.contype = 'p'
          AND key.attnum = attribute.attnum
    ), 0) AS primary_key_ordinal
FROM pg_class relation
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
JOIN pg_attribute attribute ON attribute.attrelid = relation.oid
JOIN pg_type type ON type.oid = attribute.atttypid
WHERE namespace.nspname = ANY($1::text[])
  AND relation.relkind = ANY(ARRAY['r', 'p', 'v', 'm', 'f']::char[])
  AND attribute.attnum > 0
  AND NOT attribute.attisdropped
ORDER BY namespace.nspname, relation.relname, attribute.attnum
"""

FOREIGN_KEY_QUERY = """
SELECT
    source_namespace.nspname AS source_schema,
    source_relation.relname AS source_table,
    ARRAY(
        SELECT source_attribute.attname
        FROM unnest(constraint_row.conkey) WITH ORDINALITY key(attnum, ordinal)
        JOIN pg_attribute source_attribute
          ON source_attribute.attrelid = source_relation.oid
         AND source_attribute.attnum = key.attnum
        ORDER BY key.ordinal
    ) AS source_columns,
    target_namespace.nspname AS target_schema,
    target_relation.relname AS target_table,
    ARRAY(
        SELECT target_attribute.attname
        FROM unnest(constraint_row.confkey) WITH ORDINALITY key(attnum, ordinal)
        JOIN pg_attribute target_attribute
          ON target_attribute.attrelid = target_relation.oid
         AND target_attribute.attnum = key.attnum
        ORDER BY key.ordinal
    ) AS target_columns
FROM pg_constraint constraint_row
JOIN pg_class source_relation ON source_relation.oid = constraint_row.conrelid
JOIN pg_namespace source_namespace ON source_namespace.oid = source_relation.relnamespace
JOIN pg_class target_relation ON target_relation.oid = constraint_row.confrelid
JOIN pg_namespace target_namespace ON target_namespace.oid = target_relation.relnamespace
WHERE constraint_row.contype = 'f'
  AND source_namespace.nspname = ANY($1::text[])
ORDER BY source_namespace.nspname, source_relation.relname, constraint_row.conname
"""

READ_ONLY_QUERY = """
SELECT
    current_setting('default_transaction_read_only') = 'on' AS default_read_only,
    role.rolsuper AS superuser,
    EXISTS (
        SELECT 1
        FROM pg_namespace namespace
        WHERE namespace.nspname = ANY($1::text[])
          AND has_schema_privilege(current_user, namespace.oid, 'CREATE')
    ) AS can_create_in_schema,
    EXISTS (
        SELECT 1
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = ANY($1::text[])
          AND relation.relkind = ANY(ARRAY['r', 'p', 'v', 'm', 'f']::char[])
          AND (
              has_table_privilege(current_user, relation.oid, 'INSERT')
              OR has_table_privilege(current_user, relation.oid, 'UPDATE')
              OR has_table_privilege(current_user, relation.oid, 'DELETE')
              OR has_table_privilege(current_user, relation.oid, 'TRUNCATE')
              OR has_table_privilege(current_user, relation.oid, 'TRIGGER')
          )
    ) AS can_mutate_relation
FROM pg_roles role
WHERE role.rolname = current_user
"""

RELATION_TYPES = {
    "r": "table",
    "p": "partitioned_table",
    "v": "view",
    "m": "materialized_view",
    "f": "foreign_table",
}


class PostgresDatabaseGateway(DatabaseGateway):
    def __init__(self, settings: Settings) -> None:
        self._database_url = str(settings.database_url)
        self._database_name = (settings.database_url.path or "").lstrip("/") or "postgres"
        self._timeout = settings.query_timeout_seconds
        self._max_rows = settings.query_row_limit
        self._max_result_bytes = settings.query_max_result_bytes
        self._allowed_schemas = settings.database_allowed_schemas
        self._categorical_columns = settings.database_categorical_columns
        self._sample_columns = settings.database_sample_columns
        self._categorical_max_values = settings.database_categorical_max_values
        self._categorical_max_value_length = settings.database_categorical_max_value_length
        self._categorical_max_columns = settings.database_categorical_max_columns
        self._require_read_only = settings.database_require_read_only
        self._pool_min_size = settings.database_pool_min_size
        self._pool_max_size = settings.database_pool_max_size
        self._connect_timeout = settings.database_connect_timeout_seconds
        self._schema_cache_seconds = settings.database_schema_cache_seconds
        self._schema_cache: list[TableMetadata] | None = None
        self._schema_cached_at = 0.0
        self._schema_lock = asyncio.Lock()
        self._pool: asyncpg.Pool | None = None

    def source(self) -> DatabaseSource:
        return DatabaseSource(
            identifier=f"postgres:{self._database_name}",
            dialect="postgres",
            provider="postgres",
        )

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(
                    dsn=self._database_url,
                    min_size=self._pool_min_size,
                    max_size=self._pool_max_size,
                    timeout=self._connect_timeout,
                    command_timeout=self._timeout,
                    server_settings={"application_name": "enterprise-data-agent"},
                    init=self._initialize_connection,
                )
            except DatabaseReadOnlyConfigurationError:
                raise
            except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
                raise DatabaseUnavailableError("PostgreSQL is unavailable.") from exc
        return self._pool

    async def _initialize_connection(self, connection: asyncpg.Connection) -> None:
        if not self._require_read_only:
            return
        verification = await connection.fetchrow(
            READ_ONLY_QUERY,
            list(self._allowed_schemas),
            timeout=self._timeout,
        )
        if verification is None or not verification["default_read_only"]:
            raise DatabaseReadOnlyConfigurationError(
                "The PostgreSQL role is not configured read-only."
            )
        if any(
            bool(verification[field])
            for field in ("superuser", "can_create_in_schema", "can_mutate_relation")
        ):
            raise DatabaseReadOnlyConfigurationError(
                "The PostgreSQL role has write-capable privileges."
            )

    async def health_check(self) -> bool:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as connection:
                return bool(await connection.fetchval("SELECT true", timeout=self._timeout))
        except DatabaseReadOnlyConfigurationError:
            raise
        except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
            raise DatabaseUnavailableError("PostgreSQL is unavailable.") from exc

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        if self._schema_cache_valid():
            return list(self._schema_cache or [])
        async with self._schema_lock:
            if self._schema_cache_valid():
                return list(self._schema_cache or [])
            metadata = await self._discover_schema()
            self._schema_cache = metadata
            self._schema_cached_at = monotonic()
            return list(metadata)

    def _schema_cache_valid(self) -> bool:
        return self._schema_cache is not None and (
            self._schema_cache_seconds > 0
            and monotonic() - self._schema_cached_at < self._schema_cache_seconds
        )

    async def _discover_schema(self) -> list[TableMetadata]:
        try:
            pool = await self._get_pool()
            async with (
                pool.acquire() as connection,
                connection.transaction(readonly=True),
            ):
                column_rows = await connection.fetch(
                    SCHEMA_QUERY,
                    list(self._allowed_schemas),
                    timeout=self._timeout,
                )
                foreign_key_rows = await connection.fetch(
                    FOREIGN_KEY_QUERY,
                    list(self._allowed_schemas),
                    timeout=self._timeout,
                )
                metadata = _build_table_metadata(column_rows, foreign_key_rows)
                return await self._add_observed_values(connection, metadata)
        except DatabaseReadOnlyConfigurationError:
            raise
        except (asyncpg.QueryCanceledError, TimeoutError) as exc:
            raise DatabaseQueryTimeoutError("PostgreSQL schema discovery timed out.") from exc
        except asyncpg.InsufficientPrivilegeError as exc:
            raise DatabasePermissionError(
                "The PostgreSQL role cannot inspect configured schemas."
            ) from exc
        except asyncpg.PostgresConnectionError as exc:
            raise DatabaseUnavailableError("PostgreSQL is unavailable.") from exc
        except asyncpg.PostgresError as exc:
            raise DatabaseQueryExecutionError("PostgreSQL schema discovery failed.") from exc

    async def _add_observed_values(
        self,
        connection: asyncpg.Connection,
        tables: list[TableMetadata],
    ) -> list[TableMetadata]:
        if self._categorical_max_values == 0:
            return tables
        enriched: list[TableMetadata] = []
        discovered_columns = 0
        for table in tables:
            columns: list[ColumnMetadata] = []
            for column in table.column_metadata:
                if (
                    not self._categorical_candidate(column, table)
                    or discovered_columns >= self._categorical_max_columns
                ):
                    columns.append(column)
                    continue
                discovered_columns += 1
                values = await self._observed_values(connection, table, column)
                columns.append(
                    replace(
                        column,
                        observed_values=values,
                        observed_values_source="database" if values else None,
                    )
                )
            enriched.append(replace(table, column_metadata=columns))
        return enriched

    def _categorical_candidate(
        self, column: ColumnMetadata, table: TableMetadata | None = None
    ) -> bool:
        """Whether to sample this column's distinct values.

        Two ways in. A caller may name the exact columns to sample, which is how
        a confirmed semantic model drives this: the columns a reviewer agreed
        carry entity labels and keys. Otherwise the configured name list
        applies, which only ever worked for a database whose columns happen to
        be called `status` or `region` -- exactly the naming assumption this
        architecture exists to remove.

        The type guard is unconditional either way: a numeric or timestamp
        column is never sampled, whichever route selected it.
        """
        data_type = column.data_type.casefold()
        safe_type = (
            data_type == "text"
            or data_type.startswith("character varying")
            or data_type.startswith("character(")
            or data_type == "boolean"
            or data_type.startswith("enum:")
        )
        if not safe_type:
            return False
        if self._sample_columns:
            qualified = (
                f"{table.schema_name}.{table.table_name}.{column.name}".casefold()
                if table is not None
                else column.name.casefold()
            )
            return qualified in self._sample_columns
        return column.name.casefold() in self._categorical_columns

    async def _observed_values(
        self,
        connection: asyncpg.Connection,
        table: TableMetadata,
        column: ColumnMetadata,
    ) -> tuple[str, ...]:
        relation = f"{_quote_identifier(table.schema_name)}.{_quote_identifier(table.table_name)}"
        field = _quote_identifier(column.name)
        query = (
            f"SELECT DISTINCT {field}::text AS value FROM {relation} "
            f"WHERE {field} IS NOT NULL AND length({field}::text) <= $1 "
            "ORDER BY value LIMIT $2"
        )
        rows = await connection.fetch(
            query,
            self._categorical_max_value_length,
            self._categorical_max_values + 1,
            timeout=self._timeout,
        )
        if len(rows) > self._categorical_max_values:
            return ()
        return tuple(str(row["value"]) for row in rows)

    async def execute_readonly(
        self,
        sql: str,
        parameters: Sequence[Any] = (),
    ) -> DatabaseQueryResult:
        try:
            metadata = await self.search_schema("")
            allowed_relations = frozenset(
                (table.schema_name, table.table_name) for table in metadata
            )
            safe_sql = SQLValidator(
                allowed_schemas=frozenset(self._allowed_schemas),
                allowed_tables=frozenset(),
                max_rows=self._max_rows,
            ).validate_readonly(
                sql,
                allowed_relations=allowed_relations,
            )
            started_at = perf_counter()
            executed_at = datetime.now(UTC)
            pool = await self._get_pool()
            async with (
                pool.acquire() as connection,
                connection.transaction(readonly=True),
            ):
                await connection.execute(
                    "SELECT set_config('statement_timeout', $1, true)",
                    f"{max(1, int(self._timeout * 1000))}ms",
                    timeout=self._timeout,
                )
                async with asyncio.timeout(self._timeout):
                    statement = await connection.prepare(safe_sql, timeout=self._timeout)
                    cursor = await statement.cursor(*parameters)
                    records = await cursor.fetch(self._max_rows + 1)
                columns = [
                    ResultColumnMetadata(
                        name=attribute.name,
                        data_type=attribute.type.name,
                    )
                    for attribute in statement.get_attributes()
                ]
            rows, result_bytes, bytes_truncated = bounded_rows(
                records[: self._max_rows],
                max_result_bytes=self._max_result_bytes,
            )
            truncated = len(records) > self._max_rows or bytes_truncated
            return DatabaseQueryResult(
                rows=rows,
                columns=columns,
                metadata=DatabaseExecutionMetadata(
                    duration_ms=round((perf_counter() - started_at) * 1000, 3),
                    executed_at=executed_at,
                    row_count=len(rows),
                    result_bytes=result_bytes,
                    truncated=truncated,
                    live=True,
                ),
            )
        except DatabaseReadOnlyConfigurationError:
            raise
        except (asyncpg.QueryCanceledError, TimeoutError) as exc:
            raise DatabaseQueryTimeoutError("PostgreSQL query timed out.") from exc
        except asyncpg.InsufficientPrivilegeError as exc:
            raise DatabasePermissionError(
                "The PostgreSQL role cannot read the requested data."
            ) from exc
        except asyncpg.PostgresConnectionError as exc:
            raise DatabaseUnavailableError("PostgreSQL is unavailable.") from exc
        except asyncpg.PostgresError as exc:
            raise DatabaseQueryExecutionError("PostgreSQL rejected the read-only query.") from exc

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def _build_table_metadata(
    column_rows: Sequence[Mapping[str, Any]],
    foreign_key_rows: Sequence[Mapping[str, Any]],
) -> list[TableMetadata]:
    foreign_keys: dict[tuple[str, str], list[ForeignKeyMetadata]] = defaultdict(list)
    for row in foreign_key_rows:
        foreign_keys[(row["source_schema"], row["source_table"])].append(
            ForeignKeyMetadata(
                columns=tuple(row["source_columns"]),
                referenced_table=f"{row['target_schema']}.{row['target_table']}",
                referenced_columns=tuple(row["target_columns"]),
            )
        )

    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in column_rows:
        grouped[(row["schema_name"], row["relation_name"])].append(row)

    metadata: list[TableMetadata] = []
    for (schema_name, table_name), rows in grouped.items():
        columns = [
            ColumnMetadata(
                name=row["column_name"],
                data_type=(
                    f"enum:{row['data_type']}" if row["type_kind"] == "e" else row["data_type"]
                ),
                nullable=bool(row["nullable"]),
                description=row["column_description"],
                primary_key=bool(row.get("primary_key_ordinal", row.get("primary_key", 0))),
            )
            for row in rows
        ]
        relation_type = RELATION_TYPES.get(rows[0]["relkind"], "table")
        metadata.append(
            TableMetadata(
                schema_name=schema_name,
                table_name=table_name,
                columns=[column.name for column in columns],
                description=rows[0]["relation_description"]
                or f"PostgreSQL {relation_type.replace('_', ' ')}.",
                column_metadata=columns,
                primary_key=tuple(
                    row["column_name"]
                    for row in sorted(
                        rows,
                        key=lambda value: int(value.get("primary_key_ordinal", 0) or 0),
                    )
                    if bool(row.get("primary_key_ordinal", row.get("primary_key", 0)))
                ),
                foreign_keys=tuple(foreign_keys[(schema_name, table_name)]),
                object_type=cast(Any, relation_type),
            )
        )
    return metadata


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
