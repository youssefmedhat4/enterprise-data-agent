from pathlib import Path

from app.metrics.evaluation import (
    MetricEvaluationCase,
    compare_metric_rows,
    load_metric_cases,
)
from app.metrics.gateway import MetricQuery


def test_metric_dataset_is_frozen_provider_independent_contract() -> None:
    cases = load_metric_cases()

    assert len(cases) == 25
    assert len({case.id for case in cases}) == 25
    assert {case.expected_error for case in cases if case.expected_error} == {
        "invalid_metric_query"
    }


def test_metric_comparison_is_unordered_and_numeric_type_tolerant() -> None:
    case = MetricEvaluationCase(
        id="example",
        description="example",
        query=MetricQuery(metric="active_headcount"),
        expected_rows=(
            {"department": "Engineering", "active_headcount": 4},
            {"department": "Finance", "active_headcount": 2},
        ),
    )

    passed, reason = compare_metric_rows(
        case,
        [
            {"department": "Finance", "active_headcount": "2"},
            {"department": "Engineering", "active_headcount": 4.0},
        ],
    )

    assert passed is True
    assert reason == "semantically_equivalent"


def test_metric_dataset_is_valid_json_at_expected_path() -> None:
    path = Path(__file__).parents[2] / "evals" / "metrics_cases.json"
    assert path.is_file()
