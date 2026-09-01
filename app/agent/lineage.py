"""Where an answer came from, derived rather than described.

Everything here is read off things the system already holds: the validated SQL,
the confirmed semantic model, and a certified metric's bounded expression tree.
Nothing asks a model to explain itself. A model recounting its own reasoning
produces a plausible story, and a plausible story about lineage is worse than
none -- it is unfalsifiable, and people act on it.

That principle decides the awkward cases too. When column-level lineage cannot
be read confidently out of the SQL, this says table-level lineage is what is
available rather than guessing which column fed which measure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.knowledge.expressions import BinaryOp, ExpressionNode, Literal_, MetricRef

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.knowledge.discovery import SemanticModel

logger = logging.getLogger(__name__)

#: Deeper than this and the picture stops helping anyone.
MAX_METRIC_DEPTH = 6


@dataclass(frozen=True, slots=True)
class TableLineage:
    """One physical table an answer read, with the columns it used."""

    table: str
    columns: tuple[str, ...] = ()
    #: The confirmed business entity this table holds, when review named one.
    entity: str | None = None

    @property
    def column_level(self) -> bool:
        return bool(self.columns)


@dataclass(frozen=True, slots=True)
class MetricNode:
    """A certified metric and what it is computed from."""

    label: str
    kind: str
    children: tuple[MetricNode, ...] = ()


@dataclass(frozen=True, slots=True)
class AnswerLineage:
    tables: tuple[TableLineage, ...] = ()
    metrics: tuple[MetricNode, ...] = ()
    #: True when every table's columns could be read from the statement.
    column_level: bool = False
    note: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.tables and not self.metrics


def lineage_from_sql(
    sql: str | None,
    *,
    semantic_model: SemanticModel | None = None,
    fallback_tables: tuple[str, ...] = (),
) -> AnswerLineage:
    """Read tables and columns out of a validated statement.

    SQLGlot has already parsed this statement once to validate it, so parsing it
    again is cheap and, more importantly, authoritative: the lineage describes
    the query that ran rather than the query anyone intended.
    """
    entities = _entities_by_table(semantic_model)
    if not sql:
        return AnswerLineage(
            tables=tuple(
                TableLineage(table=table, entity=entities.get(table.casefold()))
                for table in fallback_tables
            ),
            note="Table-level lineage only: no statement was executed.",
        )

    try:
        import sqlglot
        from sqlglot import exp

        statement = sqlglot.parse_one(sql, dialect="postgres")
    except Exception as exc:
        logger.info("lineage parse failed: %s", type(exc).__name__)
        return AnswerLineage(
            tables=tuple(
                TableLineage(table=table, entity=entities.get(table.casefold()))
                for table in fallback_tables
            ),
            note="Table-level lineage only: the statement could not be re-parsed.",
        )

    # A CTE name is not a table anyone can be given access to; it names a
    # result computed inside this same statement.
    local = {
        cte.alias_or_name.casefold()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    real: dict[str, set[str]] = {}
    alias_to_table: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        if not table.name:
            continue
        identifier = f"{table.db}.{table.name}".strip(".")
        if identifier.casefold() in local or table.name.casefold() in local:
            continue
        real.setdefault(identifier, set())
        for name in {table.alias_or_name, table.name}:
            if name:
                alias_to_table[name.casefold()] = identifier

    # Columns are attributed only when the reference says which table it came
    # from. An unqualified column in a multi-table statement is genuinely
    # ambiguous, and guessing is the one thing worth avoiding here.
    unresolved = False
    for column in statement.find_all(exp.Column):
        owner = column.table.casefold() if column.table else ""
        if owner and owner in alias_to_table:
            real.setdefault(alias_to_table[owner], set()).add(column.name)
        elif len(real) == 1:
            only = next(iter(real))
            real[only].add(column.name)
        else:
            unresolved = True

    tables = tuple(
        TableLineage(
            table=identifier,
            columns=tuple(sorted(columns)),
            entity=entities.get(identifier.casefold()),
        )
        for identifier, columns in sorted(real.items())
    )
    column_level = bool(tables) and all(item.column_level for item in tables)
    return AnswerLineage(
        tables=tables,
        column_level=column_level and not unresolved,
        note=""
        if column_level and not unresolved
        else "Table-level lineage available; some columns could not be attributed.",
    )


def lineage_from_metric(
    label: str, expression: ExpressionNode | None, *, depth: int = 0
) -> MetricNode:
    """A certified metric's own expression tree, which is already bounded.

    Nothing is inferred: a derived metric is stored as a small tree over
    certified metric keys, so this is a rendering rather than an analysis.
    """
    if expression is None or depth >= MAX_METRIC_DEPTH:
        return MetricNode(label=label, kind="metric")
    if isinstance(expression, MetricRef):
        return MetricNode(label=expression.metric_key, kind="metric")
    if isinstance(expression, Literal_):
        return MetricNode(label=f"{expression.value:f}", kind="literal")
    if isinstance(expression, BinaryOp):
        left, right = expression.left, expression.right
        children = tuple(
            lineage_from_metric("", operand, depth=depth + 1)
            for operand in (left, right)
            if operand is not None
        )
        return MetricNode(
            label=label or expression.operator, kind=expression.operator, children=children
        )
    return MetricNode(label=label, kind="metric")


def _entities_by_table(model: SemanticModel | None) -> dict[str, str]:
    if model is None:
        return {}
    return {
        f"{entity.source_schema}.{entity.source_table}".casefold(): entity.entity_name
        for entity in model.confirmed_entities()
    }


@dataclass(frozen=True, slots=True)
class AnswerTrace:
    """Everything safe to say about how one answer was produced."""

    data_source: str
    route: str
    execution_source: str
    semantic_entities: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()
    business_instructions: tuple[str, ...] = ()
    query_examples: tuple[str, ...] = ()
    resolved_entities: tuple[str, ...] = ()
    lineage: AnswerLineage = field(default_factory=AnswerLineage)
    metric_lineage: tuple[MetricNode, ...] = ()
    validation_status: str = "not_applicable"
    grounded: bool = False
    data_quality: tuple[str, ...] = ()
    model_profile: str = ""
    total_latency_ms: float = 0.0
    #: Only when this caller may see it, decided upstream by the same policy
    #: that gates debug provenance. Absent is the normal case.
    generated_sql: str | None = None
