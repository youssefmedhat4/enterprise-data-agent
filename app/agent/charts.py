from decimal import Decimal, InvalidOperation

from app.contracts.analytics import ChartSpec


class ChartValidator:
    def validate(
        self,
        chart: ChartSpec | None,
        rows: list[dict[str, object]],
    ) -> tuple[ChartSpec | None, list[str]]:
        if chart is None:
            return None, []
        if not rows:
            return None, ["Chart omitted because the query returned no rows."]

        columns = set(rows[0])
        requested_fields = {chart.x, chart.y}
        if chart.series is not None:
            requested_fields.add(chart.series)
        if not requested_fields.issubset(columns):
            return None, ["Chart omitted because one or more chart fields are unavailable."]

        y_values = [row.get(chart.y) for row in rows if row.get(chart.y) is not None]
        if not y_values or not all(_is_numeric(value) for value in y_values):
            return None, ["Chart omitted because its measure is not numeric."]
        if chart.chart_type in {"pie", "donut"} and any(
            _as_decimal(value) < 0 for value in y_values
        ):
            return None, ["Chart omitted because pie and donut measures cannot be negative."]
        return chart, []


def _is_numeric(value: object) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int | float | Decimal):
        return True
    if isinstance(value, str):
        try:
            Decimal(value.replace(",", ""))
        except InvalidOperation:
            return False
        return True
    return False


def _as_decimal(value: object) -> Decimal:
    return Decimal(str(value).replace(",", ""))
