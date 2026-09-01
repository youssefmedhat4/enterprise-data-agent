from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlglot import expressions as exp
from sqlglot import parse
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.data.gateway import TableMetadata


class SQLValidationCode(StrEnum):
    PARSE_ERROR = "parse_error"
    MULTIPLE_STATEMENTS = "multiple_statements"
    MUTATION_ATTEMPT = "mutation_attempt"
    UNKNOWN_TABLE = "unknown_table"
    UNKNOWN_COLUMN = "unknown_column"
    AMBIGUOUS_COLUMN = "ambiguous_column"
    FORBIDDEN_SCHEMA = "forbidden_schema"
    FORBIDDEN_SYSTEM_ACCESS = "forbidden_system_access"
    FORBIDDEN_FUNCTION = "forbidden_function"
    UNRESOLVED_ALIAS = "unresolved_alias"
    INVALID_CTE_REFERENCE = "invalid_cte_reference"
    RESTRICTED_STAR = "restricted_star"
    INVALID_LIMIT = "invalid_limit"


_REPAIRABLE_CODES = frozenset(
    {
        SQLValidationCode.UNKNOWN_TABLE,
        SQLValidationCode.UNKNOWN_COLUMN,
        SQLValidationCode.AMBIGUOUS_COLUMN,
        SQLValidationCode.UNRESOLVED_ALIAS,
        SQLValidationCode.INVALID_CTE_REFERENCE,
        SQLValidationCode.FORBIDDEN_SCHEMA,
    }
)


class SQLValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    is_valid: bool
    validated_sql: str | None = None
    error_code: SQLValidationCode | None = None
    error_details: str | None = None
    repairable: bool = False
    referenced_tables: tuple[str, ...] = ()
    referenced_columns: tuple[str, ...] = ()
    referenced_schemas: tuple[str, ...] = ()
    resolved_aliases: dict[str, str] = Field(default_factory=dict)
    unknown_tables: tuple[str, ...] = ()
    unknown_columns: tuple[str, ...] = ()
    ambiguous_columns: tuple[str, ...] = ()
    unsupported_functions: tuple[str, ...] = ()
    parse_latency_ms: float = Field(default=0, ge=0)
    schema_validation_latency_ms: float = Field(default=0, ge=0)


class SQLValidationError(ValueError):
    """Raised when generated SQL fails the full read-only validation pipeline."""

    def __init__(self, message: str, *, result: SQLValidationResult | None = None) -> None:
        super().__init__(message)
        self.result = result


class SQLSchemaValidationError(SQLValidationError):
    """Raised for schema-aware incompatibility with the allowed schema snapshot."""


class SQLRepairFailedError(SQLSchemaValidationError):
    """Raised when the one permitted SQL repair still fails validation."""


@dataclass(frozen=True)
class SQLValidator:
    allowed_schemas: frozenset[str] = frozenset({"analytics"})
    allowed_tables: frozenset[str] = frozenset(
        {
            "customers",
            "departments",
            "employee_project_assignments",
            "employees",
            "invoice_lines",
            "invoices",
            "payroll",
            "project_costs",
            "projects",
        }
    )
    #: Everything here is deterministic, read-only, and confined to the values
    #: already in the result. The list is an allowlist because the failure mode
    #: of missing one is a rejected query, while the failure mode of admitting
    #: a wrong one is data leaving the database.
    #:
    #: `exists`, `any` and `all` are subquery *predicates*, not calls, and
    #: without them "projects invoiced but with no posted cost" cannot be
    #: written the natural way at all -- the anti-join question was refused as
    #: unsafe on every schema. The date and number conversions matter for the
    #: same reason on any database that stores a date as text, which older
    #: schemas routinely do.
    #:
    #: Pattern-matching functions are deliberately absent: a caller-supplied
    #: expression is a cost the read-only role and statement timeout do not
    #: bound.
    allowed_functions: frozenset[str] = frozenset(
        {
            "abs", "age", "all", "and", "any", "array_agg", "avg", "bool_and",
            "bool_or", "case", "cast", "ceil", "ceiling", "char_length",
            "coalesce", "concat", "concat_ws", "corr", "count", "cume_dist",
            "current_date", "current_timestamp", "date_part", "date_trunc",
            "dense_rank", "div", "every", "exists", "extract", "first_value",
            "floor", "greatest", "if", "initcap", "lag", "last_value", "lead",
            "least", "left", "length", "lower", "lpad", "ltrim", "max", "min",
            "mod", "mode", "nth_value", "ntile", "nullif", "or",
            "percent_rank", "percentile_cont", "percentile_disc", "position",
            "power", "rank", "replace", "reverse", "right", "round",
            "row_number", "rpad", "rtrim", "sign", "split_part", "sqrt",
            "starts_with", "stddev", "stddev_pop", "stddev_samp",
            # SQLGlot canonicalises names, so the allowlist is written in its
            # vocabulary: `to_date` parses as `str_to_date`, `to_timestamp` as
            # `str_to_time`, `position`/`strpos` as `str_position`. Listing the
            # surface spelling alone silently rejects the function.
            "str_position", "str_to_date", "str_to_time", "string_agg",
            # `date_trunc` parses as `timestamp_trunc`, so listing only the
            # surface spelling blocked grouping by month -- the single most
            # common thing a time-series question asks for.
            "timestamp_trunc",
            "strpos", "substring", "sum", "to_char", "to_date", "to_number",
            "to_timestamp", "translate", "trim", "trunc", "upper", "var_pop",
            "var_samp", "variance",
        }
    )
    prohibited_functions: frozenset[str] = frozenset(
        {
            "dblink_connect", "lo_export", "lo_import", "nextval", "pg_advisory_lock",
            "pg_advisory_xact_lock", "pg_read_binary_file", "pg_read_file",
            "pg_reload_conf", "pg_rotate_logfile", "pg_terminate_backend", "set_config",
            "setval",
        }
    )
    system_schemas: frozenset[str] = frozenset({"pg_catalog", "information_schema"})
    max_rows: int = 100

    def validate(
        self,
        sql: str,
        *,
        allowed_schema: list[TableMetadata] | None = None,
        allowed_relations: frozenset[tuple[str, str]] | None = None,
    ) -> SQLValidationResult:
        parse_started = perf_counter()
        try:
            statements = parse(sql, read="postgres")
        except ParseError:
            return self._invalid(
                SQLValidationCode.PARSE_ERROR,
                "SQL could not be parsed.",
                parse_latency_ms=_elapsed_ms(parse_started),
            )
        parse_latency_ms = _elapsed_ms(parse_started)
        if len(statements) != 1:
            return self._invalid(
                SQLValidationCode.MULTIPLE_STATEMENTS,
                "SQL must contain exactly one statement.",
                parse_latency_ms=parse_latency_ms,
            )
        statement = statements[0]
        if statement is None or not isinstance(statement, exp.Query):
            return self._invalid(
                SQLValidationCode.MUTATION_ATTEMPT,
                "Only read-only SELECT statements are allowed.",
                parse_latency_ms=parse_latency_ms,
            )
        if _contains_prohibited_statement(statement):
            return self._invalid(
                SQLValidationCode.MUTATION_ATTEMPT,
                "SQL contains a prohibited mutation or command.",
                parse_latency_ms=parse_latency_ms,
            )

        schema_started = perf_counter()
        snapshot = _SchemaSnapshot.from_metadata(allowed_schema)
        relation_scope = frozenset(snapshot.tables) if snapshot is not None else allowed_relations
        cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
        references = _table_references(statement, cte_names)
        aliases = _resolved_aliases(statement, cte_names)
        referenced_tables = tuple(sorted(reference.identifier for reference in references))
        referenced_schemas = tuple(
            sorted({reference.schema for reference in references if reference.schema})
        )
        referenced_columns = tuple(
            sorted({column.sql(dialect="postgres") for column in statement.find_all(exp.Column)})
        )

        common = {
            "referenced_tables": referenced_tables,
            "referenced_columns": referenced_columns,
            "referenced_schemas": referenced_schemas,
            "resolved_aliases": aliases,
            "parse_latency_ms": parse_latency_ms,
        }
        for reference in references:
            if _is_system_reference(reference.schema, reference.table, self.system_schemas):
                return self._invalid(
                    SQLValidationCode.FORBIDDEN_SYSTEM_ACCESS,
                    "System catalog access is not allowed for analytical SQL.",
                    unknown_tables=(reference.identifier,),
                    schema_validation_latency_ms=_elapsed_ms(schema_started),
                    **common,
                )
            if not reference.schema:
                code = (
                    SQLValidationCode.INVALID_CTE_REFERENCE
                    if cte_names
                    else SQLValidationCode.FORBIDDEN_SCHEMA
                )
                return self._invalid(
                    code,
                    "Physical tables must be schema-qualified.",
                    unknown_tables=(reference.table,),
                    schema_validation_latency_ms=_elapsed_ms(schema_started),
                    **common,
                )
            if reference.schema not in self.allowed_schemas:
                return self._invalid(
                    SQLValidationCode.FORBIDDEN_SCHEMA,
                    f"Schema '{reference.schema}' is not allowed.",
                    unknown_tables=(reference.identifier,),
                    schema_validation_latency_ms=_elapsed_ms(schema_started),
                    **common,
                )
            if (
                relation_scope is not None
                and (reference.schema, reference.table) not in relation_scope
            ):
                return self._invalid(
                    SQLValidationCode.UNKNOWN_TABLE,
                    f"Relation '{reference.identifier}' was not discovered in the allowed "
                    "schema snapshot.",
                    unknown_tables=(reference.identifier,),
                    schema_validation_latency_ms=_elapsed_ms(schema_started),
                    **common,
                )
            if relation_scope is None and reference.table not in self.allowed_tables:
                return self._invalid(
                    SQLValidationCode.UNKNOWN_TABLE,
                    f"Table '{reference.table}' is not allowed.",
                    unknown_tables=(reference.identifier,),
                    schema_validation_latency_ms=_elapsed_ms(schema_started),
                    **common,
                )

        unsupported = _unsupported_functions(
            statement,
            allowed=self.allowed_functions,
            prohibited=self.prohibited_functions,
        )
        if unsupported:
            return self._invalid(
                SQLValidationCode.FORBIDDEN_FUNCTION,
                f"Function '{unsupported[0]}' is not allowed.",
                unsupported_functions=unsupported,
                schema_validation_latency_ms=_elapsed_ms(schema_started),
                **common,
            )
        if any(scope.stars for scope in traverse_scope(statement)):
            return self._invalid(
                SQLValidationCode.RESTRICTED_STAR,
                "Projection stars are not allowed; select approved columns explicitly.",
                schema_validation_latency_ms=_elapsed_ms(schema_started),
                **common,
            )

        if snapshot is not None:
            try:
                qualify(
                    statement.copy(),
                    dialect="postgres",
                    schema=cast(dict[str, object], snapshot.sqlglot_schema),
                    validate_qualify_columns=True,
                    expand_stars=False,
                    quote_identifiers=False,
                    identify=False,
                )
            except OptimizeError as exc:
                code, name = _classify_scope_error(statement, snapshot, str(exc))
                return self._invalid(
                    code,
                    _scope_error_message(code, name),
                    unknown_columns=(name,) if code != SQLValidationCode.AMBIGUOUS_COLUMN else (),
                    ambiguous_columns=(name,) if code == SQLValidationCode.AMBIGUOUS_COLUMN else (),
                    schema_validation_latency_ms=_elapsed_ms(schema_started),
                    **common,
                )

        limit = statement.args.get("limit")
        if limit is not None:
            limit_value = limit.expression
            if not isinstance(limit_value, exp.Literal) or not limit_value.is_int:
                return self._invalid(
                    SQLValidationCode.INVALID_LIMIT,
                    "LIMIT must be a fixed integer.",
                    schema_validation_latency_ms=_elapsed_ms(schema_started),
                    **common,
                )
            if int(limit_value.this) > self.max_rows:
                statement = statement.limit(self.max_rows)
        else:
            statement = statement.limit(self.max_rows)
        return SQLValidationResult(
            is_valid=True,
            validated_sql=statement.sql(dialect="postgres"),
            schema_validation_latency_ms=_elapsed_ms(schema_started),
            **common,
        )

    def validate_readonly(
        self,
        sql: str,
        *,
        allowed_relations: frozenset[tuple[str, str]] | None = None,
        allowed_schema: list[TableMetadata] | None = None,
    ) -> str:
        result = self.validate(
            sql,
            allowed_schema=allowed_schema,
            allowed_relations=allowed_relations,
        )
        if not result.is_valid or result.validated_sql is None:
            error_type = (
                SQLSchemaValidationError
                if result.error_code in _REPAIRABLE_CODES
                else SQLValidationError
            )
            raise error_type(result.error_details or "SQL validation failed.", result=result)
        return result.validated_sql

    def _invalid(
        self,
        code: SQLValidationCode,
        details: str,
        **fields: Any,
    ) -> SQLValidationResult:
        return SQLValidationResult(
            is_valid=False,
            error_code=code,
            error_details=details,
            repairable=code in _REPAIRABLE_CODES,
            **fields,
        )


@dataclass(frozen=True)
class _TableReference:
    schema: str
    table: str

    @property
    def identifier(self) -> str:
        return f"{self.schema}.{self.table}" if self.schema else self.table


@dataclass(frozen=True)
class _SchemaSnapshot:
    tables: dict[tuple[str, str], frozenset[str]]
    sqlglot_schema: dict[str, dict[str, dict[str, str]]]

    @classmethod
    def from_metadata(cls, metadata: list[TableMetadata] | None) -> _SchemaSnapshot | None:
        if metadata is None:
            return None
        tables = {
            (table.schema_name, table.table_name): frozenset(table.columns)
            for table in metadata
        }
        schema: dict[str, dict[str, dict[str, str]]] = {}
        for table in metadata:
            typed = {column.name: column.data_type for column in table.column_metadata}
            schema.setdefault(table.schema_name, {})[table.table_name] = {
                column: typed.get(column, "UNKNOWN") for column in table.columns
            }
        return cls(tables=tables, sqlglot_schema=schema)


def _contains_prohibited_statement(statement: exp.Query) -> bool:
    prohibited_names = (
        "Alter", "Command", "Copy", "Create", "Delete", "Drop", "Execute", "Grant",
        "Insert", "Into", "Lock", "Merge", "Revoke", "Transaction", "TruncateTable",
        "Update",
    )
    prohibited = tuple(
        expression_type
        for name in prohibited_names
        if isinstance((expression_type := getattr(exp, name, None)), type)
    )
    return any(isinstance(node, prohibited) for node in statement.walk())


def _table_references(
    statement: exp.Query,
    cte_names: set[str],
) -> tuple[_TableReference, ...]:
    return tuple(
        _TableReference(table.db, table.name)
        for table in statement.find_all(exp.Table)
        if table.db or table.name not in cte_names
    )


def _resolved_aliases(statement: exp.Query, cte_names: set[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        if not table.db and table.name in cte_names:
            aliases[table.alias_or_name] = f"cte:{table.name}"
        else:
            identifier = f"{table.db}.{table.name}" if table.db else table.name
            aliases[table.alias_or_name] = identifier
    for scope in traverse_scope(statement):
        for name, source in scope.sources.items():
            if isinstance(source, Scope):
                aliases.setdefault(name, "derived")
    return dict(sorted(aliases.items()))


def _is_system_reference(
    schema: str,
    table: str,
    system_schemas: frozenset[str],
) -> bool:
    normalized_schema = schema.casefold()
    normalized_table = table.casefold()
    return (
        normalized_schema in system_schemas
        or normalized_table.startswith("pg_")
        or normalized_table in {"pg_user", "pg_roles", "pg_tables"}
    )


def _unsupported_functions(
    statement: exp.Query,
    *,
    allowed: frozenset[str],
    prohibited: frozenset[str],
) -> tuple[str, ...]:
    names: set[str] = set()
    for function in statement.find_all(exp.Func):
        name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
        normalized = name.casefold()
        if normalized in prohibited or normalized not in allowed:
            names.add(normalized)
    return tuple(sorted(names))


def _classify_scope_error(
    statement: exp.Query,
    snapshot: _SchemaSnapshot,
    message: str,
) -> tuple[SQLValidationCode, str]:
    name_match = re.search(r"(?:Column '([^']+)'|Unknown column: ([\w]+))", message)
    name = (
        next((item for item in name_match.groups() if item), "unknown")
        if name_match
        else "unknown"
    )
    table_match = re.search(r"for table: '([^']+)'", message)
    if table_match and table_match.group(1) not in _all_scope_aliases(statement):
        return SQLValidationCode.UNRESOLVED_ALIAS, table_match.group(1)
    if _is_ambiguous_unqualified_column(statement, snapshot, name):
        return SQLValidationCode.AMBIGUOUS_COLUMN, name
    return SQLValidationCode.UNKNOWN_COLUMN, name


def _all_scope_aliases(statement: exp.Query) -> set[str]:
    return {name for scope in traverse_scope(statement) for name in scope.sources}


def _is_ambiguous_unqualified_column(
    statement: exp.Query,
    snapshot: _SchemaSnapshot,
    column_name: str,
) -> bool:
    for scope in traverse_scope(statement):
        if not any(column.name == column_name and not column.table for column in scope.columns):
            continue
        candidates = 0
        for _, (_, source) in scope.selected_sources.items():
            if isinstance(source, exp.Table):
                columns = snapshot.tables.get((source.db, source.name), frozenset())
                candidates += column_name in columns
            elif isinstance(source, Scope):
                candidates += column_name in getattr(source.expression, "named_selects", [])
        if candidates > 1:
            return True
    return False


def _scope_error_message(code: SQLValidationCode, name: str) -> str:
    return {
        SQLValidationCode.UNRESOLVED_ALIAS: f"Table alias '{name}' could not be resolved.",
        SQLValidationCode.AMBIGUOUS_COLUMN: f"Column '{name}' is ambiguous.",
    }.get(code, f"Column '{name}' is not in the allowed schema snapshot.")


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 4)
