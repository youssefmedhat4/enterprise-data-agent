"""Composite governed execution for multi-metric plans.

The previous architecture sent any multi-metric request to ad-hoc SQL, which is
how the department query ended up joining department aggregates against
per-project rows and returning six rows for four departments — every payroll
figure repeated once per project.

The fix is structural rather than cosmetic. Independent facts are never joined
at row level. Each certified metric is executed as its own governed query,
already aggregated by the metric provider to the requested grain, and the
results are joined **on the dimension key only**, after aggregation. A fact
table is therefore never multiplied by another fact table, so there is nothing
to deduplicate and no reason to reach for SELECT DISTINCT or SUM(DISTINCT ...).

Those hacks are not merely discouraged here; they are unreachable. This layer
never emits SQL. It composes already-validated governed results in Python, and
each individual query still goes through the provider, SQLGlot validation, and
the read-only role exactly as a single-metric query does.

Gemini is not asked to reconstruct a certified formula as SQL. It selects which
certified metrics to combine; the provider owns how each is computed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.knowledge.planner import ValidatedMetricPlan


class CompositionError(RuntimeError):
    """Raised when governed results cannot be composed safely."""


@dataclass(frozen=True, slots=True)
class MetricResultSlice:
    """One metric's governed result, already aggregated to the final grain."""

    metric_key: str
    dimensions: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]

    def key_for(self, row: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(row.get(dimension) for dimension in self.dimensions)


def compose(
    plan: ValidatedMetricPlan,
    slices: list[MetricResultSlice],
) -> list[dict[str, Any]]:
    """Join per-metric governed results on the requested dimensions.

    Every slice must already be grouped by exactly `plan.dimensions`. That is
    the precondition that makes the join safe: one row per dimension tuple per
    metric means the join is one-to-one and cannot fan out.
    """
    if not slices:
        return []
    expected = tuple(plan.dimensions)

    by_metric: dict[str, MetricResultSlice] = {}
    for result_slice in slices:
        if result_slice.dimensions != expected:
            raise CompositionError(
                f"Metric {result_slice.metric_key!r} was aggregated by "
                f"{result_slice.dimensions} but the plan requires {expected}."
            )
        _assert_unique_grain(result_slice)
        by_metric[result_slice.metric_key] = result_slice

    missing = set(plan.metric_keys) - set(by_metric)
    if missing:
        raise CompositionError(f"No result supplied for metrics: {sorted(missing)}.")

    # Ordered union of dimension tuples across metrics. A dimension value that
    # appears for one metric but not another must still produce a row, with the
    # absent measure left null rather than dropping the row entirely.
    ordered_keys: list[tuple[Any, ...]] = []
    seen: set[tuple[Any, ...]] = set()
    for metric_key in plan.metric_keys:
        for row in by_metric[metric_key].rows:
            key = by_metric[metric_key].key_for(row)
            if key not in seen:
                seen.add(key)
                ordered_keys.append(key)

    indexed = {
        metric_key: {result.key_for(row): row for row in result.rows}
        for metric_key, result in by_metric.items()
    }

    composed: list[dict[str, Any]] = []
    for key in ordered_keys:
        composed_row: dict[str, Any] = dict(zip(expected, key, strict=True))
        for metric_key in plan.metric_keys:
            source = indexed[metric_key].get(key)
            composed_row[metric_key] = (
                None if source is None else _measure_of(source, metric_key)
            )
        composed.append(composed_row)
    return composed


def _assert_unique_grain(result_slice: MetricResultSlice) -> None:
    """Refuse a slice that is not already one row per dimension tuple.

    This is the fan-out guard. If a provider returns per-project rows for a
    request grouped by department, composing them would multiply every other
    metric. Failing loudly here is the whole point: the previous bug was silent.
    """
    seen: set[tuple[Any, ...]] = set()
    for row in result_slice.rows:
        key = result_slice.key_for(row)
        if key in seen:
            raise CompositionError(
                f"Metric {result_slice.metric_key!r} returned more than one row "
                f"for a single {result_slice.dimensions} group, so it is not "
                "aggregated to the requested grain."
            )
        seen.add(key)


def _measure_of(row: dict[str, Any], metric_key: str) -> Any:
    """Pull the measure value out of a governed result row."""
    if metric_key in row:
        return row[metric_key]
    measures = [key for key in row if key not in {"value", metric_key}]
    if "value" in row:
        return row["value"]
    if len(measures) == 1:
        return row[measures[0]]
    raise CompositionError(
        f"Cannot identify the measure column for {metric_key!r} in a governed row."
    )


def coerce_number(value: Any) -> Decimal | None:
    """Normalize a governed measure for comparison.

    PostgreSQL `numeric` arrives as a string while `int8` arrives as a number,
    so a caller comparing them without coercion silently gets it wrong.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
