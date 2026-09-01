"""Business periods resolved to exact instants.

Every assertion here is against a pinned clock. Asserting time behaviour against
`datetime.now()` produces tests that pass for eleven months and fail in
December, which is how boundary bugs survive.

The anchor is 2026-09-01 12:00 in Africa/Cairo (09:00 UTC): a Tuesday, inside
Q3, and -- under the July fiscal calendar used below -- two months into fiscal
year 2027.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.timeintel.clock import FixedClock
from app.timeintel.intent import (
    Comparison,
    Grain,
    PeriodType,
    RollingUnit,
    TimeIntent,
)
from app.timeintel.policy import (
    FiscalYearLabel,
    PolicyStatus,
    TimePolicy,
    TimePolicyError,
    WeekStart,
    default_policy,
)
from app.timeintel.resolver import TimeResolutionError, resolve

SOURCE = uuid4()

#: 2026-09-01 12:00 +03:00 -- the anchor every case below is resolved against.
ANCHOR = datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("Africa/Cairo"))
CLOCK = FixedClock(ANCHOR)


def _policy(
    *,
    timezone: str = "Africa/Cairo",
    week_start: WeekStart = WeekStart.SUNDAY,
    fiscal_month: int = 7,
    fiscal_day: int = 1,
    label: FiscalYearLabel = FiscalYearLabel.END_YEAR,
    status: PolicyStatus = PolicyStatus.CONFIRMED,
) -> TimePolicy:
    return TimePolicy(
        data_source_id=SOURCE,
        timezone=timezone,
        week_start=week_start,
        fiscal_year_start_month=fiscal_month,
        fiscal_year_start_day=fiscal_day,
        fiscal_year_label=label,
        status=status,
    )


def _local(moment: datetime, zone: str = "Africa/Cairo") -> str:
    return moment.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M")


def _resolved(period: PeriodType, **fields: object) -> tuple[str, str]:
    plan = resolve(
        TimeIntent(period=period, **fields),  # type: ignore[arg-type]
        _policy(),
        clock=CLOCK,
    )
    return _local(plan.primary.start), _local(plan.primary.end)


# --- calendar periods --------------------------------------------------------


@pytest.mark.parametrize(
    ("period", "start", "end"),
    [
        (PeriodType.TODAY, "2026-09-01 00:00", "2026-09-02 00:00"),
        (PeriodType.YESTERDAY, "2026-08-31 00:00", "2026-09-01 00:00"),
        # The anchor is a Tuesday; this policy starts weeks on Sunday.
        (PeriodType.WEEK_TO_DATE, "2026-08-30 00:00", "2026-09-01 12:00"),
        (PeriodType.LAST_WEEK, "2026-08-23 00:00", "2026-08-30 00:00"),
        (PeriodType.MONTH_TO_DATE, "2026-09-01 00:00", "2026-09-01 12:00"),
        (PeriodType.LAST_MONTH, "2026-08-01 00:00", "2026-09-01 00:00"),
        (PeriodType.QUARTER_TO_DATE, "2026-07-01 00:00", "2026-09-01 12:00"),
        (PeriodType.LAST_QUARTER, "2026-04-01 00:00", "2026-07-01 00:00"),
        (PeriodType.YEAR_TO_DATE, "2026-01-01 00:00", "2026-09-01 12:00"),
        (PeriodType.LAST_YEAR, "2025-01-01 00:00", "2026-01-01 00:00"),
    ],
)
def test_calendar_periods_resolve_to_local_boundaries(
    period: PeriodType, start: str, end: str
) -> None:
    assert _resolved(period) == (start, end)


def test_every_period_is_half_open() -> None:
    """[start, end), never 23:59:59.

    Ending a day at 23:59:59 loses whatever happens in the last second, breaks
    differently for dates and timestamps, and changes meaning when a column
    gains precision. Half-open ranges also tile: yesterday ends exactly where
    today begins.
    """
    yesterday = resolve(TimeIntent(period=PeriodType.YESTERDAY), _policy(), clock=CLOCK)
    today = resolve(TimeIntent(period=PeriodType.TODAY), _policy(), clock=CLOCK)

    assert yesterday.primary.end == today.primary.start
    assert today.primary.end.second == 0
    assert today.primary.end.microsecond == 0


def test_week_start_is_policy_not_convention() -> None:
    monday = resolve(
        TimeIntent(period=PeriodType.WEEK_TO_DATE),
        _policy(week_start=WeekStart.MONDAY),
        clock=CLOCK,
    )
    sunday = resolve(
        TimeIntent(period=PeriodType.WEEK_TO_DATE),
        _policy(week_start=WeekStart.SUNDAY),
        clock=CLOCK,
    )

    assert _local(monday.primary.start) == "2026-08-31 00:00"
    assert _local(sunday.primary.start) == "2026-08-30 00:00"


# --- fiscal periods ----------------------------------------------------------


def test_fiscal_periods_follow_the_configured_calendar() -> None:
    """July fiscal year, END_YEAR labelling -- the example from the brief."""
    assert _resolved(PeriodType.FISCAL_YEAR_TO_DATE) == (
        "2026-07-01 00:00",
        "2026-09-01 12:00",
    )
    assert _resolved(PeriodType.FISCAL_QUARTER_TO_DATE) == (
        "2026-07-01 00:00",
        "2026-09-01 12:00",
    )
    assert _resolved(PeriodType.LAST_FISCAL_QUARTER) == (
        "2026-04-01 00:00",
        "2026-07-01 00:00",
    )
    assert _resolved(PeriodType.FISCAL_YEAR) == (
        "2026-07-01 00:00",
        "2027-07-01 00:00",
    )
    assert _resolved(PeriodType.LAST_FISCAL_YEAR) == (
        "2025-07-01 00:00",
        "2026-07-01 00:00",
    )


def test_a_named_fiscal_year_uses_the_company_labelling_convention() -> None:
    """The same range is FY2026 at one company and FY2027 at another."""
    end_year = resolve(
        TimeIntent(period=PeriodType.NAMED_FISCAL_YEAR, named_year=2027, fiscal=True),
        _policy(label=FiscalYearLabel.END_YEAR),
        clock=CLOCK,
    )
    start_year = resolve(
        TimeIntent(period=PeriodType.NAMED_FISCAL_YEAR, named_year=2026, fiscal=True),
        _policy(label=FiscalYearLabel.START_YEAR),
        clock=CLOCK,
    )

    assert _local(end_year.primary.start) == "2026-07-01 00:00"
    assert _local(end_year.primary.end) == "2027-07-01 00:00"
    assert _local(start_year.primary.start) == "2026-07-01 00:00"
    assert _local(start_year.primary.end) == "2027-07-01 00:00"


def test_the_current_fiscal_year_is_labelled_the_way_the_company_names_it() -> None:
    plan = resolve(
        TimeIntent(period=PeriodType.FISCAL_YEAR),
        _policy(label=FiscalYearLabel.END_YEAR),
        clock=CLOCK,
    )

    assert plan.label == "fiscal year 2027"


def test_a_fiscal_quarter_can_be_named() -> None:
    plan = resolve(
        TimeIntent(
            period=PeriodType.NAMED_QUARTER,
            named_quarter=2,
            named_year=2027,
            fiscal=True,
        ),
        _policy(),
        clock=CLOCK,
    )

    # Fiscal 2027 starts 1 July 2026, so its second quarter starts in October.
    assert _local(plan.primary.start) == "2026-10-01 00:00"
    assert _local(plan.primary.end) == "2027-01-01 00:00"


def test_a_calendar_quarter_is_not_a_fiscal_one() -> None:
    plan = resolve(
        TimeIntent(period=PeriodType.NAMED_QUARTER, named_quarter=2, named_year=2026),
        _policy(),
        clock=CLOCK,
    )

    assert _local(plan.primary.start) == "2026-04-01 00:00"
    assert _local(plan.primary.end) == "2026-07-01 00:00"


def test_a_fiscal_question_is_refused_when_no_calendar_was_confirmed() -> None:
    """A default calendar starting in January is not a company's answer.

    Resolving it anyway would produce a number that looks right and traces to
    nothing anyone agreed to.
    """
    with pytest.raises(TimeResolutionError, match="no confirmed fiscal calendar"):
        resolve(
            TimeIntent(period=PeriodType.FISCAL_YEAR_TO_DATE),
            default_policy(SOURCE),
            clock=CLOCK,
        )


def test_a_calendar_period_still_works_without_a_confirmed_policy() -> None:
    """January to December is January to December wherever you are."""
    plan = resolve(
        TimeIntent(period=PeriodType.YEAR_TO_DATE), default_policy(SOURCE), clock=CLOCK
    )

    assert plan.primary.start == datetime(2026, 1, 1, tzinfo=UTC)
    assert plan.policy_status == "DEFAULT"


# --- comparisons -------------------------------------------------------------


def test_year_on_year_compares_the_equivalent_elapsed_period() -> None:
    """Eight months against twelve is how a flat year looks like a collapse."""
    plan = resolve(
        TimeIntent(
            period=PeriodType.YEAR_TO_DATE,
            comparison=Comparison.SAME_PERIOD_LAST_YEAR,
        ),
        _policy(),
        clock=CLOCK,
    )

    assert plan.comparison is not None
    assert _local(plan.primary.start) == "2026-01-01 00:00"
    assert _local(plan.primary.end) == "2026-09-01 12:00"
    assert _local(plan.comparison.start) == "2025-01-01 00:00"
    assert _local(plan.comparison.end) == "2025-09-01 12:00"


def test_fiscal_year_on_year_compares_the_equivalent_fiscal_stretch() -> None:
    plan = resolve(
        TimeIntent(
            period=PeriodType.FISCAL_YEAR_TO_DATE,
            comparison=Comparison.SAME_PERIOD_LAST_YEAR,
        ),
        _policy(),
        clock=CLOCK,
    )

    assert plan.comparison is not None
    assert _local(plan.comparison.start) == "2025-07-01 00:00"
    assert _local(plan.comparison.end) == "2025-09-01 12:00"


def test_the_previous_period_is_the_same_length_immediately_before() -> None:
    plan = resolve(
        TimeIntent(period=PeriodType.LAST_MONTH, comparison=Comparison.PREVIOUS_PERIOD),
        _policy(),
        clock=CLOCK,
    )

    assert plan.comparison is not None
    assert plan.comparison.end == plan.primary.start
    assert (plan.primary.end - plan.primary.start) == (
        plan.comparison.end - plan.comparison.start
    )


# --- rolling windows ---------------------------------------------------------


def test_rolling_months_use_calendar_arithmetic_not_thirty_day_blocks() -> None:
    plan = resolve(
        TimeIntent(
            period=PeriodType.ROLLING, rolling_value=12, rolling_unit=RollingUnit.MONTH
        ),
        _policy(),
        clock=CLOCK,
    )

    assert _local(plan.primary.start) == "2025-09-01 00:00"
    assert _local(plan.primary.end) == "2026-09-01 12:00"


def test_rolling_days_count_days() -> None:
    plan = resolve(
        TimeIntent(
            period=PeriodType.ROLLING, rolling_value=30, rolling_unit=RollingUnit.DAY
        ),
        _policy(),
        clock=CLOCK,
    )

    assert _local(plan.primary.start) == "2026-08-02 00:00"


def test_an_unbounded_rolling_window_is_refused() -> None:
    """Nobody asks for a thousand years; a query that scans one is an accident."""
    with pytest.raises(ValueError, match="beyond the supported limit"):
        TimeIntent(
            period=PeriodType.ROLLING,
            rolling_value=1_000_000,
            rolling_unit=RollingUnit.YEAR,
        )


# --- timezones ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "start_utc", "end_utc"),
    [
        # 2026-09-01 09:00 UTC is the anchor instant.
        ("UTC", "2026-09-01 00:00", "2026-09-02 00:00"),
        ("Africa/Cairo", "2026-08-31 21:00", "2026-09-01 21:00"),
        # New York is on daylight time in September: UTC-4.
        ("America/New_York", "2026-09-01 04:00", "2026-09-02 04:00"),
    ],
)
def test_today_begins_when_the_business_says_it_begins(
    zone: str, start_utc: str, end_utc: str
) -> None:
    plan = resolve(
        TimeIntent(period=PeriodType.TODAY), _policy(timezone=zone), clock=CLOCK
    )

    assert plan.primary.start.strftime("%Y-%m-%d %H:%M") == start_utc
    assert plan.primary.end.strftime("%Y-%m-%d %H:%M") == end_utc


def test_a_year_on_year_shift_survives_a_daylight_saving_change() -> None:
    """Subtracting 365 days would drift; shifting the local date does not."""
    winter = FixedClock(datetime(2026, 3, 1, 12, tzinfo=ZoneInfo("America/New_York")))
    plan = resolve(
        TimeIntent(
            period=PeriodType.MONTH_TO_DATE,
            comparison=Comparison.SAME_PERIOD_LAST_YEAR,
        ),
        _policy(timezone="America/New_York"),
        clock=winter,
    )

    assert plan.comparison is not None
    assert _local(plan.comparison.start, "America/New_York") == "2025-03-01 00:00"
    assert _local(plan.comparison.end, "America/New_York") == "2025-03-01 12:00"


def test_an_invalid_timezone_is_refused_rather_than_stored() -> None:
    """"EST" and "GMT+2" are how daylight saving gets silently mishandled."""
    for bad in ("EST5EDT-ish", "GMT+2", "Mars/Olympus"):
        with pytest.raises(TimePolicyError):
            TimePolicy(data_source_id=SOURCE, timezone=bad)


# --- isolation ---------------------------------------------------------------


def test_the_same_phrase_resolves_differently_per_datasource() -> None:
    """Two companies, one phrase, two answers -- and neither is wrong."""
    cairo = _policy(timezone="Africa/Cairo", fiscal_month=1, fiscal_day=1)
    new_york = _policy(timezone="America/New_York", fiscal_month=7, fiscal_day=1)
    intent = TimeIntent(period=PeriodType.FISCAL_YEAR_TO_DATE)

    first = resolve(intent, cairo, clock=CLOCK)
    second = resolve(intent, new_york, clock=CLOCK)

    assert _local(first.primary.start, "Africa/Cairo") == "2026-01-01 00:00"
    assert _local(second.primary.start, "America/New_York") == "2026-07-01 00:00"
    assert first.primary.start != second.primary.start


def test_the_plan_describes_itself_for_a_reader() -> None:
    plan = resolve(
        TimeIntent(
            period=PeriodType.FISCAL_YEAR_TO_DATE,
            comparison=Comparison.SAME_PERIOD_LAST_YEAR,
            grain=Grain.MONTH,
        ),
        _policy(),
        clock=CLOCK,
    )

    described = plan.describe()

    assert "fiscal year to date" in described
    assert "2026-07-01 00:00 to 2026-09-01 12:00" in described
    assert "same period last year" in described
    assert plan.grain is Grain.MONTH
