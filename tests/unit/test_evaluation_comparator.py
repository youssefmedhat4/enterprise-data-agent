"""What counts as a pass.

A benchmark that reports a wrong number as correct is worse than no benchmark:
it turns an unnoticed regression into a confident claim that nothing broke. So
most of these are about what must *not* pass.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.knowledge.evaluation import (
    CaseResult,
    EvaluationCase,
    EvaluationError,
    EvaluationRun,
    ExpectationKind,
    Movement,
    Outcome,
    compare,
    movements,
    validate_expected,
)

SOURCE = uuid4()


def _case(
    expectation: ExpectationKind,
    expected: dict[str, Any],
    *,
    tolerance: str = "0",
    ordered: bool = False,
    route: str | None = None,
    metrics: tuple[str, ...] = (),
) -> EvaluationCase:
    return EvaluationCase(
        data_source_id=SOURCE,
        name="case",
        question="q",
        expectation=expectation,
        expected=expected,
        tolerance=Decimal(tolerance),
        ordered=ordered,
        expected_route=route,
        expected_metric_ids=metrics,
    )


# --- scalars -----------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        6345000,
        "6345000",
        "6345000.00",
        Decimal("6345000.0000"),
        "6,345,000.00",
        "$6345000",
    ],
)
def test_a_number_matches_however_the_driver_rendered_it(value: Any) -> None:
    """`numeric` arrives as a string and `int8` as an int; both are the answer."""
    case = _case(ExpectationKind.SCALAR, {"value": "6345000"})

    assert compare(case, rows=[{"payroll": value}]).outcome is Outcome.PASS


@pytest.mark.parametrize("value", [6345001, "6344999.99", 4395000, 0, None, "n/a"])
def test_a_different_number_never_passes(value: Any) -> None:
    case = _case(ExpectationKind.SCALAR, {"value": "6345000"})

    assert compare(case, rows=[{"payroll": value}]).outcome is Outcome.FAIL


def test_tolerance_is_absolute_and_only_what_was_configured() -> None:
    case = _case(ExpectationKind.SCALAR, {"value": "100.00"}, tolerance="0.01")

    assert compare(case, rows=[{"v": "100.01"}]).outcome is Outcome.PASS
    assert compare(case, rows=[{"v": "99.99"}]).outcome is Outcome.PASS
    assert compare(case, rows=[{"v": "100.02"}]).outcome is Outcome.FAIL


def test_a_scalar_case_is_not_satisfied_by_a_table() -> None:
    case = _case(ExpectationKind.SCALAR, {"value": "42"})

    assert compare(case, rows=[{"a": 42}, {"a": 42}]).outcome is Outcome.FAIL
    assert compare(case, rows=[{"a": 42, "b": 1}]).outcome is Outcome.FAIL
    assert compare(case, rows=[]).outcome is Outcome.FAIL


# --- tables ------------------------------------------------------------------


def test_column_order_does_not_decide_a_case() -> None:
    """`SELECT a, b` and `SELECT b, a` answer the same question."""
    case = _case(
        ExpectationKind.TABLE,
        {"rows": [{"unit": "OU2100", "headcount": 4}]},
    )

    assert (
        compare(case, rows=[{"headcount": 4, "unit": "OU2100"}]).outcome
        is Outcome.PASS
    )


def test_row_order_is_ignored_unless_the_case_is_about_ranking() -> None:
    rows = [{"unit": "B", "n": 2}, {"unit": "A", "n": 1}]
    unordered = _case(
        ExpectationKind.TABLE, {"rows": [{"unit": "A", "n": 1}, {"unit": "B", "n": 2}]}
    )
    ranked = _case(
        ExpectationKind.TABLE,
        {"rows": [{"unit": "A", "n": 1}, {"unit": "B", "n": 2}]},
        ordered=True,
    )

    assert compare(unordered, rows=rows).outcome is Outcome.PASS
    assert compare(ranked, rows=rows).outcome is Outcome.FAIL, (
        "a ranking case accepted the wrong order"
    )


def test_a_missing_row_fails_even_when_every_other_row_matches() -> None:
    case = _case(
        ExpectationKind.TABLE,
        {"rows": [{"unit": "A", "n": 1}, {"unit": "B", "n": 2}]},
    )

    assert compare(case, rows=[{"unit": "A", "n": 1}]).outcome is Outcome.FAIL


def test_an_extra_column_in_the_answer_is_not_a_regression() -> None:
    """The case asserts what it named; a new column is not a wrong answer."""
    case = _case(ExpectationKind.TABLE, {"rows": [{"unit": "A", "n": 1}]})

    assert (
        compare(case, rows=[{"unit": "A", "n": 1, "code": "OU1000"}]).outcome
        is Outcome.PASS
    )


def test_a_duplicated_row_cannot_satisfy_two_expected_rows() -> None:
    case = _case(
        ExpectationKind.TABLE,
        {"rows": [{"unit": "A", "n": 1}, {"unit": "B", "n": 2}]},
    )

    assert (
        compare(case, rows=[{"unit": "A", "n": 1}, {"unit": "A", "n": 1}]).outcome
        is Outcome.FAIL
    )


# --- shape, route and metrics ------------------------------------------------


def test_row_count_and_empty_expectations() -> None:
    counted = _case(ExpectationKind.ROW_COUNT, {"value": 3})
    empty = _case(ExpectationKind.EMPTY, {})

    assert compare(counted, rows=[{"a": 1}] * 3).outcome is Outcome.PASS
    assert compare(counted, rows=[{"a": 1}] * 4).outcome is Outcome.FAIL
    assert compare(empty, rows=[]).outcome is Outcome.PASS
    assert compare(empty, rows=[{"a": 1}]).outcome is Outcome.FAIL


def test_a_right_number_by_the_wrong_route_is_a_failure() -> None:
    """A governed metric silently becoming ad-hoc SQL is exactly a regression."""
    case = _case(
        ExpectationKind.SCALAR, {"value": "1565000"}, route="governed_metric"
    )

    passed = compare(case, rows=[{"v": 1565000}], route="governed_metric")
    slipped = compare(case, rows=[{"v": 1565000}], route="adhoc_analytics")

    assert passed.outcome is Outcome.PASS
    assert slipped.outcome is Outcome.FAIL
    assert slipped.detail is not None and "adhoc_analytics" in slipped.detail


def test_a_case_can_require_the_metric_it_was_meant_to_exercise() -> None:
    case = _case(
        ExpectationKind.SCALAR, {"value": "1"}, metrics=("annual_base_payroll",)
    )

    assert (
        compare(case, rows=[{"v": 1}], metric_ids=("annual_base_payroll",)).outcome
        is Outcome.PASS
    )
    assert compare(case, rows=[{"v": 1}], metric_ids=()).outcome is Outcome.FAIL


# --- what may be stored ------------------------------------------------------


def test_an_expectation_that_cannot_be_compared_is_refused() -> None:
    """Otherwise the case fails forever or passes vacuously, and gets ignored."""
    with pytest.raises(EvaluationError):
        validate_expected(ExpectationKind.SCALAR, {})
    with pytest.raises(EvaluationError):
        validate_expected(ExpectationKind.TABLE, {"rows": []})
    with pytest.raises(EvaluationError):
        validate_expected(ExpectationKind.ROW_COUNT, {"value": "many"})
    with pytest.raises(EvaluationError):
        validate_expected(ExpectationKind.ROW_COUNT, {"value": -1})


def test_a_table_expectation_is_a_comparison_value_not_an_export() -> None:
    with pytest.raises(EvaluationError, match="at most"):
        validate_expected(
            ExpectationKind.TABLE, {"rows": [{"a": index} for index in range(500)]}
        )


# --- regression classification ----------------------------------------------


def _run(outcomes: dict[Any, Outcome]) -> EvaluationRun:
    return EvaluationRun(
        data_source_id=SOURCE,
        model_profile="gemini_pro",
        results=tuple(
            CaseResult(case_id=case_id, outcome=outcome)
            for case_id, outcome in outcomes.items()
        ),
    )


def test_a_case_that_stopped_passing_is_named_a_regression() -> None:
    kept, broke, fixed, always, added = (uuid4() for _ in range(5))
    previous = _run(
        {
            kept: Outcome.PASS,
            broke: Outcome.PASS,
            fixed: Outcome.FAIL,
            always: Outcome.FAIL,
        }
    )
    current = _run(
        {
            kept: Outcome.PASS,
            broke: Outcome.FAIL,
            fixed: Outcome.PASS,
            always: Outcome.FAIL,
            added: Outcome.PASS,
        }
    )

    moved = movements(current, previous)

    assert moved[kept] is Movement.UNCHANGED_PASS
    assert moved[broke] is Movement.REGRESSION
    assert moved[fixed] is Movement.IMPROVED
    assert moved[always] is Movement.UNCHANGED_FAIL
    assert moved[added] is Movement.NEW


def test_the_first_run_has_nothing_to_regress_from() -> None:
    case_id = uuid4()

    assert movements(_run({case_id: Outcome.FAIL}), None)[case_id] is Movement.NEW
