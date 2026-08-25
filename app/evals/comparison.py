import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.evals.models import EvaluationCase, Scalar

FIELD_GROUPS = (
    frozenset({"department", "department_name"}),
    frozenset({"customer", "customer_name"}),
    frozenset({"project", "project_code", "project_name"}),
    frozenset({"employee", "employee_name", "full_name"}),
    frozenset({"employee_count", "active_employees", "assigned_employees", "headcount", "count"}),
    frozenset({"customer_count", "total_customers", "count"}),
    frozenset({"hires", "hire_count", "employee_count", "count"}),
    frozenset({"invoice_total", "total_amount", "invoice_amount"}),
    frozenset({"invoiced_revenue", "revenue", "invoice_revenue"}),
    frozenset({"total_cost", "project_cost", "total_project_costs"}),
    frozenset({"net_payroll", "total_net_payroll"}),
    frozenset({"total_salary", "annual_salary", "total_payroll"}),
    frozenset({"average_salary", "avg_salary"}),
    frozenset({"budget_used_percent", "budget_utilization", "utilization_percent"}),
)

ENTITY_EQUIVALENTS = (
    frozenset({"P-101", "Retail Analytics Modernization"}),
    frozenset({"P-102", "Fleet Optimization Platform"}),
    frozenset({"P-099", "Factory Data Foundation"}),
)

ORDERING_PHRASES = (
    "ordered",
    "largest first",
    "highest first",
    "highest paid",
    "highest salary",
    "top two",
    "rank ",
    "second",
    "higher budget",
    "مرتبا",
    "الأعلى",
)


@dataclass(frozen=True)
class ComparisonResult:
    passed: bool
    reason: str
    ordering_required: bool
    normalized_actual: list[dict[str, Scalar]]
    normalized_expected: list[dict[str, Scalar]]


def compare_case_results(case: EvaluationCase, rows: list[dict[str, Any]]) -> ComparisonResult:
    actual = _normalize_rows(_normalize_result_shape(case, rows))
    expected = _expected_rows(case)
    ordering_required = _ordering_required(case.question)
    if case.expected_row_count != len(actual):
        return ComparisonResult(
            False,
            f"row_count_mismatch: expected {case.expected_row_count}, got {len(actual)}",
            ordering_required,
            actual,
            expected,
        )

    expected_groups: dict[int, list[Any]] = {}
    for assertion in case.assertions:
        expected_groups.setdefault(assertion.row_index, []).append(assertion)

    unmatched = set(range(len(actual)))
    for row_index, assertions in sorted(expected_groups.items()):
        candidate_indexes = [row_index] if ordering_required else sorted(unmatched)
        match = next(
            (
                candidate_index
                for candidate_index in candidate_indexes
                if candidate_index < len(actual)
                and _row_matches(
                    actual[candidate_index],
                    assertions,
                    tolerance=case.numeric_tolerance,
                )
            ),
            None,
        )
        if match is None:
            fields = ", ".join(assertion.field for assertion in assertions)
            return ComparisonResult(
                False,
                f"required_values_not_found: expected row {row_index} fields {fields}",
                ordering_required,
                actual,
                expected,
            )
        unmatched.discard(match)
    return ComparisonResult(
        True,
        "semantically_equivalent",
        ordering_required,
        actual,
        expected,
    )


def _row_matches(row: dict[str, Scalar], assertions: list[Any], *, tolerance: float) -> bool:
    used_fields: set[str] = set()
    for assertion in assertions:
        field = _matching_field(assertion.field, assertion.expected, row, used_fields)
        if field is None:
            return False
        if not _value_matches(
            row[field],
            assertion.expected,
            operator=assertion.operator,
            tolerance=tolerance,
        ):
            return False
        used_fields.add(field)
    return True


def _matching_field(
    expected_field: str,
    expected_value: Scalar,
    row: dict[str, Scalar],
    used_fields: set[str],
) -> str | None:
    if expected_field in row and expected_field not in used_fields:
        return expected_field
    canonical_expected = _canonical_field(expected_field)
    aliases = next(
        (group for group in FIELD_GROUPS if canonical_expected in group),
        frozenset({canonical_expected}),
    )
    candidates = [
        field for field in row if field not in used_fields and _canonical_field(field) in aliases
    ]
    matching = [
        field
        for field in candidates
        if _value_matches(row[field], expected_value, operator="eq", tolerance=0)
    ]
    if len(matching) == 1:
        return matching[0]
    if len(candidates) == 1:
        return candidates[0]
    if len(row) == 1:
        only_field = next(iter(row))
        return only_field if only_field not in used_fields else None
    return None


def _value_matches(
    actual: Scalar,
    expected: Scalar,
    *,
    operator: str,
    tolerance: float,
) -> bool:
    if operator == "contains":
        return str(expected).casefold() in str(actual).casefold()
    actual_number = _decimal(actual)
    expected_number = _decimal(expected)
    if actual_number is not None and expected_number is not None:
        effective_tolerance = Decimal(str(tolerance))
        return abs(actual_number - expected_number) <= effective_tolerance
    actual_date = _date_value(actual)
    expected_date = _date_value(expected)
    if actual_date is not None and expected_date is not None:
        return actual_date == expected_date
    if actual is None or expected is None:
        return actual is expected
    if str(actual).casefold() == str(expected).casefold():
        return True
    return any(str(actual) in group and str(expected) in group for group in ENTITY_EQUIVALENTS)


def _normalize_result_shape(
    case: EvaluationCase, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    expected_fields = {assertion.field for assertion in case.assertions}
    if {"active_customers", "inactive_customers"}.issubset(expected_fields):
        pivot: dict[str, Any] = {}
        for row in rows:
            status_field = next(
                (field for field in row if _canonical_field(field) == "status"), None
            )
            count_field = next(
                (
                    field
                    for field in row
                    if _canonical_field(field) in {"customer_count", "count", "total_customers"}
                ),
                None,
            )
            if status_field and count_field:
                pivot[f"{str(row[status_field]).casefold()}_customers"] = row[count_field]
        if pivot:
            return [pivot]
    if "period_start" in expected_fields:
        converted = []
        for row in rows:
            if "period_start" not in row and "year" in row and "month" in row:
                converted.append(
                    {
                        **row,
                        "period_start": f"{int(row['year']):04d}-{int(row['month']):02d}-01",
                    }
                )
            else:
                converted.append(row)
        return converted
    return rows


def _expected_rows(case: EvaluationCase) -> list[dict[str, Scalar]]:
    rows: list[dict[str, Scalar]] = [{} for _ in range(case.expected_row_count or 0)]
    for assertion in case.assertions:
        rows[assertion.row_index][assertion.field] = _normalize_scalar(assertion.expected)
    return rows


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Scalar]]:
    return [{field: _normalize_scalar(value) for field, value in row.items()} for row in rows]


def _normalize_scalar(value: Any) -> Scalar:
    if value is None or isinstance(value, (str, int, float, bool)):
        date_value = _date_value(value)
        return date_value or value
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return str(value)


def _canonical_field(field: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", field.casefold()).strip("_")
    if normalized.startswith("count_") or normalized.startswith("count("):
        return "count"
    return normalized


def _decimal(value: Scalar) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _date_value(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[ T].*)?", value)
    return match.group(1) if match else None


def _ordering_required(question: str) -> bool:
    normalized = question.casefold()
    return any(phrase in normalized for phrase in ORDERING_PHRASES)
