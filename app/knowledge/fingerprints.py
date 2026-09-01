"""Deterministic structural fingerprints for analytical requests.

A fingerprint answers "what shape of analysis was this?" rather than "what did
it say?". Two questions phrased differently that resolve to the same metrics at
the same grain produce the same fingerprint, which is what lets clustering group
them without relying on embedding similarity alone.

Two properties matter more than compactness.

**Deterministic.** The same plan yields the same string on every process and
every run: members are sorted, whitespace and case are normalised, and nothing
depends on dict ordering or object identity. Clustering keys off this, so drift
would silently fragment clusters.

**Free of literal values.** A filter contributes its dimension and operator but
never its operand. `customer = 'ACME Secret Account'` becomes
`filter:customer:eq`, so a fingerprint can be stored, indexed and shown to a
reviewer without carrying customer names, project names or salary figures out
of the database.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence

import sqlglot
from sqlglot import exp

#: Bumped when canonicalization changes meaning. Old fingerprints then stop
#: colliding with new ones instead of silently clustering different shapes.
FINGERPRINT_VERSION = "v1"

_WHITESPACE = re.compile(r"\s+")


def normalize_question(text: str) -> str:
    """Casefold and collapse whitespace. Not a fingerprint on its own."""
    return _WHITESPACE.sub(" ", text).strip().casefold()


def governed_fingerprint(
    *,
    metric_keys: Iterable[str],
    dimensions: Iterable[str],
    filter_dimensions: Iterable[tuple[str, str]] = (),
) -> str:
    """Fingerprint for a governed plan.

    `filter_dimensions` carries (dimension, operator) pairs only. The operand is
    deliberately not a parameter, so a caller cannot pass a customer name in by
    accident.
    """
    parts = [
        "route=governed",
        f"metrics={','.join(sorted(_clean(metric_keys)))}",
        f"dimensions={','.join(sorted(_clean(dimensions)))}",
    ]
    filters = sorted(
        f"filter:{dimension}:{operator}"
        for dimension, operator in _clean_pairs(filter_dimensions)
    )
    if filters:
        parts.append(f"filters={','.join(filters)}")
    return _finalize(parts)


def adhoc_fingerprint(sql: str, *, time_tags: tuple[str, ...] = ()) -> str:
    """Fingerprint for validated ad-hoc SQL, derived from its parsed shape.

    Only structure is read: which tables were touched, which aggregate functions
    were applied, what the grouping was, and whether the query ordered or
    limited. Literals are never read, so a WHERE clause contributes the column
    and comparison but not the value compared against.

    Falls back to a parse-failure marker rather than raising: a fingerprint is
    memory, and failing to remember must not fail a request that already
    succeeded.

    `time_tags` carries the *concept* of a period rather than its dates. Since
    literals are never read, "revenue year to date" and "revenue last month"
    would otherwise produce identical shapes and collapse into one cluster,
    while the same question asked in two months would already share one. The
    tag keeps both of those right: `time:YEAR_TO_DATE` recurs across months and
    stays distinct from `time:LAST_MONTH`.
    """
    try:
        statement = sqlglot.parse_one(sql)
    except Exception:
        return _finalize(["route=adhoc", "parse=failed", *sorted(time_tags)])
    if statement is None:
        return _finalize(["route=adhoc", "parse=failed", *sorted(time_tags)])

    tables = sorted(
        {
            _qualified_table(table)
            for table in statement.find_all(exp.Table)
            if _qualified_table(table)
        }
    )
    aggregates = sorted(
        {
            type(node).__name__.lower()
            for node in statement.find_all(exp.AggFunc)
        }
    )
    groupings = sorted(
        {
            _column_name(column)
            for group in statement.find_all(exp.Group)
            for column in group.find_all(exp.Column)
            if _column_name(column)
        }
    )
    # Column and comparison only. The operand is never read, so a filter on a
    # customer name contributes `customer:eq` and not the name itself.
    predicate_shapes: set[str] = set()
    for where in statement.find_all(exp.Where):
        for predicate in where.find_all(exp.Binary):
            comparison = type(predicate).__name__.lower()
            for column in predicate.find_all(exp.Column):
                name = _column_name(column)
                if name:
                    predicate_shapes.add(f"{name}:{comparison}")
    predicates = sorted(predicate_shapes)

    parts = [
        "route=adhoc",
        f"tables={','.join(tables)}",
        f"aggregates={','.join(aggregates)}",
        f"grouping={','.join(groupings)}",
    ]
    if predicates:
        parts.append(f"predicates={','.join(predicates)}")
    parts.extend(sorted(time_tags))
    if list(statement.find_all(exp.Join)):
        parts.append(f"joins={len(list(statement.find_all(exp.Join)))}")
    if statement.find(exp.Order) is not None:
        parts.append("ordered=true")
    return _finalize(parts)


def fingerprint_digest(fingerprint: str) -> str:
    """Stable short digest, for indexing where the full string is unwieldy."""
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]


def structures_are_compatible(left: str, right: str) -> bool:
    """Whether two fingerprints describe the same analytical shape.

    Exact equality by design. Near-miss structural matching would let a metric
    at one grain absorb a question asked at another, and a cluster that mixes
    grains cannot yield a single correct reusable definition.
    """
    return left == right


def _clean(values: Iterable[str]) -> list[str]:
    return [value.strip().casefold() for value in values if value and value.strip()]


def _clean_pairs(pairs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    return [
        (dimension.strip().casefold(), operator.strip().casefold())
        for dimension, operator in pairs
        if dimension and dimension.strip()
    ]


def _finalize(parts: Sequence[str]) -> str:
    return f"{FINGERPRINT_VERSION}|" + "|".join(parts)


def _qualified_table(table: exp.Table) -> str:
    name = table.name or ""
    schema = table.db or ""
    if not name:
        return ""
    return f"{schema}.{name}".strip(".").casefold()


def _column_name(column: exp.Column) -> str:
    return (column.name or "").casefold()
