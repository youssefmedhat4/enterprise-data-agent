"""Seed the metric registry from the checked-in demo catalog.

`GOVERNED_METRICS` stops being the runtime source of truth and becomes bootstrap
material for the default datasource. Seeded metrics are CERTIFIED because they
are the hand-written, already-executable definitions this system shipped with —
not because anything proposed them.

Business meaning and concepts are derived here rather than invented: aliases
become concepts (retrieval signal, not routing authority), and the description
plus grain and unit carry the meaning. Real deployments would author richer
business meaning through the admin surface.
"""

from __future__ import annotations

from uuid import UUID

from app.knowledge.metrics import (
    MetricDimensionSpec,
    MetricStatus,
    RegisteredMetric,
)
from app.metrics.catalog import GOVERNED_METRICS
from app.metrics.gateway import MetricDefinition

#: Curated business meaning and concept vocabulary for the demo metrics.
#:
#: Retrieval has to match a question asked in business language against a
#: definition written in engineering language. The catalog's descriptions are
#: precise but narrow ("Annual base salary across employee roster rows"), so a
#: question about "what the company commits to compensation" shares almost no
#: vocabulary with them. These entries supply the missing meaning.
#:
#: This is curation, not aliasing: the terms describe what the metric *means*
#: and are only ever retrieval signal. Nothing routes on an exact match.
_CURATED_MEANING: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "active_headcount": (
        "How many people the organization currently employs. The size of the "
        "active workforce, counting staff on the roster who have not left.",
        (
            "workforce size",
            "staff count",
            "number of people employed",
            "employee population",
            "team size",
        ),
        (
            "How many people work here?",
            "What is the size of each team?",
        ),
    ),
    "annual_base_payroll": (
        "The annual fixed compensation commitment for the employee roster. "
        "What the organization commits to spend on employee base pay each "
        "year, before bonuses and deductions.",
        (
            "employee compensation",
            "staff cost",
            "workforce expense",
            "salary expense",
            "personnel cost",
            "base pay commitment",
            "cost of employing people",
            "money spent on salaries",
        ),
        (
            "How much do employees cost yearly?",
            "Which team has the largest salary expense?",
            "Compare workforce compensation by department.",
        ),
    ),
    "net_payroll": (
        "What employees are actually paid after bonuses are added and "
        "deductions removed. Take-home payroll actually disbursed.",
        ("take home pay", "actual pay", "net wages", "disbursed payroll"),
        ("What did we actually pay out last month?",),
    ),
    "invoice_amount": (
        "Money billed to customers. Revenue invoiced on customer invoice "
        "lines, before payment is received.",
        ("billed revenue", "customer billing", "amount invoiced", "sales value"),
        ("How much did we bill each customer?",),
    ),
    "project_cost": (
        "Money spent delivering projects. Recorded expenditure charged "
        "against project work.",
        ("delivery spend", "project expense", "money spent on projects"),
        ("What are we spending on delivery?",),
    ),
    "project_margin": (
        "Profitability of project work. What is left from what was invoiced "
        "after project costs are subtracted.",
        ("project profit", "delivery profitability", "contribution", "gross margin"),
        ("Which projects are most profitable?",),
    ),
    "budget_utilization": (
        "How much of an approved project budget has been consumed by "
        "recorded spend.",
        ("budget consumption", "budget burn", "spend against budget"),
        ("Are any projects over budget?",),
    ),
}


def _business_meaning(definition: MetricDefinition) -> str:
    curated = _CURATED_MEANING.get(definition.id)
    if curated is not None:
        return curated[0]
    parts = [definition.description.rstrip(".")]
    if definition.unit:
        parts.append(f"Measured in {definition.unit}")
    if definition.grain:
        parts.append(f"at {definition.grain} grain")
    return ". ".join(part for part in parts if part) + "."


def registered_metrics_for_default_datasource(
    data_source_id: UUID,
) -> list[RegisteredMetric]:
    """Translate the demo catalog into CERTIFIED registry entries."""
    registered: list[RegisteredMetric] = []
    for definition in GOVERNED_METRICS:
        dimensions = tuple(
            MetricDimensionSpec(
                dimension_key=dimension.id,
                display_name=dimension.id.replace("_", " ").title(),
                description=dimension.description,
                data_type=dimension.data_type,
                allowed_operators=tuple(
                    operator.value for operator in dimension.allowed_operators
                ),
            )
            for dimension in definition.dimensions
        ) + tuple(
            MetricDimensionSpec(
                dimension_key=time_dimension.id,
                display_name=time_dimension.id.replace("_", " ").title(),
                description=time_dimension.description,
                data_type="time",
                is_time_dimension=True,
            )
            for time_dimension in definition.time_dimensions
        )
        registered.append(
            RegisteredMetric(
                data_source_id=data_source_id,
                metric_key=definition.id,
                display_name=definition.id.replace("_", " ").title(),
                description=definition.description,
                business_meaning=_business_meaning(definition),
                status=MetricStatus.CERTIFIED,
                semantic_expression=definition.formula,
                grain=definition.grain,
                unit=definition.unit,
                null_behavior=definition.null_behavior,
                owner="bootstrap",
                dimensions=dimensions,
                # Aliases become retrieval signal only. Nothing routes on
                # them; they carry the same weight as any other concept term.
                concepts=tuple(definition.aliases) + _concepts(definition),
                example_questions=_examples(definition),
            )
        )
    return registered


def _concepts(definition: MetricDefinition) -> tuple[str, ...]:
    curated = _CURATED_MEANING.get(definition.id)
    return curated[1] if curated else ()


def _examples(definition: MetricDefinition) -> tuple[str, ...]:
    curated = _CURATED_MEANING.get(definition.id)
    return curated[2] if curated else ()
