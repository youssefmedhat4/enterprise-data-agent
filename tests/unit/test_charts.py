import pytest
from pydantic import ValidationError

from app.agent.charts import ChartValidator
from app.contracts.analytics import ChartSpec


def test_valid_chart_fields_and_numeric_measure_are_accepted() -> None:
    chart = ChartSpec(chart_type="bar", x="department", y="payroll", title="Payroll")

    validated, warnings = ChartValidator().validate(
        chart,
        [{"department": "Engineering", "payroll": "100.00"}],
    )

    assert validated == chart
    assert warnings == []


@pytest.mark.parametrize(
    ("chart", "rows"),
    [
        (
            ChartSpec(chart_type="line", x="month", y="missing", title="Trend"),
            [{"month": "Jan", "payroll": 100}],
        ),
        (
            ChartSpec(chart_type="pie", x="department", y="owner", title="Owners"),
            [{"department": "Engineering", "owner": "Maya"}],
        ),
        (
            ChartSpec(chart_type="donut", x="department", y="payroll", title="Payroll"),
            [],
        ),
        (
            ChartSpec(chart_type="pie", x="department", y="payroll", title="Payroll"),
            [{"department": "Engineering", "payroll": -1}],
        ),
    ],
)
def test_invalid_or_empty_chart_is_omitted_with_warning(
    chart: ChartSpec,
    rows: list[dict[str, object]],
) -> None:
    validated, warnings = ChartValidator().validate(chart, rows)

    assert validated is None
    assert warnings


def test_table_only_is_represented_by_no_chart() -> None:
    assert ChartValidator().validate(None, [{"value": 1}]) == (None, [])


def test_chart_contract_rejects_unsupported_types_and_executable_fields() -> None:
    with pytest.raises(ValidationError):
        ChartSpec.model_validate(
            {
                "type": "javascript",
                "x": "department",
                "y": "payroll",
                "title": "Unsafe",
                "code": "alert(1)",
            }
        )
