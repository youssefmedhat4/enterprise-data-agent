from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings
from app.data.gateway import DatabaseReadOnlyConfigurationError, DatabaseUnavailableError
from app.data.toolbox import (
    READ_ONLY_VERIFICATION_SQL,
    ToolboxDatabaseGateway,
    ToolboxTransportUnavailableError,
)
from app.security.sql_validation import SQLValidationError


class MockToolboxTransport:
    def __init__(self, *, read_only: bool = True, unavailable: bool = False) -> None:
        self.read_only = read_only
        self.unavailable = unavailable
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def health_check(self) -> bool:
        if self.unavailable:
            raise ToolboxTransportUnavailableError("unavailable")
        return True

    async def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        if self.unavailable:
            raise ToolboxTransportUnavailableError("unavailable")
        self.calls.append((name, arguments))
        if arguments.get("sql") == READ_ONLY_VERIFICATION_SQL:
            return [{"read_only": self.read_only}]
        if name == "list_tables":
            return _schema_payload()
        if name == "execute_sql":
            return [
                {"department": "Engineering", "employee_count": 4},
                {"department": "Finance", "employee_count": 2},
            ]
        raise AssertionError(f"Unexpected Toolbox tool: {name}")

    async def close(self) -> None:
        return None


def _settings() -> Settings:
    return Settings(
        DATABASE_PROVIDER="toolbox",
        TOOLBOX_MCP_URL="http://toolbox.test/mcp",
        TOOLBOX_SOURCE_ID="enterprise-postgres",
        DB_ALLOWED_SCHEMAS="analytics",
        DB_MAX_ROWS=10,
        DB_SCHEMA_CACHE_SECONDS=300,
    )


def _schema_payload() -> list[dict[str, Any]]:
    return [
        {
            "schema_name": "analytics",
            "object_name": "departments",
            "object_details": {
                "schema_name": "analytics",
                "object_name": "departments",
                "object_type": "TABLE",
                "comment": "Enterprise departments.",
                "columns": [
                    {
                        "column_name": "id",
                        "data_type": "integer",
                        "is_not_nullable": True,
                        "column_comment": "Department identifier.",
                    },
                    {
                        "column_name": "name",
                        "data_type": "text",
                        "is_not_nullable": True,
                        "column_comment": "Department name.",
                    },
                ],
                "constraints": [
                    {"constraint_type": "PRIMARY KEY", "constraint_columns": ["id"]}
                ],
            },
        },
        {
            "schema_name": "analytics",
            "object_name": "employees",
            "object_details": {
                "schema_name": "analytics",
                "object_name": "employees",
                "object_type": "PARTITIONED TABLE",
                "columns": [
                    {
                        "column_name": "id",
                        "data_type": "integer",
                        "is_not_nullable": True,
                    },
                    {
                        "column_name": "department_id",
                        "data_type": "integer",
                        "is_not_nullable": True,
                    },
                    {
                        "column_name": "salary",
                        "data_type": "numeric(12,2)",
                        "is_not_nullable": True,
                    },
                ],
                "constraints": [
                    {"constraint_type": "PRIMARY KEY", "constraint_columns": ["id"]},
                    {
                        "constraint_type": "FOREIGN KEY",
                        "constraint_columns": ["department_id"],
                        "foreign_key_referenced_table": "analytics.departments",
                        "foreign_key_referenced_columns": ["id"],
                    },
                ],
            },
        },
        {
            "schema_name": "private",
            "object_name": "secrets",
            "object_details": {
                "schema_name": "private",
                "object_name": "secrets",
                "object_type": "TABLE",
                "columns": [{"column_name": "value", "data_type": "text"}],
            },
        },
    ]


@pytest.mark.asyncio
async def test_toolbox_contract_maps_schema_executes_bounded_readonly_sql() -> None:
    transport = MockToolboxTransport()
    gateway = ToolboxDatabaseGateway(_settings(), transport=transport)

    metadata = await gateway.search_schema("department headcount")
    result = await gateway.execute_readonly(
        "SELECT d.name AS department, COUNT(e.id) AS employee_count "
        "FROM analytics.departments d "
        "LEFT JOIN analytics.employees e ON e.department_id = d.id "
        "GROUP BY d.name ORDER BY employee_count DESC"
    )

    assert [table.identifier for table in metadata] == [
        "analytics.departments",
        "analytics.employees",
    ]
    employees = metadata[1]
    assert employees.object_type == "partitioned_table"
    assert employees.primary_key == ("id",)
    assert employees.foreign_keys[0].referenced_table == "analytics.departments"
    assert result.rows[0] == {"department": "Engineering", "employee_count": 4}
    assert result.metadata.live is True
    assert gateway.source().provider == "mcp_toolbox"
    assert gateway.source().identifier == "toolbox:enterprise-postgres"
    executed = [arguments["sql"] for name, arguments in transport.calls if name == "execute_sql"]
    assert executed[0] == READ_ONLY_VERIFICATION_SQL
    assert executed[1].endswith("LIMIT 10")


@pytest.mark.asyncio
async def test_toolbox_unavailable_fails_without_fallback() -> None:
    gateway = ToolboxDatabaseGateway(
        _settings(),
        transport=MockToolboxTransport(unavailable=True),
    )

    with pytest.raises(DatabaseUnavailableError):
        await gateway.health_check()
    with pytest.raises(DatabaseUnavailableError):
        await gateway.search_schema("")


@pytest.mark.asyncio
async def test_toolbox_requires_verified_physical_readonly_role() -> None:
    gateway = ToolboxDatabaseGateway(
        _settings(),
        transport=MockToolboxTransport(read_only=False),
    )

    with pytest.raises(DatabaseReadOnlyConfigurationError):
        await gateway.search_schema("")


@pytest.mark.asyncio
async def test_toolbox_never_invokes_user_mutation_sql() -> None:
    transport = MockToolboxTransport()
    gateway = ToolboxDatabaseGateway(_settings(), transport=transport)
    await gateway.search_schema("")
    transport.calls.clear()

    with pytest.raises(SQLValidationError):
        await gateway.execute_readonly("DELETE FROM analytics.employees")

    assert transport.calls == []
