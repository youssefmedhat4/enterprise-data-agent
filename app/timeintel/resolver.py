"""Turning a period into exact instants, deterministically.

This is the part a model must never do. "Year to date" on 1 September 2026 in
Africa/Cairo ends at 07:00 UTC, not at midnight and not at 23:59:59, and the
answer depends on a company's fiscal calendar rather than on language. A model
asked to produce those boundaries will produce plausible ones, and a plausible
boundary silently includes or excludes a day of business.

Two decisions run through everything here.

**Half-open intervals.** Every range is `[start, end)`. The alternative --
ending at 23:59:59 -- loses anything between that second and midnight, breaks
differently for dates and timestamps, and quietly changes meaning when a column
gains sub-second precision. Half-open ranges compose: consecutive periods tile
without gaps or overlaps, and a comparison period is the same shape as the
period it compares to.

**Local time decides the calendar; UTC carries the instants.** A day begins when
the business says it begins. So boundaries are computed on local dates in the
datasource's zone and then converted, which is what makes "today" mean the same
thing to a reader in Cairo and to the database in UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from app.timeintel.clock import Clock
from app.timeintel.intent import (
    Comparison,
    Grain,
    PeriodType,
    RollingUnit,
    TimeIntent,
)
from app.timeintel.policy import FiscalYearLabel, TimePolicy, TimePolicyError


class TimeResolutionError(RuntimeError):
    """Raised when a period cannot be resolved from policy and intent."""


@dataclass(frozen=True, slots=True)
class Period:
    """A half-open interval, `[start, end)`, in UTC."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise TimeResolutionError("A period must end after it starts.")


@dataclass(frozen=True, slots=True)
class ResolvedTimePlan:
    """Exactly which instants an answer covers, and how that was decided."""

    intent: TimeIntent
    timezone: str
    label: str
    primary: Period
    comparison: Period | None = None
    comparison_label: str = ""
    grain: Grain = Grain.NONE
    fiscal: bool = False
    policy_id: UUID | None = None
    policy_status: str = "DEFAULT"
    temporal_dimension_id: UUID | None = None
    as_of: datetime | None = None

    @property
    def has_comparison(self) -> bool:
        return self.comparison is not None

    def describe(self) -> str:
        """One line a reader can check the answer against."""
        window = (
            f"{_local(self.primary.start, self.timezone)} to "
            f"{_local(self.primary.end, self.timezone)}"
        )
        if self.comparison is None:
            return f"{self.label}: {window}"
        compared = (
            f"{_local(self.comparison.start, self.timezone)} to "
            f"{_local(self.comparison.end, self.timezone)}"
        )
        return f"{self.label}: {window}; {self.comparison_label}: {compared}"


def resolve(
    intent: TimeIntent,
    policy: TimePolicy,
    *,
    clock: Clock,
    temporal_dimension_id: UUID | None = None,
) -> ResolvedTimePlan:
    """The instants this intent covers under this datasource's calendar."""
    if intent.needs_fiscal_calendar:
        # A default calendar happens to start in January. Answering a fiscal
        # question from it produces a number traceable to nothing anyone agreed.
        try:
            policy.require_fiscal()
        except TimePolicyError as exc:
            raise TimeResolutionError(str(exc)) from exc

    zone = policy.zone
    now_local = clock.now().astimezone(zone)
    as_of = _instant(now_local)
    today = now_local.date()

    primary, label = _primary(intent, policy, today, as_of)
    comparison, comparison_label = _comparison(intent, policy, primary, today)

    return ResolvedTimePlan(
        intent=intent,
        timezone=policy.timezone,
        label=label,
        primary=primary,
        comparison=comparison,
        comparison_label=comparison_label,
        grain=intent.grain,
        fiscal=intent.needs_fiscal_calendar,
        policy_id=policy.id,
        policy_status=policy.status.value,
        temporal_dimension_id=temporal_dimension_id or intent.time_dimension_id,
        as_of=as_of,
    )


# --- primary period ----------------------------------------------------------


def _primary(
    intent: TimeIntent, policy: TimePolicy, today: date, as_of: datetime
) -> tuple[Period, str]:
    zone = policy.timezone
    period = intent.period

    if period is PeriodType.TODAY:
        return _days(today, 1, zone), "today"
    if period is PeriodType.YESTERDAY:
        return _days(today - timedelta(days=1), 1, zone), "yesterday"

    if period is PeriodType.WEEK_TO_DATE:
        return _to_date(_week_start(today, policy), as_of, zone), "week to date"
    if period is PeriodType.LAST_WEEK:
        start = _week_start(today, policy) - timedelta(days=7)
        return _days(start, 7, zone), "last week"

    if period is PeriodType.MONTH_TO_DATE:
        return _to_date(today.replace(day=1), as_of, zone), "month to date"
    if period is PeriodType.LAST_MONTH:
        start = _add_months(today.replace(day=1), -1)
        return _between(start, _add_months(start, 1), zone), "last month"

    if period is PeriodType.QUARTER_TO_DATE:
        return _to_date(_quarter_start(today), as_of, zone), "quarter to date"
    if period is PeriodType.LAST_QUARTER:
        start = _add_months(_quarter_start(today), -3)
        return _between(start, _add_months(start, 3), zone), "last quarter"

    if period is PeriodType.YEAR_TO_DATE:
        return _to_date(date(today.year, 1, 1), as_of, zone), "year to date"
    if period is PeriodType.LAST_YEAR:
        return (
            _between(date(today.year - 1, 1, 1), date(today.year, 1, 1), zone),
            "last calendar year",
        )

    if period is PeriodType.FISCAL_YEAR_TO_DATE:
        return _to_date(_fiscal_year_start(today, policy), as_of, zone), "fiscal year to date"
    if period is PeriodType.FISCAL_YEAR:
        start = _fiscal_year_start(today, policy)
        return (
            _between(start, _add_months(start, 12), zone),
            f"fiscal year {_fiscal_label(start, policy)}",
        )
    if period is PeriodType.LAST_FISCAL_YEAR:
        start = _add_months(_fiscal_year_start(today, policy), -12)
        return (
            _between(start, _add_months(start, 12), zone),
            f"fiscal year {_fiscal_label(start, policy)}",
        )
    if period is PeriodType.FISCAL_QUARTER_TO_DATE:
        return (
            _to_date(_fiscal_quarter_start(today, policy), as_of, zone),
            "fiscal quarter to date",
        )
    if period is PeriodType.LAST_FISCAL_QUARTER:
        start = _add_months(_fiscal_quarter_start(today, policy), -3)
        return _between(start, _add_months(start, 3), zone), "last fiscal quarter"

    if period is PeriodType.NAMED_FISCAL_YEAR:
        assert intent.named_year is not None
        start = _named_fiscal_year_start(intent.named_year, policy)
        return (
            _between(start, _add_months(start, 12), zone),
            f"fiscal year {intent.named_year}",
        )
    if period is PeriodType.NAMED_QUARTER:
        assert intent.named_quarter is not None and intent.named_year is not None
        return _named_quarter(intent, policy, zone)

    if period is PeriodType.ROLLING:
        return _rolling(intent, as_of, today, zone)

    if period is PeriodType.EXPLICIT_RANGE:
        assert intent.explicit_start is not None and intent.explicit_end is not None
        try:
            start = date.fromisoformat(intent.explicit_start)
            end = date.fromisoformat(intent.explicit_end)
        except ValueError as exc:
            raise TimeResolutionError("An explicit range needs ISO dates.") from exc
        return _between(start, end, zone), f"{start.isoformat()} to {end.isoformat()}"

    raise TimeResolutionError("This intent names no period to resolve.")


def _named_quarter(
    intent: TimeIntent, policy: TimePolicy, zone: str
) -> tuple[Period, str]:
    assert intent.named_quarter is not None and intent.named_year is not None
    quarter, year = intent.named_quarter, intent.named_year
    if intent.fiscal:
        year_start = _named_fiscal_year_start(year, policy)
        start = _add_months(year_start, 3 * (quarter - 1))
        return (
            _between(start, _add_months(start, 3), zone),
            f"fiscal Q{quarter} {year}",
        )
    start = date(year, 3 * (quarter - 1) + 1, 1)
    return _between(start, _add_months(start, 3), zone), f"Q{quarter} {year}"


def _rolling(
    intent: TimeIntent, as_of: datetime, today: date, zone: str
) -> tuple[Period, str]:
    assert intent.rolling_value is not None and intent.rolling_unit is not None
    count, unit = intent.rolling_value, intent.rolling_unit
    if unit is RollingUnit.DAY:
        start = today - timedelta(days=count)
    elif unit is RollingUnit.WEEK:
        start = today - timedelta(weeks=count)
    elif unit is RollingUnit.MONTH:
        # Calendar arithmetic, not 30-day blocks: "rolling 12 months" from
        # 1 March means 1 March a year earlier, whatever the month lengths.
        start = _add_months(today, -count)
    elif unit is RollingUnit.QUARTER:
        start = _add_months(today, -3 * count)
    else:
        start = _add_months(today, -12 * count)
    return (
        Period(start=_instant_at(start, zone), end=as_of),
        f"rolling {count} {unit.value.lower()}{'s' if count != 1 else ''}",
    )


# --- comparison period -------------------------------------------------------


def _comparison(
    intent: TimeIntent, policy: TimePolicy, primary: Period, today: date
) -> tuple[Period | None, str]:
    if intent.comparison is Comparison.NONE:
        return None, ""
    zone = policy.timezone

    if intent.comparison is Comparison.SAME_PERIOD_LAST_YEAR:
        # The *equivalent elapsed* period, not the whole of last year. Comparing
        # eight months against twelve is the single most common way a
        # year-on-year number is made to look like a collapse.
        start = _shift_years(primary.start, -1, zone)
        end = _shift_years(primary.end, -1, zone)
        return Period(start=start, end=end), "same period last year"

    length = primary.end - primary.start
    return (
        Period(start=primary.start - length, end=primary.start),
        "previous period",
    )


# --- calendar helpers --------------------------------------------------------


def _week_start(day: date, policy: TimePolicy) -> date:
    offset = (day.weekday() - policy.week_start.weekday) % 7
    return day - timedelta(days=offset)


def _quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def _fiscal_year_start(day: date, policy: TimePolicy) -> date:
    """The first day of the fiscal year containing `day`."""
    candidate = date(day.year, policy.fiscal_year_start_month, policy.fiscal_year_start_day)
    if day < candidate:
        return _add_months(candidate, -12)
    return candidate


def _fiscal_quarter_start(day: date, policy: TimePolicy) -> date:
    year_start = _fiscal_year_start(day, policy)
    months = (day.year - year_start.year) * 12 + day.month - year_start.month
    if day.day < year_start.day:
        months -= 1
    return _add_months(year_start, 3 * (months // 3))


def _fiscal_label(start: date, policy: TimePolicy) -> int:
    """What this company calls the fiscal year beginning on `start`."""
    if policy.fiscal_year_label is FiscalYearLabel.START_YEAR:
        return start.year
    end = _add_months(start, 12)
    # A calendar-aligned fiscal year ends on 1 January of the next year, which
    # would otherwise be labelled a year late under END_YEAR.
    return end.year if (end.month, end.day) != (1, 1) else start.year


def _named_fiscal_year_start(label: int, policy: TimePolicy) -> date:
    if policy.fiscal_year_label is FiscalYearLabel.START_YEAR:
        return date(label, policy.fiscal_year_start_month, policy.fiscal_year_start_day)
    start = date(label - 1, policy.fiscal_year_start_month, policy.fiscal_year_start_day)
    if policy.fiscal_year_is_calendar:
        # January-to-January years carry the same label either way.
        return date(label, policy.fiscal_year_start_month, policy.fiscal_year_start_day)
    return start


def _add_months(day: date, months: int) -> date:
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, min(day.day, _days_in_month(year, month + 1)))


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


# --- instants ----------------------------------------------------------------


def _instant(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


def _instant_at(day: date, zone: str) -> datetime:
    """Local midnight on `day`, as a UTC instant.

    Built through the zone rather than by adding an offset, so daylight saving
    is handled by the timezone database rather than by arithmetic here.
    """
    from zoneinfo import ZoneInfo

    return datetime.combine(day, time.min, tzinfo=ZoneInfo(zone)).astimezone(UTC)


def _days(start: date, count: int, zone: str) -> Period:
    return Period(
        start=_instant_at(start, zone),
        end=_instant_at(start + timedelta(days=count), zone),
    )


def _between(start: date, end: date, zone: str) -> Period:
    return Period(start=_instant_at(start, zone), end=_instant_at(end, zone))


def _to_date(start: date, as_of: datetime, zone: str) -> Period:
    """A period that runs from `start` up to the present moment."""
    return Period(start=_instant_at(start, zone), end=as_of)


def _shift_years(moment: datetime, years: int, zone: str) -> datetime:
    """The same local wall-clock moment, `years` earlier or later.

    Shifting the UTC instant by 365 days would drift across a leap year and
    across a daylight-saving change; shifting the local calendar date does not.
    """
    from zoneinfo import ZoneInfo

    local = moment.astimezone(ZoneInfo(zone))
    shifted = _add_months(local.date(), 12 * years)
    return datetime.combine(shifted, local.time(), tzinfo=ZoneInfo(zone)).astimezone(UTC)


def _local(moment: datetime, zone: str) -> str:
    from zoneinfo import ZoneInfo

    return moment.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M")
