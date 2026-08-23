from dataclasses import dataclass

from sqlglot import expressions as exp
from sqlglot import parse
from sqlglot.errors import ParseError


class SQLValidationError(ValueError):
    """Raised when generated SQL is not safe for read-only analytics execution."""


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
    prohibited_functions: frozenset[str] = frozenset(
        {
            "dblink_connect",
            "lo_export",
            "lo_import",
            "nextval",
            "pg_advisory_lock",
            "pg_advisory_xact_lock",
            "pg_read_binary_file",
            "pg_read_file",
            "pg_reload_conf",
            "pg_rotate_logfile",
            "pg_terminate_backend",
            "set_config",
            "setval",
        }
    )
    max_rows: int = 100

    def validate_readonly(self, sql: str) -> str:
        try:
            statements = parse(sql, read="postgres")
        except ParseError as exc:
            raise SQLValidationError("SQL could not be parsed.") from exc

        if len(statements) != 1:
            raise SQLValidationError("SQL must contain exactly one statement.")

        statement = statements[0]
        if statement is None or not isinstance(statement, exp.Query):
            raise SQLValidationError("Only read-only SELECT statements are allowed.")

        prohibited_names = (
            "Alter",
            "Command",
            "Copy",
            "Create",
            "Delete",
            "Drop",
            "Execute",
            "Grant",
            "Insert",
            "Into",
            "Lock",
            "Merge",
            "Revoke",
            "Transaction",
            "TruncateTable",
            "Update",
        )
        prohibited = tuple(
            expression_type
            for name in prohibited_names
            if isinstance((expression_type := getattr(exp, name, None)), type)
        )
        if any(isinstance(node, prohibited) for node in statement.walk()):
            raise SQLValidationError("SQL contains a prohibited mutation or command.")

        cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
        for table in statement.find_all(exp.Table):
            db = table.db
            if not db and table.name in cte_names:
                continue
            if not db:
                raise SQLValidationError("Physical tables must be schema-qualified.")
            if db and db not in self.allowed_schemas:
                raise SQLValidationError(f"Schema '{db}' is not allowed.")
            if table.name not in self.allowed_tables:
                raise SQLValidationError(f"Table '{table.name}' is not allowed.")

        for function in statement.find_all(exp.Func):
            function_name = (
                function.name if isinstance(function, exp.Anonymous) else function.sql_name()
            )
            if function_name.lower() in self.prohibited_functions:
                raise SQLValidationError(f"Function '{function_name}' is not allowed.")

        limit = statement.args.get("limit")
        if limit is not None:
            limit_value = limit.expression
            if not isinstance(limit_value, exp.Literal) or not limit_value.is_int:
                raise SQLValidationError("LIMIT must be a fixed integer.")
            if int(limit_value.this) > self.max_rows:
                statement = statement.limit(self.max_rows)
        else:
            statement = statement.limit(self.max_rows)

        return statement.sql(dialect="postgres")
