"""Reading a time phrase without asking a model.

Almost every temporal question people actually ask uses one of about thirty
phrases: year to date, last month, rolling 12 months, FY2026, year on year. A
model can classify those, but paying for a network round trip to recognise "YTD"
is a cost on every question and a source of variance on a decision that has one
right answer.

So this recognises the common shapes deterministically, and anything it does not
recognise is simply not a time intent -- the existing planner still sees the
question and may classify it. Failing to match is safe; matching wrongly is not,
which is why the patterns are anchored on word boundaries and why a bare
"quarter" or "period" matches nothing.
"""

from __future__ import annotations

import re

from app.timeintel.intent import (
    Comparison,
    Grain,
    PeriodType,
    RollingUnit,
    TimeIntent,
    TimeIntentError,
)

#: Ordered: the first match wins, so longer and more specific phrases are
#: listed before the shorter ones they contain. "last fiscal quarter" must not
#: be read as "last quarter".
_PERIOD_PATTERNS: tuple[tuple[str, PeriodType], ...] = (
    (r"\bfiscal\s+year\s+to\s+date\b|\bfiscal\s+ytd\b|\bfy\s?td\b", PeriodType.FISCAL_YEAR_TO_DATE),
    (r"\blast\s+fiscal\s+year\b|\bprevious\s+fiscal\s+year\b", PeriodType.LAST_FISCAL_YEAR),
    (r"\bthis\s+fiscal\s+year\b|\bcurrent\s+fiscal\s+year\b", PeriodType.FISCAL_YEAR),
    (
        r"\blast\s+fiscal\s+quarter\b|\bprevious\s+fiscal\s+quarter\b",
        PeriodType.LAST_FISCAL_QUARTER,
    ),
    (
        r"\bfiscal\s+quarter\s+to\s+date\b|\bthis\s+fiscal\s+quarter\b"
        r"|\bcurrent\s+fiscal\s+quarter\b",
        PeriodType.FISCAL_QUARTER_TO_DATE,
    ),
    (r"\byear\s+to\s+date\b|\bytd\b", PeriodType.YEAR_TO_DATE),
    (r"\bquarter\s+to\s+date\b|\bqtd\b", PeriodType.QUARTER_TO_DATE),
    (r"\bmonth\s+to\s+date\b|\bmtd\b", PeriodType.MONTH_TO_DATE),
    (r"\bweek\s+to\s+date\b|\bwtd\b", PeriodType.WEEK_TO_DATE),
    (r"\byesterday\b", PeriodType.YESTERDAY),
    (r"\btoday\b", PeriodType.TODAY),
    (r"\blast\s+week\b|\bprevious\s+week\b", PeriodType.LAST_WEEK),
    (r"\bthis\s+week\b|\bcurrent\s+week\b", PeriodType.WEEK_TO_DATE),
    (r"\blast\s+month\b|\bprevious\s+month\b", PeriodType.LAST_MONTH),
    (r"\bthis\s+month\b|\bcurrent\s+month\b", PeriodType.MONTH_TO_DATE),
    (r"\blast\s+quarter\b|\bprevious\s+quarter\b", PeriodType.LAST_QUARTER),
    (r"\bthis\s+quarter\b|\bcurrent\s+quarter\b", PeriodType.QUARTER_TO_DATE),
    (r"\blast\s+year\b|\bprevious\s+year\b", PeriodType.LAST_YEAR),
    (r"\bthis\s+year\b|\bcurrent\s+year\b", PeriodType.YEAR_TO_DATE),
)

_COMPARISON_PATTERNS: tuple[tuple[str, Comparison], ...] = (
    (
        r"\bsame\s+period\s+(?:in\s+)?last\s+year\b|\bsame\s+period\s+a\s+year\s+ago\b"
        r"|\byear\s+over\s+year\b|\byear\s+on\s+year\b|\byoy\b"
        r"|\bcompared?\s+(?:it\s+)?(?:with|to)\s+last\s+year\b"
        r"|\bvs\.?\s+last\s+year\b|\bversus\s+last\s+year\b",
        Comparison.SAME_PERIOD_LAST_YEAR,
    ),
    (
        r"\bmonth\s+over\s+month\b|\bmom\b|\bquarter\s+over\s+quarter\b|\bqoq\b"
        r"|\bprevious\s+period\b|\bprior\s+period\b"
        r"|\bvs\.?\s+(?:the\s+)?previous\s+period\b",
        Comparison.PREVIOUS_PERIOD,
    ),
)

_GRAIN_PATTERNS: tuple[tuple[str, Grain], ...] = (
    (r"\b(?:by|per|each)\s+day\b|\bdaily\b", Grain.DAY),
    (r"\b(?:by|per|each)\s+week\b|\bweekly\b", Grain.WEEK),
    (r"\b(?:by|per|each)\s+month\b|\bmonthly\b", Grain.MONTH),
    (r"\b(?:by|per|each)\s+quarter\b|\bquarterly\b", Grain.QUARTER),
    (r"\b(?:by|per|each)\s+year\b|\byearly\b|\bannually\b", Grain.YEAR),
)

_ROLLING = re.compile(
    r"\b(?:rolling|trailing|last|past|previous)\s+(\d{1,4})\s+"
    r"(day|days|week|weeks|month|months|quarter|quarters|year|years)\b"
)

_FISCAL_YEAR_LABEL = re.compile(r"\b(?:fy|fiscal\s+year)\s*(\d{4})\b")

_QUARTER_LABEL = re.compile(
    r"\b(?:(fiscal)\s+)?q([1-4])\s*(?:of\s+)?(?:fy)?\s*(\d{4})\b"
    r"|\b(\d{4})\s+(?:(fiscal)\s+)?q([1-4])\b"
)

_ROLLING_UNITS = {
    "day": RollingUnit.DAY,
    "week": RollingUnit.WEEK,
    "month": RollingUnit.MONTH,
    "quarter": RollingUnit.QUARTER,
    "year": RollingUnit.YEAR,
}


def parse_time_phrase(question: str) -> TimeIntent | None:
    """The period this question asks about, or None if it names none.

    Returning None is the normal outcome for most questions and is not a
    failure: a question with no time phrase should be answered exactly as it
    was before this layer existed.
    """
    text = question.casefold()
    comparison = _match_comparison(text)
    grain = _match_grain(text)

    named = _match_named(text)
    if named is not None:
        return _build(named | {"comparison": comparison, "grain": grain, "phrase": question})

    rolling = _ROLLING.search(text)
    if rolling is not None:
        unit = _ROLLING_UNITS[rolling.group(2).rstrip("s")]
        return _build(
            {
                "period": PeriodType.ROLLING,
                "rolling_value": int(rolling.group(1)),
                "rolling_unit": unit,
                "comparison": comparison,
                "grain": grain,
                "phrase": question,
            }
        )

    for pattern, period in _PERIOD_PATTERNS:
        if re.search(pattern, text):
            return _build(
                {
                    "period": period,
                    "comparison": comparison,
                    "grain": grain,
                    "fiscal": "fiscal" in text,
                    "phrase": question,
                }
            )

    if comparison is not Comparison.NONE or grain is not Grain.NONE:
        # "Compare it with last year" or "now monthly" -- a change to an
        # existing period rather than a period of its own. The caller decides
        # what to inherit; saying nothing here would lose the instruction.
        return _build(
            {
                "period": PeriodType.NONE,
                "comparison": comparison,
                "grain": grain,
                "phrase": question,
            }
        )
    return None


def _match_comparison(text: str) -> Comparison:
    for pattern, comparison in _COMPARISON_PATTERNS:
        if re.search(pattern, text):
            return comparison
    return Comparison.NONE


def _match_grain(text: str) -> Grain:
    for pattern, grain in _GRAIN_PATTERNS:
        if re.search(pattern, text):
            return grain
    return Grain.NONE


def _match_named(text: str) -> dict[str, object] | None:
    quarter = _QUARTER_LABEL.search(text)
    if quarter is not None:
        fiscal_word, number, year, year_first, fiscal_word_2, number_2 = quarter.groups()
        return {
            "period": PeriodType.NAMED_QUARTER,
            "named_quarter": int(number or number_2),
            "named_year": int(year or year_first),
            "fiscal": bool(fiscal_word or fiscal_word_2) or "fiscal" in text,
        }
    fiscal_year = _FISCAL_YEAR_LABEL.search(text)
    if fiscal_year is not None:
        return {
            "period": PeriodType.NAMED_FISCAL_YEAR,
            "named_year": int(fiscal_year.group(1)),
            "fiscal": True,
        }
    return None


def _build(fields: dict[str, object]) -> TimeIntent | None:
    try:
        return TimeIntent.model_validate(fields)
    except (TimeIntentError, ValueError):
        # An unparseable or out-of-bounds phrase is treated as no time intent
        # rather than as an error: the question still deserves an answer, and
        # the planner may make sense of it.
        return None
