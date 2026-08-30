from decimal import Decimal, InvalidOperation

from app.contracts.analytics import ChartSpec

#: Beyond this many slices a part-to-whole chart stops communicating anything.
MAX_PART_TO_WHOLE_SLICES = 12

_OMITTED = "Visualization omitted because "


class ChartValidator:
    """Validates an AI-selected chart against the rows that were actually returned.

    The model chooses the visualization; this class decides whether that choice is
    renderable. Every referenced column is checked against the real result — a
    column name is never trusted because the model produced it.

    Two kinds of problem are handled differently, on purpose:

    - A *data-integrity* problem (missing column, non-numeric measure, negative
      slice) means the chart would misrepresent the result, so the chart is
      dropped and a warning is returned.
    - A *cosmetic* mismatch (horizontal orientation on a pie, stacking with a
      single series) would render harmlessly but incoherently, so the field is
      normalised and the chart is kept.

    Dropping a chart never fails the analysis: the grounded answer and the table
    are unaffected, which is why every failure path returns `(None, [warning])`
    rather than raising.
    """

    def validate(
        self,
        chart: ChartSpec | None,
        rows: list[dict[str, object]],
    ) -> tuple[ChartSpec | None, list[str]]:
        if chart is None:
            return None, []
        if not rows:
            return None, [f"{_OMITTED}the query returned no rows."]

        columns = set(rows[0])
        referenced = {chart.x, *chart.measures}
        if chart.series is not None:
            referenced.add(chart.series)
        missing = sorted(referenced - columns)
        if missing:
            return None, [
                f"{_OMITTED}the generated specification referenced fields that are "
                "not present in the result."
            ]

        for measure in chart.measures:
            values = [row.get(measure) for row in rows]
            present = [value for value in values if value is not None]
            if not present or not all(_is_numeric(value) for value in present):
                return None, [f"{_OMITTED}a measure is not numeric."]

        if chart.chart_type == "scatter":
            x_values = [row.get(chart.x) for row in rows]
            present_x = [value for value in x_values if value is not None]
            if not present_x or not all(_is_numeric(value) for value in present_x):
                return None, [
                    f"{_OMITTED}a scatter chart needs a numeric x field."
                ]

        if chart.chart_type in {"pie", "donut"}:
            measure = chart.measures[0]
            values = [row.get(measure) for row in rows if row.get(measure) is not None]
            if any(_as_decimal(value) < 0 for value in values):
                return None, [
                    f"{_OMITTED}part-to-whole measures cannot be negative."
                ]
            categories = {str(row.get(chart.x)) for row in rows}
            if len(categories) > MAX_PART_TO_WHOLE_SLICES:
                return None, [
                    f"{_OMITTED}the result has too many categories to read as a "
                    "part-to-whole chart."
                ]

        return self._normalise(chart), []

    def _normalise(self, chart: ChartSpec) -> ChartSpec:
        """Reset cosmetic fields that do not apply to the chosen chart type."""
        updates: dict[str, object] = {}

        if chart.orientation == "horizontal" and chart.chart_type != "bar":
            updates["orientation"] = "vertical"

        multi_series = len(chart.measures) > 1 or chart.series is not None
        stackable = chart.chart_type in {"bar", "area"}
        if chart.mode == "stacked" and not (multi_series and stackable):
            updates["mode"] = "grouped"

        return chart.model_copy(update=updates) if updates else chart


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
