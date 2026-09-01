"""Checking that a requested period actually reached the query.

The failure this exists for is quiet and expensive: someone asks for revenue
this month, the model writes a perfectly valid query with no date filter, and
the answer is every invoice ever raised. It is correct SQL, it passes every
other guardrail, and the number looks like a number.

So when a period was resolved, the statement is required to constrain the
temporal column it was resolved against. The check reads the parsed statement
rather than searching its text -- a substring search finds the column name in a
`SELECT` list, in a comment, or in an unrelated `ORDER BY`, and reports success
for a query that filters nothing.

Deliberately structural rather than exact. Requiring the emitted bounds to match
the resolved instants to the microsecond would reject correct queries for
writing `>= '2026-01-01'` instead of a timestamp literal, or for filtering a
`date` column. What is checked is that the column is genuinely constrained: a
comparison, a BETWEEN, or an equality against something.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TemporalConstraintError(RuntimeError):
    """Raised when a requested period is missing from the generated SQL."""


def constrains_column(sql: str, column: str) -> bool:
    """Whether the statement filters on `column` in a way that bounds it.

    A reference inside a projection, an ordering or a grouping does not count:
    those change how rows are shown, not which rows are read.
    """
    try:
        import sqlglot
        from sqlglot import exp

        statement = sqlglot.parse_one(sql, dialect="postgres")
    except Exception as caught:
        logger.info("temporal guard could not parse: %s", type(caught).__name__)
        # Unparseable SQL is a problem the validator owns; refusing here as
        # well would report the wrong cause.
        return True

    wanted = column.casefold()
    predicates = (
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.Between,
        exp.EQ,
        exp.In,
    )
    return any(_mentions(node, wanted) for node in statement.find_all(*predicates))


def _mentions(node: object, column: str) -> bool:
    from sqlglot import exp

    if not isinstance(node, exp.Expression):
        return False
    return any(
        reference.name.casefold() == column
        for reference in node.find_all(exp.Column)
        if reference.name
    )


def require_temporal_constraint(sql: str, column: str, *, period: str) -> None:
    """Refuse a statement that dropped the period the user asked for.

    Raised as a distinct error so the caller can offer the model one bounded
    repair -- the same single attempt schema mistakes already get -- rather than
    retrying blindly or, worse, returning the unfiltered answer.
    """
    if constrains_column(sql, column):
        return
    raise TemporalConstraintError(
        f"The query does not restrict {column} to the requested period "
        f"({period}), so it would answer over all of history."
    )
