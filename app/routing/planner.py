from __future__ import annotations

import re
from time import perf_counter

from app.agent.context import AnalyticalContext
from app.data.gateway import TableMetadata
from app.metrics.catalog import metric_definition, validate_metric_query
from app.metrics.gateway import (
    MetricFilter,
    MetricFilterOperator,
    MetricOrder,
    MetricOrderDirection,
    MetricQuery,
    MetricTimeGrain,
)
from app.routing.contracts import (
    MetricPlanningError,
    MetricRequestPlan,
    QueryRoute,
    RouteDecision,
    RouteReasonCode,
)
from app.routing.router import normalize_text
from app.semantic.entities import EntityResolver

_TIME_GRAINS = {
    "by year": MetricTimeGrain.YEAR,
    "by quarter": MetricTimeGrain.QUARTER,
    "by month": MetricTimeGrain.MONTH,
    "by week": MetricTimeGrain.WEEK,
    "by day": MetricTimeGrain.DAY,
    "حسب السنه": MetricTimeGrain.YEAR,
    "حسب الشهر": MetricTimeGrain.MONTH,
    "شهريا": MetricTimeGrain.MONTH,
}


class MetricRequestPlanner:
    """Produce only catalog-validated governed members, never SQL or formulas."""

    def __init__(self, entity_resolver: EntityResolver | None = None) -> None:
        self._entities = entity_resolver or EntityResolver()

    def plan(
        self,
        question: str,
        decision: RouteDecision,
        *,
        prior_context: AnalyticalContext | None = None,
        authorized_tables: list[TableMetadata] | None = None,
    ) -> MetricRequestPlan:
        started = perf_counter()
        if decision.route != QueryRoute.GOVERNED_METRIC:
            raise MetricPlanningError("Only governed metric routes can be planned.")
        if len(decision.metric_candidates) != 1:
            raise MetricPlanningError("A metric request must resolve to exactly one metric.")
        metric_id = decision.metric_candidates[0]
        definition = metric_definition(metric_id)
        prior_query = (
            prior_context.metric_query
            if decision.reason_code == RouteReasonCode.FOLLOWUP_REFERENCE
            and prior_context is not None
            else None
        )
        normalized = normalize_text(question)
        dimensions = list(prior_query.dimensions if prior_query is not None else ())
        for dimension in definition.dimensions:
            aliases = (dimension.id, *dimension.aliases)
            if (
                any(
                    _requests_dimension(normalized, normalize_text(alias))
                    for alias in aliases
                )
                or (
                    normalized.startswith("top ")
                    and any(
                        f"{normalize_text(alias)}s" in normalized
                        for alias in aliases
                    )
                )
            ) and dimension.id not in dimensions:
                dimensions.append(dimension.id)

        filters = list(prior_query.filters if prior_query is not None else ())
        if "active customer" in normalized or "العملاء النشط" in normalized:
            raise MetricPlanningError(
                "Customer status is not an allowed governed filter for this metric."
            )
        # Entity values are resolved against the live, authorized datasource
        # rather than a hardcoded list, so this works for any schema. Only an
        # unambiguous match becomes a filter: an ambiguous or absent one leaves
        # the query unfiltered instead of guessing.
        for dimension in definition.dimensions:
            resolution = self._entities.resolve(
                user_text=question,
                authorized_tables=authorized_tables or [],
                concept=dimension.id,
            )
            match = resolution.resolved
            if match is None:
                continue
            filters = [item for item in filters if item.dimension != dimension.id]
            filters.append(
                MetricFilter(
                    dimension=dimension.id,
                    operator=MetricFilterOperator.EQ,
                    values=(match.value,),
                )
            )

        time_dimension = prior_query.time_dimension if prior_query is not None else None
        time_grain = prior_query.time_grain if prior_query is not None else None
        for phrase, grain in _TIME_GRAINS.items():
            if normalize_text(phrase) in normalized:
                if not definition.time_dimensions:
                    raise MetricPlanningError(
                        f"Metric '{metric_id}' has no governed time dimension."
                    )
                time_dimension = definition.time_dimensions[0].id
                time_grain = grain
                break

        limit = prior_query.limit if prior_query is not None else 100
        top_match = re.search(r"\btop\s+(\d{1,3})\b", normalized)
        if top_match:
            limit = min(int(top_match.group(1)), 100)
        elif "highest" in normalized or "اعلي" in normalized:
            limit = 1
        order = prior_query.order if prior_query is not None else ()
        if top_match or limit == 1:
            order = (
                MetricOrder(member=metric_id, direction=MetricOrderDirection.DESC),
            )

        query = MetricQuery(
            metric=metric_id,
            dimensions=tuple(dimensions),
            filters=tuple(filters),
            time_dimension=time_dimension,
            time_grain=time_grain,
            date_range=prior_query.date_range if prior_query is not None else None,
            order=order,
            limit=limit,
        )
        validate_metric_query(query)
        return MetricRequestPlan(
            query=query,
            planning_latency_ms=round((perf_counter() - started) * 1000, 3),
            used_prior_context=prior_query is not None,
        )


def _requests_dimension(question: str, alias: str) -> bool:
    return any(
        phrase in question
        for phrase in (f"by {alias}", f"per {alias}", f"حسب {alias}")
    )
