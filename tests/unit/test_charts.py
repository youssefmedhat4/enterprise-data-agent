from typing import Any

import pytest
from pydantic import ValidationError

from app.agent.charts import MAX_PART_TO_WHOLE_SLICES, ChartValidator
from app.contracts.analytics import ChartSpec

CATEGORY_ROWS = [
    {"department": "Engineering", "payroll": "710000.00", "headcount": 5, "lead": "Maya"},
    {"department": "Sales", "payroll": "375000.00", "headcount": 3, "lead": "Noura"},
]
TREND_ROWS = [
    {"month": "2026-01", "revenue": 100, "cost": 60},
    {"month": "2026-02", "revenue": 140, "cost": 70},
]


def test_single_measure_bar_is_accepted_unchanged() -> None:
    chart = ChartSpec(
        chart_type="bar", x="department", measures=["payroll"], title="Payroll"
    )

    validated, warnings = ChartValidator().validate(chart, CATEGORY_ROWS)

    assert validated == chart
    assert warnings == []


@pytest.mark.parametrize(
    "chart",
    [
        ChartSpec(
            chart_type="line", x="month", measures=["revenue", "cost"], title="Trend"
        ),
        ChartSpec(
            chart_type="area",
            x="month",
            measures=["revenue", "cost"],
            mode="stacked",
            title="Composition",
        ),
        ChartSpec(chart_type="scatter", x="revenue", measures=["cost"], title="Relation"),
    ],
)
def test_multi_measure_and_scatter_types_are_accepted(chart: ChartSpec) -> None:
    validated, warnings = ChartValidator().validate(chart, TREND_ROWS)

    assert validated is not None
    assert warnings == []


def test_horizontal_ranking_bar_is_accepted() -> None:
    chart = ChartSpec(
        chart_type="bar",
        x="department",
        measures=["payroll"],
        orientation="horizontal",
        sort="descending",
        value_format="currency",
        title="Ranking",
    )

    validated, warnings = ChartValidator().validate(chart, CATEGORY_ROWS)

    assert validated is not None
    assert validated.orientation == "horizontal"
    assert warnings == []


@pytest.mark.parametrize(
    ("chart", "rows"),
    [
        pytest.param(
            ChartSpec(chart_type="line", x="month", measures=["missing"], title="T"),
            TREND_ROWS,
            id="unknown_measure_column",
        ),
        pytest.param(
            ChartSpec(chart_type="bar", x="missing", measures=["revenue"], title="T"),
            TREND_ROWS,
            id="unknown_x_column",
        ),
        pytest.param(
            ChartSpec(
                chart_type="bar",
                x="month",
                measures=["revenue"],
                series="missing",
                title="T",
            ),
            TREND_ROWS,
            id="unknown_series_column",
        ),
        pytest.param(
            ChartSpec(chart_type="pie", x="department", measures=["lead"], title="T"),
            CATEGORY_ROWS,
            id="non_numeric_measure",
        ),
        pytest.param(
            ChartSpec(chart_type="scatter", x="department", measures=["payroll"], title="T"),
            CATEGORY_ROWS,
            id="scatter_with_non_numeric_x",
        ),
        pytest.param(
            ChartSpec(chart_type="pie", x="department", measures=["payroll"], title="T"),
            [{"department": "Engineering", "payroll": -1}],
            id="negative_part_to_whole",
        ),
        pytest.param(
            ChartSpec(chart_type="donut", x="department", measures=["payroll"], title="T"),
            [],
            id="empty_result",
        ),
    ],
)
def test_incompatible_chart_is_omitted_with_warning(
    chart: ChartSpec,
    rows: list[dict[str, Any]],
) -> None:
    validated, warnings = ChartValidator().validate(chart, rows)

    assert validated is None
    assert warnings and warnings[0].startswith("Visualization omitted because")


def test_high_cardinality_part_to_whole_is_rejected() -> None:
    rows: list[dict[str, Any]] = [
        {"department": f"Team {index}", "payroll": index + 1}
        for index in range(MAX_PART_TO_WHOLE_SLICES + 1)
    ]
    chart = ChartSpec(chart_type="pie", x="department", measures=["payroll"], title="T")

    validated, warnings = ChartValidator().validate(chart, rows)

    assert validated is None
    assert warnings


def test_cosmetic_mismatches_are_normalised_rather_than_dropped() -> None:
    """A field that does not apply should not cost the user a usable chart."""
    chart = ChartSpec(
        chart_type="pie",
        x="department",
        measures=["payroll"],
        orientation="horizontal",
        mode="stacked",
        title="Share",
    )

    validated, warnings = ChartValidator().validate(chart, CATEGORY_ROWS)

    assert validated is not None
    assert validated.orientation == "vertical"
    assert validated.mode == "grouped"
    assert warnings == []


def test_table_only_is_represented_by_no_chart() -> None:
    assert ChartValidator().validate(None, [{"value": 1}]) == (None, [])


def test_part_to_whole_display_defaults_to_value_and_percent() -> None:
    """Raw amounts should show their own value beside the derived share."""
    chart = ChartSpec(chart_type="donut", x="department", measures=["payroll"], title="S")

    assert chart.part_to_whole_display == "value_and_percent"


def test_value_format_and_part_to_whole_display_are_independent() -> None:
    """A currency measure can carry a share without becoming a percent field.

    This is the contract-level fix for the bug where `value_format=percent` was
    overloaded to mean "show the share", rendering a 710000 payroll as 710,000%.
    """
    chart = ChartSpec(
        chart_type="donut",
        x="department",
        measures=["payroll"],
        value_format="currency",
        part_to_whole_display="percent",
        title="Share",
    )

    validated, warnings = ChartValidator().validate(chart, CATEGORY_ROWS)

    assert validated is not None
    assert validated.value_format == "currency"
    assert validated.part_to_whole_display == "percent"
    assert warnings == []


def test_already_percent_slices_do_not_also_derive_a_share() -> None:
    """Two percentages of different wholes is worse than showing neither."""
    chart = ChartSpec(
        chart_type="pie",
        x="department",
        measures=["utilization"],
        value_format="percent",
        part_to_whole_display="value_and_percent",
        title="Utilization",
    )

    validated, warnings = ChartValidator().validate(
        chart,
        [{"department": "Engineering", "utilization": 80}],
    )

    assert validated is not None
    assert validated.part_to_whole_display == "value"
    assert warnings == []


def test_part_to_whole_display_rejects_unknown_modes() -> None:
    with pytest.raises(ValidationError):
        ChartSpec.model_validate(
            {
                "type": "donut",
                "x": "department",
                "measures": ["payroll"],
                "title": "S",
                "part_to_whole_display": "share_of_everything",
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {"type": "javascript", "x": "d", "measures": ["p"], "title": "T"},
            id="unsupported_type",
        ),
        pytest.param(
            {
                "type": "bar",
                "x": "d",
                "measures": ["p"],
                "title": "T",
                "code": "alert(1)",
            },
            id="executable_extra_field",
        ),
        pytest.param(
            {"type": "bar", "x": "d", "measures": [], "title": "T"},
            id="no_measures",
        ),
        pytest.param(
            {"type": "bar", "x": "d", "measures": ["p", "p"], "title": "T"},
            id="duplicate_measures",
        ),
        pytest.param(
            {"type": "bar", "x": "d", "measures": ["d"], "title": "T"},
            id="x_reused_as_measure",
        ),
        pytest.param(
            {
                "type": "bar",
                "x": "d",
                "measures": ["a", "b"],
                "series": "region",
                "title": "T",
            },
            id="series_with_multiple_measures",
        ),
        pytest.param(
            {"type": "pie", "x": "d", "measures": ["a", "b"], "title": "T"},
            id="part_to_whole_with_multiple_measures",
        ),
        pytest.param(
            {
                "type": "scatter",
                "x": "d",
                "measures": ["a", "b"],
                "title": "T",
            },
            id="scatter_with_multiple_measures",
        ),
    ],
)
def test_structurally_invalid_specs_are_rejected_by_the_contract(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ChartSpec.model_validate(payload)
