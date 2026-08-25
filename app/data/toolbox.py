from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from time import monotonic, perf_counter
from typing import Any, Protocol, cast

import httpx

from app.config import Settings
from app.data.gateway import (
    ColumnMetadata,
    DatabaseExecutionMetadata,
    DatabaseGateway,
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

READ_ONLY_VERIFICATION_SQL = """
SELECT
    current_setting('default_transaction_read_only') = 'on'
    AND NOT role.rolsuper
    AND NOT EXISTS (
        SELECT 1
        FROM pg_namespace namespace
        WHERE namespace.nspname <> 'information_schema'
          AND namespace.nspname NOT LIKE 'pg_%'
          AND has_schema_privilege(current_user, namespace.oid, 'CREATE')
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname <> 'information_schema'
          AND namespace.nspname NOT LIKE 'pg_%'
          AND relation.relkind = ANY(ARRAY['r', 'p', 'v', 'm', 'f']::char[])
          AND (
              has_table_privilege(current_user, relation.oid, 'INSERT')
              OR has_table_privilege(current_user, relation.oid, 'UPDATE')
              OR has_table_privilege(current_user, relation.oid, 'DELETE')
              OR has_table_privilege(current_user, relation.oid, 'TRUNCATE')
              OR has_table_privilege(current_user, relation.oid, 'TRIGGER')
          )
    ) AS read_only
FROM pg_roles role
WHERE role.rolname = current_user
"""


class ToolboxTransportUnavailableError(RuntimeError):
    """Raised when the MCP service cannot be reached or initialized."""


class ToolboxTransportTimeoutError(ToolboxTransportUnavailableError):
    """Raised when an MCP operation exceeds its configured timeout."""


class ToolboxToolInvocationError(RuntimeError):
    """Raised when Toolbox reports a tool-level failure or malformed result."""


class ToolboxTransport(Protocol):
    async def health_check(self) -> bool:
        """Check the MCP server without invoking a database tool."""

    async def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke one named Toolbox tool and return its JSON-compatible payload."""

    async def close(self) -> None:
        """Release transport resources."""


class MCPToolboxTransport(ToolboxTransport):
    """Standard streamable-HTTP MCP transport for Toolbox v1 `/mcp`."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        auth_token: str | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._http_client = httpx.AsyncClient(timeout=timeout_seconds, headers=headers)

    async def health_check(self) -> bool:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise ToolboxTransportUnavailableError(
                "The MCP client dependency is not installed."
            ) from exc
        try:
            async with (
                asyncio.timeout(self._timeout_seconds),
                streamable_http_client(
                    self._url,
                    http_client=self._http_client,
                ) as (read_stream, write_stream, _),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                await session.list_tools()
            return True
        except TimeoutError as exc:
            raise ToolboxTransportTimeoutError("MCP Toolbox health check timed out.") from exc
        except Exception as exc:
            raise ToolboxTransportUnavailableError(
                "The configured MCP Toolbox service is unavailable."
            ) from exc

    async def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise ToolboxTransportUnavailableError(
                "The MCP client dependency is not installed."
            ) from exc
        try:
            async with (
                asyncio.timeout(self._timeout_seconds),
                streamable_http_client(
                    self._url,
                    http_client=self._http_client,
                ) as (read_stream, write_stream, _),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                result = await session.call_tool(name, arguments)
            return _tool_result_payload(result)
        except ToolboxToolInvocationError:
            raise
        except TimeoutError as exc:
            raise ToolboxTransportTimeoutError("MCP Toolbox request timed out.") from exc
        except Exception as exc:
            raise ToolboxTransportUnavailableError(
                "The configured MCP Toolbox service is unavailable."
            ) from exc

    async def close(self) -> None:
        await self._http_client.aclose()


class ToolboxDatabaseGateway(DatabaseGateway):
    """Connectivity-only DatabaseGateway for the current PostgreSQL Toolbox tools."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: ToolboxTransport | None = None,
    ) -> None:
        token = (
            settings.toolbox_auth_token.get_secret_value()
            if settings.toolbox_auth_token is not None
            else None
        )
        self._transport = transport or MCPToolboxTransport(
            settings.toolbox_mcp_url,
            timeout_seconds=settings.toolbox_timeout_seconds,
            auth_token=token,
        )
        self._source_id = settings.toolbox_source_id
        self._dialect = settings.toolbox_dialect
        self._execute_tool = settings.toolbox_execute_tool
        self._schema_tool = settings.toolbox_schema_tool
        self._allowed_schemas = settings.database_allowed_schemas
        self._max_rows = settings.query_row_limit
        self._max_result_bytes = settings.query_max_result_bytes
        self._require_read_only = settings.database_require_read_only
        self._schema_cache_seconds = settings.database_schema_cache_seconds
        self._schema_cache: list[TableMetadata] | None = None
        self._schema_cached_at = 0.0
        self._schema_lock = asyncio.Lock()
        self._read_only_verified = False

    def source(self) -> DatabaseSource:
        return DatabaseSource(
            identifier=f"toolbox:{self._source_id}",
            dialect=self._dialect,
            provider="mcp_toolbox",
        )

    async def health_check(self) -> bool:
        try:
            if not await self._transport.health_check():
                raise DatabaseUnavailableError("MCP Toolbox is unavailable.")
            await self._ensure_read_only()
            return True
        except ToolboxTransportTimeoutError as exc:
            raise DatabaseQueryTimeoutError("MCP Toolbox health check timed out.") from exc
        except ToolboxTransportUnavailableError as exc:
            raise DatabaseUnavailableError("MCP Toolbox is unavailable.") from exc

    async def search_schema(self, question: str) -> list[TableMetadata]:
        del question
        if self._schema_cache_valid():
            return list(self._schema_cache or [])
        async with self._schema_lock:
            if self._schema_cache_valid():
                return list(self._schema_cache or [])
            await self._ensure_read_only()
            try:
                payload = await self._transport.invoke(
                    self._schema_tool,
                    {"table_names": "", "output_format": "detailed"},
                )
                metadata = _table_metadata(payload, frozenset(self._allowed_schemas))
            except ToolboxTransportTimeoutError as exc:
                raise DatabaseQueryTimeoutError(
                    "MCP Toolbox schema discovery timed out."
                ) from exc
            except ToolboxTransportUnavailableError as exc:
                raise DatabaseUnavailableError("MCP Toolbox is unavailable.") from exc
            except ToolboxToolInvocationError as exc:
                raise DatabaseQueryExecutionError(
                    "MCP Toolbox schema discovery failed."
                ) from exc
            self._schema_cache = metadata
            self._schema_cached_at = monotonic()
            return list(metadata)

    def _schema_cache_valid(self) -> bool:
        return self._schema_cache is not None and (
            self._schema_cache_seconds > 0
            and monotonic() - self._schema_cached_at < self._schema_cache_seconds
        )

    async def _ensure_read_only(self) -> None:
        if not self._require_read_only or self._read_only_verified:
            return
        try:
            payload = await self._transport.invoke(
                self._execute_tool,
                {"sql": READ_ONLY_VERIFICATION_SQL},
            )
        except ToolboxTransportTimeoutError as exc:
            raise DatabaseQueryTimeoutError(
                "MCP Toolbox read-only verification timed out."
            ) from exc
        except ToolboxTransportUnavailableError as exc:
            raise DatabaseUnavailableError("MCP Toolbox is unavailable.") from exc
        except ToolboxToolInvocationError as exc:
            raise DatabaseReadOnlyConfigurationError(
                "MCP Toolbox could not verify the database role as read-only."
            ) from exc
        records = _records(payload)
        if len(records) != 1 or records[0].get("read_only") is not True:
            raise DatabaseReadOnlyConfigurationError(
                "The MCP Toolbox database role is not safely configured as read-only."
            )
        self._read_only_verified = True

    async def execute_readonly(
        self,
        sql: str,
        parameters: Sequence[Any] = (),
    ) -> DatabaseQueryResult:
        if parameters:
            raise DatabaseQueryExecutionError(
                "The selected MCP Toolbox execute-sql tool does not support bound parameters."
            )
        metadata = await self.search_schema("")
        allowed_relations = frozenset(
            (table.schema_name, table.table_name) for table in metadata
        )
        safe_sql = SQLValidator(
            allowed_schemas=frozenset(self._allowed_schemas),
            allowed_tables=frozenset(),
            max_rows=self._max_rows,
        ).validate_readonly(sql, allowed_relations=allowed_relations)
        started_at = perf_counter()
        executed_at = datetime.now(UTC)
        try:
            payload = await self._transport.invoke(
                self._execute_tool,
                {"sql": safe_sql},
            )
        except ToolboxTransportTimeoutError as exc:
            raise DatabaseQueryTimeoutError("MCP Toolbox query timed out.") from exc
        except ToolboxTransportUnavailableError as exc:
            raise DatabaseUnavailableError("MCP Toolbox is unavailable.") from exc
        except ToolboxToolInvocationError as exc:
            raise DatabaseQueryExecutionError(
                "MCP Toolbox rejected the read-only query."
            ) from exc
        records = _records(payload)
        rows, result_bytes, bytes_truncated = bounded_rows(
            records,
            max_result_bytes=self._max_result_bytes,
        )
        columns = [
            ResultColumnMetadata(name=name, data_type=_result_type(rows, name))
            for name in (rows[0] if rows else {})
        ]
        return DatabaseQueryResult(
            rows=rows,
            columns=columns,
            metadata=DatabaseExecutionMetadata(
                duration_ms=round((perf_counter() - started_at) * 1000, 3),
                executed_at=executed_at,
                row_count=len(rows),
                result_bytes=result_bytes,
                truncated=bytes_truncated,
                live=True,
            ),
        )

    async def close(self) -> None:
        await self._transport.close()


def _tool_result_payload(result: Any) -> Any:
    if bool(getattr(result, "isError", False) or getattr(result, "is_error", False)):
        raise ToolboxToolInvocationError("MCP Toolbox reported a tool error.")
    structured = getattr(result, "structuredContent", None) or getattr(
        result, "structured_content", None
    )
    if isinstance(structured, Mapping):
        if set(structured) == {"result"}:
            return structured["result"]
        return dict(structured)
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    raise ToolboxToolInvocationError("MCP Toolbox returned no JSON result.")


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        for key in ("result", "rows", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                payload = value
                break
        else:
            return [dict(payload)]
    if not isinstance(payload, list):
        raise ToolboxToolInvocationError("MCP Toolbox returned an invalid row contract.")
    if not all(isinstance(row, Mapping) for row in payload):
        raise ToolboxToolInvocationError("MCP Toolbox returned invalid result rows.")
    return [dict(cast(Mapping[str, Any], row)) for row in payload]


def _table_metadata(payload: Any, allowed_schemas: frozenset[str]) -> list[TableMetadata]:
    tables: list[TableMetadata] = []
    for record in _records(payload):
        details = record.get("object_details", record)
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except json.JSONDecodeError as exc:
                raise ToolboxToolInvocationError(
                    "MCP Toolbox returned invalid table metadata."
                ) from exc
        if not isinstance(details, Mapping):
            raise ToolboxToolInvocationError("MCP Toolbox returned invalid table metadata.")
        schema = _string(details.get("schema_name") or record.get("schema_name"))
        table_name = _string(
            details.get("object_name")
            or details.get("table_name")
            or record.get("object_name")
        )
        if not schema or not table_name or schema not in allowed_schemas:
            continue
        columns = _columns(details.get("columns"))
        constraints = _mapping_list(details.get("constraints"))
        primary_key = next(
            (
                tuple(_string_list(item.get("constraint_columns")))
                for item in constraints
                if _string(item.get("constraint_type")).casefold() == "primary key"
            ),
            (),
        )
        primary_names = set(primary_key)
        columns = [
            ColumnMetadata(
                name=column.name,
                data_type=column.data_type,
                nullable=column.nullable,
                description=column.description,
                primary_key=column.name in primary_names,
            )
            for column in columns
        ]
        foreign_keys = tuple(
            foreign_key
            for item in constraints
            if (foreign_key := _foreign_key(item, allowed_schemas)) is not None
        )
        object_type = _object_type(details.get("object_type"))
        tables.append(
            TableMetadata(
                schema_name=schema,
                table_name=table_name,
                columns=[column.name for column in columns],
                description=_string(details.get("comment"))
                or f"Toolbox-discovered PostgreSQL {object_type.replace('_', ' ')}.",
                column_metadata=columns,
                primary_key=primary_key,
                foreign_keys=foreign_keys,
                object_type=object_type,
            )
        )
    return sorted(tables, key=lambda table: table.identifier)


def _columns(value: Any) -> list[ColumnMetadata]:
    return [
        ColumnMetadata(
            name=_string(item.get("column_name")),
            data_type=_string(item.get("data_type")) or "unknown",
            nullable=not bool(item.get("is_not_nullable")),
            description=_string(item.get("column_comment")),
        )
        for item in _mapping_list(value)
        if _string(item.get("column_name"))
    ]


def _foreign_key(
    constraint: Mapping[str, Any],
    allowed_schemas: frozenset[str],
) -> ForeignKeyMetadata | None:
    if _string(constraint.get("constraint_type")).casefold() != "foreign key":
        return None
    referenced = _string(constraint.get("foreign_key_referenced_table")).strip('"')
    if "." not in referenced:
        return None
    schema, _, table = referenced.rpartition(".")
    schema = schema.strip('"')
    table = table.strip('"')
    if schema not in allowed_schemas or not table:
        return None
    columns = tuple(_string_list(constraint.get("constraint_columns")))
    referenced_columns = tuple(
        _string_list(constraint.get("foreign_key_referenced_columns"))
    )
    if not columns or len(columns) != len(referenced_columns):
        return None
    return ForeignKeyMetadata(
        columns=columns,
        referenced_table=f"{schema}.{table}",
        referenced_columns=referenced_columns,
    )


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _object_type(value: Any) -> Any:
    normalized = _string(value).casefold().replace(" ", "_")
    if normalized in {
        "table",
        "partitioned_table",
        "view",
        "materialized_view",
        "foreign_table",
    }:
        return normalized
    return "table"


def _result_type(rows: list[dict[str, Any]], name: str) -> str:
    value = next((row[name] for row in rows if row.get(name) is not None), None)
    return type(value).__name__ if value is not None else "unknown"
