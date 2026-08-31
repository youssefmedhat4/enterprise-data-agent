from uuid import uuid4

import pytest

from app.knowledge.composition import CompositionError, MetricResultSlice, compose
from app.knowledge.metrics import MetricStatus, RegisteredMetric
from app.knowledge.planner import (
    MetricFilterSelection,
    MetricIntentError,
    MetricIntentValidator,
    MetricSelection,
    candidate_prompt_payload,
)
from app.knowledge.retrieval import MetricCandidate
from app.knowledge.seed import registered_metrics_for_default_datasource

SOURCE_A = uuid4()
SOURCE_B = uuid4()


def candidates(*keys: str, data_source_id: uuid4 = SOURCE_A) -> list[MetricCandidate]:  # type: ignore[valid-type]
    metrics = {
        m.metric_key: m
        for m in registered_metrics_for_default_datasource(data_source_id)
    }
    return [
        MetricCandidate(metric=metrics[key], score=0.9, vector_similarity=0.8, lexical_score=0.7)
        for key in keys
    ]


def validate(selection: MetricSelection, offered: list[MetricCandidate]):  # type: ignore[no-untyped-def]
    return MetricIntentValidator().validate(
        selection=selection, data_source_id=SOURCE_A, candidates=offered
    )


# --------------------------------------------------------------------------
# The model may only select what it was offered
# --------------------------------------------------------------------------


def test_selecting_an_offered_metric_is_accepted() -> None:
    plan = validate(
        MetricSelection(
            intent="governed",
            metrics=["annual_base_payroll"],
            dimensions=["department"],
            confidence=0.95,
        ),
        candidates("annual_base_payroll", "active_headcount"),
    )

    assert plan.metric_keys == ("annual_base_payroll",)
    assert plan.dimensions == ("department",)
    assert plan.is_multi_metric is False


def test_model_cannot_invent_a_metric_id() -> None:
    with pytest.raises(MetricIntentError, match="not among the offered candidates"):
        validate(
            MetricSelection(intent="governed", metrics=["revenue_per_wizard"]),
            candidates("annual_base_payroll"),
        )


def test_model_cannot_select_a_metric_it_was_not_offered() -> None:
    """Present in the registry, but withheld from this caller's candidates."""
    with pytest.raises(MetricIntentError, match="not among the offered candidates"):
        validate(
            MetricSelection(intent="governed", metrics=["project_margin"]),
            candidates("annual_base_payroll"),
        )


def test_model_cannot_invent_a_dimension() -> None:
    with pytest.raises(MetricIntentError, match="Dimension 'wizard_house'"):
        validate(
            MetricSelection(
                intent="governed",
                metrics=["annual_base_payroll"],
                dimensions=["wizard_house"],
            ),
            candidates("annual_base_payroll"),
        )


def test_model_cannot_filter_on_an_unavailable_dimension() -> None:
    with pytest.raises(MetricIntentError, match="Filter dimension"):
        validate(
            MetricSelection(
                intent="governed",
                metrics=["annual_base_payroll"],
                filters=[
                    MetricFilterSelection(dimension="wizard_house", value_text="Ravenclaw")
                ],
            ),
            candidates("annual_base_payroll"),
        )


def test_metric_from_another_datasource_is_refused() -> None:
    foreign = candidates("annual_base_payroll", data_source_id=SOURCE_B)

    with pytest.raises(MetricIntentError, match="different datasource"):
        validate(
            MetricSelection(intent="governed", metrics=["annual_base_payroll"]),
            foreign,
        )


def test_uncertified_metric_is_refused_even_if_offered() -> None:
    proposal = RegisteredMetric(
        data_source_id=SOURCE_A,
        metric_key="revenue_per_active_employee",
        display_name="Revenue Per Active Employee",
        status=MetricStatus.PROPOSED,
    )
    offered = [
        MetricCandidate(metric=proposal, score=0.9, vector_similarity=0.9, lexical_score=0.9)
    ]

    with pytest.raises(MetricIntentError, match="not certified"):
        validate(
            MetricSelection(
                intent="governed", metrics=["revenue_per_active_employee"]
            ),
            offered,
        )


def test_adhoc_and_clarify_intents_are_not_planned_as_governed() -> None:
    for intent in ("adhoc", "clarify"):
        with pytest.raises(MetricIntentError, match="Only a governed selection"):
            validate(
                MetricSelection.model_validate({"intent": intent}),
                candidates("annual_base_payroll"),
            )


def test_governed_intent_requires_a_metric() -> None:
    with pytest.raises(MetricIntentError, match="at least one metric"):
        validate(
            MetricSelection(intent="governed", metrics=[]),
            candidates("annual_base_payroll"),
        )


def test_selection_contract_rejects_unknown_fields() -> None:
    """No field can carry SQL or an expression; extras are forbidden outright."""
    with pytest.raises(ValueError):
        MetricSelection.model_validate(
            {"intent": "governed", "metrics": ["x"], "sql": "SELECT 1"}
        )


def test_candidate_payload_exposes_no_physical_detail() -> None:
    payload = candidate_prompt_payload(candidates("annual_base_payroll"))

    serialized = str(payload).casefold()
    assert "metric_key" in str(payload)
    for leaked in ("analytics.", "select", "sum(", "employees", "formula"):
        assert leaked not in serialized


# --------------------------------------------------------------------------
# Multi-metric plans
# --------------------------------------------------------------------------


def test_multiple_metrics_share_a_dimension_and_are_planned_together() -> None:
    plan = validate(
        MetricSelection(
            intent="governed",
            metrics=["annual_base_payroll", "active_headcount"],
            dimensions=["department"],
        ),
        candidates("annual_base_payroll", "active_headcount"),
    )

    assert plan.is_multi_metric
    assert plan.metric_keys == ("annual_base_payroll", "active_headcount")


def test_dimension_not_shared_by_every_metric_is_refused() -> None:
    """A composite plan can only group by something all measures express."""
    with pytest.raises(MetricIntentError, match="not available on every selected metric"):
        validate(
            MetricSelection(
                intent="governed",
                metrics=["annual_base_payroll", "project_margin"],
                dimensions=["employment_status"],
            ),
            candidates("annual_base_payroll", "project_margin"),
        )


# --------------------------------------------------------------------------
# Composition: the fan-out guard
# --------------------------------------------------------------------------


def plan_for(*keys: str, dimensions: tuple[str, ...] = ("department",)):  # type: ignore[no-untyped-def]
    return validate(
        MetricSelection(
            intent="governed", metrics=list(keys), dimensions=list(dimensions)
        ),
        candidates(*keys),
    )


def test_composition_joins_metrics_one_to_one_on_the_dimension() -> None:
    plan = plan_for("annual_base_payroll", "active_headcount")

    composed = compose(
        plan,
        [
            MetricResultSlice(
                metric_key="annual_base_payroll",
                dimensions=("department",),
                rows=(
                    {"department": "Engineering", "value": "710000"},
                    {"department": "Sales", "value": "375000"},
                ),
            ),
            MetricResultSlice(
                metric_key="active_headcount",
                dimensions=("department",),
                rows=(
                    {"department": "Engineering", "value": 4},
                    {"department": "Sales", "value": 3},
                ),
            ),
        ],
    )

    assert composed == [
        {
            "department": "Engineering",
            "annual_base_payroll": "710000",
            "active_headcount": 4,
        },
        {"department": "Sales", "annual_base_payroll": "375000", "active_headcount": 3},
    ]


def test_composition_refuses_a_slice_that_is_not_aggregated_to_the_grain() -> None:
    """This is the exact shape of the old fan-out bug, now a loud failure."""
    plan = plan_for("annual_base_payroll", "project_margin")

    with pytest.raises(CompositionError, match="not\n?\\s*aggregated|more than one row"):
        compose(
            plan,
            [
                MetricResultSlice(
                    metric_key="annual_base_payroll",
                    dimensions=("department",),
                    rows=({"department": "Engineering", "value": "710000"},),
                ),
                MetricResultSlice(
                    metric_key="project_margin",
                    dimensions=("department",),
                    # Per-project rows leaking into a department-grain request.
                    rows=(
                        {"department": "Engineering", "value": "70800"},
                        {"department": "Engineering", "value": "45500"},
                    ),
                ),
            ],
        )


def test_composition_refuses_a_slice_grouped_by_the_wrong_dimensions() -> None:
    plan = plan_for("annual_base_payroll")

    with pytest.raises(CompositionError, match="requires"):
        compose(
            plan,
            [
                MetricResultSlice(
                    metric_key="annual_base_payroll",
                    dimensions=("project",),
                    rows=({"project": "Falcon", "value": "1"},),
                )
            ],
        )


def test_composition_requires_every_planned_metric() -> None:
    plan = plan_for("annual_base_payroll", "active_headcount")

    with pytest.raises(CompositionError, match="No result supplied"):
        compose(
            plan,
            [
                MetricResultSlice(
                    metric_key="annual_base_payroll",
                    dimensions=("department",),
                    rows=({"department": "Engineering", "value": "1"},),
                )
            ],
        )


def test_a_dimension_present_for_only_one_metric_still_yields_a_row() -> None:
    """Missing measures are null; the row is not silently dropped."""
    plan = plan_for("annual_base_payroll", "active_headcount")

    composed = compose(
        plan,
        [
            MetricResultSlice(
                metric_key="annual_base_payroll",
                dimensions=("department",),
                rows=({"department": "Engineering", "value": "710000"},),
            ),
            MetricResultSlice(
                metric_key="active_headcount",
                dimensions=("department",),
                rows=(
                    {"department": "Engineering", "value": 4},
                    {"department": "Finance", "value": 2},
                ),
            ),
        ],
    )

    assert composed[-1] == {
        "department": "Finance",
        "annual_base_payroll": None,
        "active_headcount": 2,
    }
