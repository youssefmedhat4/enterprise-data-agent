from typing import Any, cast

import asyncpg
import pytest

from app.config import Settings
from app.data.gateway import (
    DatabaseReadOnlyConfigurationError,
    DatabaseResultTooLargeError,
)
from app.data.postgres import (
    PostgresDatabaseGateway,
    _build_table_metadata,
    _quote_identifier,
)
from app.data.result_bounds import bounded_rows
from app.data.schema_metadata import synthetic_enterprise_metadata


class ReadOnlyVerificationConnection:
    def __init__(self, values: dict[str, bool]) -> None:
        self.values = values

    async def fetchrow(self, *args: Any, **kwargs: Any) -> dict[str, bool]:
        del args, kwargs
        return self.values


class ObservedValueConnection:
    def __init__(self, values: list[str]) -> None:
        self.values = values
        self.query = ""
        self.parameters: tuple[Any, ...] = ()

    async def fetch(self, query: str, *parameters: Any, **kwargs: Any) -> list[dict[str, str]]:
        del kwargs
        self.query = query
        self.parameters = parameters
        return [{"value": value} for value in self.values]


def _gateway() -> PostgresDatabaseGateway:
    return PostgresDatabaseGateway(
        Settings(
            DATABASE_PROVIDER="postgres",
            DATABASE_URL="postgresql://reader@example.test:5432/warehouse",
            DB_ALLOWED_SCHEMAS="analytics",
        )
    )


@pytest.mark.asyncio
async def test_connection_verification_accepts_only_physical_read_only_role() -> None:
    connection = ReadOnlyVerificationConnection(
        {
            "default_read_only": True,
            "superuser": False,
            "can_create_in_schema": False,
            "can_mutate_relation": False,
        }
    )

    await _gateway()._initialize_connection(cast(asyncpg.Connection, connection))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_field",
    ["superuser", "can_create_in_schema", "can_mutate_relation"],
)
async def test_connection_verification_rejects_write_capability(
    unsafe_field: str,
) -> None:
    values = {
        "default_read_only": True,
        "superuser": False,
        "can_create_in_schema": False,
        "can_mutate_relation": False,
    }
    values[unsafe_field] = True

    with pytest.raises(DatabaseReadOnlyConfigurationError):
        await _gateway()._initialize_connection(
            cast(asyncpg.Connection, ReadOnlyVerificationConnection(values))
        )


@pytest.mark.asyncio
async def test_connection_verification_rejects_read_write_default() -> None:
    connection = ReadOnlyVerificationConnection(
        {
            "default_read_only": False,
            "superuser": False,
            "can_create_in_schema": False,
            "can_mutate_relation": False,
        }
    )

    with pytest.raises(DatabaseReadOnlyConfigurationError):
        await _gateway()._initialize_connection(cast(asyncpg.Connection, connection))


def test_catalog_rows_become_tables_columns_keys_and_relationships() -> None:
    columns = [
        {
            "schema_name": "analytics",
            "relation_name": "departments",
            "relkind": "r",
            "relation_description": "Departments.",
            "column_name": "id",
            "data_type": "integer",
            "nullable": False,
            "column_description": "Department ID.",
            "type_kind": "b",
            "primary_key": True,
        },
        {
            "schema_name": "analytics",
            "relation_name": "employees",
            "relkind": "v",
            "relation_description": "Employee reporting view.",
            "column_name": "department_id",
            "data_type": "integer",
            "nullable": False,
            "column_description": "Department ID.",
            "type_kind": "b",
            "primary_key": False,
        },
    ]
    foreign_keys = [
        {
            "source_schema": "analytics",
            "source_table": "employees",
            "source_columns": ["department_id"],
            "target_schema": "analytics",
            "target_table": "departments",
            "target_columns": ["id"],
        }
    ]

    metadata = _build_table_metadata(columns, foreign_keys)

    departments = next(table for table in metadata if table.table_name == "departments")
    employees = next(table for table in metadata if table.table_name == "employees")
    assert departments.primary_key == ("id",)
    assert departments.column_metadata[0].data_type == "integer"
    assert employees.object_type == "view"
    assert employees.foreign_keys[0].referenced_table == "analytics.departments"


def test_result_byte_budget_truncates_only_between_rows() -> None:
    rows, result_bytes, truncated = bounded_rows(
        [{"id": 1}, {"id": 2}],
        max_result_bytes=12,
    )

    assert rows == [{"id": 1}]
    assert result_bytes <= 12
    assert truncated is True


def test_result_byte_budget_rejects_single_oversized_row() -> None:
    with pytest.raises(DatabaseResultTooLargeError):
        bounded_rows([{"value": "x" * 200}], max_result_bytes=20)


@pytest.mark.asyncio
async def test_observed_values_are_cardinality_and_length_bounded() -> None:
    gateway = PostgresDatabaseGateway(
        Settings(
            DB_CATEGORICAL_MAX_VALUES=2,
            DB_CATEGORICAL_MAX_VALUE_LENGTH=12,
        )
    )
    employee = next(
        table for table in synthetic_enterprise_metadata() if table.table_name == "employees"
    )
    status = next(column for column in employee.column_metadata if column.name == "status")
    connection = ObservedValueConnection(["active", "inactive", "leave"])

    values = await gateway._observed_values(cast(asyncpg.Connection, connection), employee, status)

    assert values == ()
    assert "SELECT DISTINCT" in connection.query
    assert connection.parameters == (12, 3)


def test_source_identifier_and_identifier_quoting_do_not_expose_credentials() -> None:
    gateway = _gateway()

    assert gateway.source().identifier == "postgres:warehouse"
    assert "reader" not in gateway.source().identifier
    assert _quote_identifier('odd"name') == '"odd""name"'
