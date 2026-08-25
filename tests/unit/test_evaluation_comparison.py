from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.evals.comparison import compare_case_results
from app.evals.loader import load_evaluation_cases

CASES = {
    case.id: case
    for case in load_evaluation_cases(Path(__file__).parents[2] / "evals" / "cases.json")
}


def test_unordered_relational_rows_are_compared_as_a_multiset() -> None:
    result = compare_case_results(
        CASES["aggregate_customers_by_status"],
        [
            {"status": "inactive", "customer_count": 1},
            {"status": "active", "customer_count": 2},
        ],
    )

    assert result.passed is True
    assert result.ordering_required is False


def test_alias_numeric_types_and_extra_columns_are_semantically_equivalent() -> None:
    result = compare_case_results(
        CASES["aggregate_invoice_totals"],
        [
            {
                "invoice_number": "INV-2025-002",
                "total_amount": Decimal("110000"),
                "currency": "USD",
            },
            {
                "invoice_number": "INV-2025-001",
                "total_amount": 95000.0,
                "currency": "USD",
            },
        ],
    )

    assert result.passed is True
    assert result.ordering_required is True


def test_unnamed_single_aggregate_column_is_accepted() -> None:
    result = compare_case_results(
        CASES["temporal_hires_in_2022"],
        [{"count(id)": 3}],
    )

    assert result.passed is True


def test_date_and_datetime_representations_are_normalized() -> None:
    case = CASES["temporal_payroll_by_month"]
    result = compare_case_results(
        case,
        [
            {"period_start": datetime(2025, 1, 1, 12), "net_payroll": Decimal("107125")},
            {"period_start": "2025-02-01T00:00:00", "net_payroll": Decimal("114375.00")},
        ],
    )

    assert result.passed is True


def test_explicit_aggregate_shape_normalization_pivots_status_counts() -> None:
    result = compare_case_results(
        CASES["compare_customer_status_counts"],
        [
            {"status": "active", "customer_count": 2},
            {"status": "inactive", "customer_count": 1},
        ],
    )

    assert result.passed is True


def test_project_name_is_an_acceptable_project_identifier() -> None:
    result = compare_case_results(
        CASES["temporal_projects_active_on_date"],
        [
            {"name": "Fleet Optimization Platform"},
            {"name": "Retail Analytics Modernization"},
        ],
    )

    assert result.passed is True


def test_missing_required_information_remains_a_failure() -> None:
    result = compare_case_results(
        CASES["aggregate_invoice_totals"],
        [
            {"invoice_number": "INV-2025-002", "currency": "USD"},
            {"invoice_number": "INV-2025-001", "currency": "USD"},
        ],
    )

    assert result.passed is False
    assert result.reason.startswith("required_values_not_found")


def test_required_ordering_is_not_ignored() -> None:
    result = compare_case_results(
        CASES["aggregate_invoice_totals"],
        [
            {"invoice_number": "INV-2025-001", "invoice_total": 95000},
            {"invoice_number": "INV-2025-002", "invoice_total": 110000},
        ],
    )

    assert result.passed is False
