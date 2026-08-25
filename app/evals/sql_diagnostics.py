from sqlglot import expressions as exp
from sqlglot import parse_one
from sqlglot.errors import ParseError

from app.data.gateway import TableMetadata
from app.evals.models import EvaluationSummary


def schema_hallucinations(
    sql: str | None,
    schema: list[TableMetadata],
) -> tuple[list[str], list[str]]:
    if not sql:
        return [], []
    try:
        statement = parse_one(sql, read="postgres")
    except ParseError:
        return [], []

    known_columns = {table.table_name: set(table.columns) for table in schema}
    cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
    table_aliases: dict[str, str] = {}
    hallucinated_tables: set[str] = set()
    referenced_tables: set[str] = set()
    for table in statement.find_all(exp.Table):
        if table.name in cte_names:
            continue
        if table.name not in known_columns:
            hallucinated_tables.add(table.name)
            continue
        referenced_tables.add(table.name)
        table_aliases[table.alias_or_name] = table.name
        table_aliases[table.name] = table.name

    select_aliases = {
        expression.alias for expression in statement.find_all(exp.Alias) if expression.alias
    }
    hallucinated_columns: set[str] = set()
    for column in statement.find_all(exp.Column):
        if column.name == "*" or column.name in select_aliases:
            continue
        qualifier = column.table
        if qualifier:
            if qualifier in cte_names:
                continue
            table_name = table_aliases.get(qualifier)
            if table_name is None:
                hallucinated_columns.add(f"{qualifier}.{column.name}")
            elif column.name not in known_columns[table_name]:
                hallucinated_columns.add(f"{table_name}.{column.name}")
        elif referenced_tables and not any(
            column.name in known_columns[table_name] for table_name in referenced_tables
        ):
            hallucinated_columns.add(column.name)
    return sorted(hallucinated_tables), sorted(hallucinated_columns)


def summarize_hallucinations(
    summary: EvaluationSummary,
    schema: list[TableMetadata],
) -> tuple[int, int, dict[str, list[str]], dict[str, list[str]]]:
    table_cases: dict[str, list[str]] = {}
    column_cases: dict[str, list[str]] = {}
    for result in summary.results:
        tables, columns = schema_hallucinations(result.generated_sql, schema)
        if tables:
            table_cases[result.case_id] = tables
        if columns:
            column_cases[result.case_id] = columns
    return (
        sum(len(values) for values in table_cases.values()),
        sum(len(values) for values in column_cases.values()),
        table_cases,
        column_cases,
    )
