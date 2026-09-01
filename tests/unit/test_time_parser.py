"""Recognising a time phrase without a model call.

Two failure modes matter and they are not symmetric. Failing to recognise a
phrase is safe: the question is answered exactly as it was before this layer
existed. Recognising the wrong one is not, because the answer then covers a
period nobody asked for and looks entirely normal.

So there are as many cases here for what must *not* match as for what must.
"""

from __future__ import annotations

import pytest

from app.timeintel.intent import Comparison, Grain, PeriodType, RollingUnit
from app.timeintel.parser import parse_time_phrase


@pytest.mark.parametrize(
    ("question", "period"),
    [
        ("Show revenue year to date", PeriodType.YEAR_TO_DATE),
        ("revenue YTD", PeriodType.YEAR_TO_DATE),
        ("revenue this year", PeriodType.YEAR_TO_DATE),
        ("What was invoiced MTD?", PeriodType.MONTH_TO_DATE),
        ("this month's costs", PeriodType.MONTH_TO_DATE),
        ("costs QTD", PeriodType.QUARTER_TO_DATE),
        ("revenue WTD", PeriodType.WEEK_TO_DATE),
        ("revenue this week", PeriodType.WEEK_TO_DATE),
        ("What was invoiced last month?", PeriodType.LAST_MONTH),
        ("costs last quarter", PeriodType.LAST_QUARTER),
        ("revenue last year", PeriodType.LAST_YEAR),
        ("headcount today", PeriodType.TODAY),
        ("what happened yesterday", PeriodType.YESTERDAY),
        ("revenue last week", PeriodType.LAST_WEEK),
    ],
)
def test_common_calendar_phrases_are_recognised(
    question: str, period: PeriodType
) -> None:
    intent = parse_time_phrase(question)

    assert intent is not None
    assert intent.period is period


@pytest.mark.parametrize(
    ("question", "period"),
    [
        ("revenue fiscal YTD", PeriodType.FISCAL_YEAR_TO_DATE),
        ("revenue fiscal year to date", PeriodType.FISCAL_YEAR_TO_DATE),
        ("costs this fiscal year", PeriodType.FISCAL_YEAR),
        ("costs last fiscal year", PeriodType.LAST_FISCAL_YEAR),
        ("revenue this fiscal quarter", PeriodType.FISCAL_QUARTER_TO_DATE),
        ("revenue last fiscal quarter", PeriodType.LAST_FISCAL_QUARTER),
    ],
)
def test_fiscal_phrases_are_distinguished_from_calendar_ones(
    question: str, period: PeriodType
) -> None:
    """"last fiscal quarter" must never be read as "last quarter"."""
    intent = parse_time_phrase(question)

    assert intent is not None
    assert intent.period is period
    assert intent.needs_fiscal_calendar


def test_a_named_fiscal_year_carries_its_label() -> None:
    intent = parse_time_phrase("revenue for FY2026")

    assert intent is not None
    assert intent.period is PeriodType.NAMED_FISCAL_YEAR
    assert intent.named_year == 2026
    assert intent.fiscal


@pytest.mark.parametrize(
    ("question", "quarter", "year", "fiscal"),
    [
        ("revenue in Q2 2026", 2, 2026, False),
        ("revenue 2026 Q3", 3, 2026, False),
        ("revenue fiscal Q3 2027", 3, 2027, True),
    ],
)
def test_named_quarters_keep_their_calendar(
    question: str, quarter: int, year: int, fiscal: bool
) -> None:
    intent = parse_time_phrase(question)

    assert intent is not None
    assert intent.period is PeriodType.NAMED_QUARTER
    assert intent.named_quarter == quarter
    assert intent.named_year == year
    assert intent.fiscal is fiscal


@pytest.mark.parametrize(
    ("question", "value", "unit"),
    [
        ("revenue rolling 30 days", 30, RollingUnit.DAY),
        ("revenue over the last 7 days", 7, RollingUnit.DAY),
        ("costs rolling 12 months", 12, RollingUnit.MONTH),
        ("costs trailing 4 quarters", 4, RollingUnit.QUARTER),
        ("revenue past 2 years", 2, RollingUnit.YEAR),
    ],
)
def test_rolling_windows_carry_their_length_and_unit(
    question: str, value: int, unit: RollingUnit
) -> None:
    intent = parse_time_phrase(question)

    assert intent is not None
    assert intent.period is PeriodType.ROLLING
    assert intent.rolling_value == value
    assert intent.rolling_unit is unit


@pytest.mark.parametrize(
    ("question", "comparison"),
    [
        ("revenue YTD year over year", Comparison.SAME_PERIOD_LAST_YEAR),
        ("revenue YTD YoY", Comparison.SAME_PERIOD_LAST_YEAR),
        ("revenue YTD vs last year", Comparison.SAME_PERIOD_LAST_YEAR),
        ("compare it with last year", Comparison.SAME_PERIOD_LAST_YEAR),
        ("revenue this month MoM", Comparison.PREVIOUS_PERIOD),
        ("revenue QoQ", Comparison.PREVIOUS_PERIOD),
        ("revenue this quarter vs the previous period", Comparison.PREVIOUS_PERIOD),
    ],
)
def test_comparisons_are_recognised(question: str, comparison: Comparison) -> None:
    intent = parse_time_phrase(question)

    assert intent is not None
    assert intent.comparison is comparison


@pytest.mark.parametrize(
    ("question", "grain"),
    [
        ("revenue by month this year", Grain.MONTH),
        ("revenue monthly", Grain.MONTH),
        ("revenue per quarter", Grain.QUARTER),
        ("headcount by day", Grain.DAY),
        ("revenue yearly", Grain.YEAR),
    ],
)
def test_a_requested_grain_is_recognised(question: str, grain: Grain) -> None:
    intent = parse_time_phrase(question)

    assert intent is not None
    assert intent.grain is grain


def test_a_follow_up_carrying_only_a_change_still_parses() -> None:
    """"Now monthly" changes the grain of a period already established."""
    intent = parse_time_phrase("now monthly")

    assert intent is not None
    assert intent.period is PeriodType.NONE
    assert intent.grain is Grain.MONTH


@pytest.mark.parametrize(
    "question",
    [
        "How many active employees do we have?",
        "What is our current annual payroll?",
        "Which customer has the highest project margin?",
        # A bare noun is not a period: "which quarter was best" names none.
        "which quarter was best",
        "show me the period breakdown",
        "list every project",
    ],
)
def test_a_question_with_no_time_phrase_produces_no_intent(question: str) -> None:
    """Answering these must be byte-identical to before this layer existed."""
    assert parse_time_phrase(question) is None


def test_a_longer_phrase_wins_over_the_shorter_one_inside_it() -> None:
    fiscal = parse_time_phrase("revenue last fiscal year")
    calendar = parse_time_phrase("revenue last year")

    assert fiscal is not None and calendar is not None
    assert fiscal.period is PeriodType.LAST_FISCAL_YEAR
    assert calendar.period is PeriodType.LAST_YEAR


def test_an_absurd_rolling_window_yields_no_intent_rather_than_an_error() -> None:
    """The question still deserves an answer; it just carries no usable period."""
    assert parse_time_phrase("revenue rolling 9999 years") is None


def test_the_users_own_words_are_kept_for_provenance() -> None:
    intent = parse_time_phrase("Show invoiced revenue fiscal YTD")

    assert intent is not None
    assert intent.phrase == "Show invoiced revenue fiscal YTD"
