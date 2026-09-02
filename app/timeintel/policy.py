"""One datasource's calendar, stated rather than assumed.

"Fiscal year to date" is not a fact about language; it is a fact about a
company. A business whose year starts in July and one whose year starts in
January mean different ranges by the same words, and a system that guesses is
wrong roughly half the time without ever saying so.

So the calendar is configuration: timezone, which day a week starts on, when the
fiscal year begins, and -- the part most often left implicit -- what a fiscal
year is *called*. A year running July 2026 to July 2027 is FY2027 at some
companies and FY2026 at others. Nobody can infer that.

A policy that nobody confirmed is marked DEFAULT rather than pretended into
truth. Calendar periods work under a default (a calendar year is January to
December wherever you are); fiscal periods do not, and asking is better than
inventing a company's accounting calendar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class WeekStart(StrEnum):
    MONDAY = "MONDAY"
    TUESDAY = "TUESDAY"
    WEDNESDAY = "WEDNESDAY"
    THURSDAY = "THURSDAY"
    FRIDAY = "FRIDAY"
    SATURDAY = "SATURDAY"
    SUNDAY = "SUNDAY"

    @property
    def weekday(self) -> int:
        """Python's Monday-is-0 numbering, which `date.weekday()` returns."""
        return _WEEKDAYS[self]


_WEEKDAYS = {
    WeekStart.MONDAY: 0,
    WeekStart.TUESDAY: 1,
    WeekStart.WEDNESDAY: 2,
    WeekStart.THURSDAY: 3,
    WeekStart.FRIDAY: 4,
    WeekStart.SATURDAY: 5,
    WeekStart.SUNDAY: 6,
}


class FiscalYearLabel(StrEnum):
    """Which calendar year names a fiscal year that spans two.

    START_YEAR: July 2026 - July 2027 is FY2026.
    END_YEAR:   the same range is FY2027.

    There is no correct answer, only a company's answer.
    """

    START_YEAR = "START_YEAR"
    END_YEAR = "END_YEAR"


class PolicyStatus(StrEnum):
    #: Nobody has confirmed this calendar. Safe for calendar periods; not
    #: enough to answer a fiscal question with.
    DEFAULT = "DEFAULT"
    CONFIRMED = "CONFIRMED"


class TimePolicyError(RuntimeError):
    """Raised when a calendar cannot be configured or is not confirmed."""


@dataclass(frozen=True, slots=True)
class TimePolicy:
    data_source_id: UUID
    timezone: str = "UTC"
    week_start: WeekStart = WeekStart.MONDAY
    fiscal_year_start_month: int = 1
    fiscal_year_start_day: int = 1
    fiscal_year_label: FiscalYearLabel = FiscalYearLabel.START_YEAR
    status: PolicyStatus = PolicyStatus.DEFAULT
    version: int = 1
    id: UUID = field(default_factory=uuid4)
    updated_by: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        validate_timezone(self.timezone)
        validate_fiscal_start(self.fiscal_year_start_month, self.fiscal_year_start_day)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def is_confirmed(self) -> bool:
        return self.status is PolicyStatus.CONFIRMED

    @property
    def fiscal_year_is_calendar(self) -> bool:
        return self.fiscal_year_start_month == 1 and self.fiscal_year_start_day == 1

    def require_fiscal(self) -> None:
        """Refuse a fiscal question the datasource has not answered.

        A default calendar happens to start in January, and answering a fiscal
        question from it would produce a number that looks right and is not
        traceable to anything anyone agreed to.
        """
        if not self.is_confirmed:
            raise TimePolicyError(
                "This data source has no confirmed fiscal calendar, so a fiscal "
                "period cannot be resolved. Set its time policy under "
                "Knowledge - Time Intelligence."
            )


def validate_timezone(name: str) -> str:
    """An IANA zone name, checked against the system database.

    Free-text zones are how "EST" and "GMT+2" get stored and then silently
    mishandle daylight saving.
    """
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise TimePolicyError(f"{name!r} is not a known IANA time zone.") from exc
    return name


#: The last day each month can start on. February stops at 28 deliberately --
#: see `validate_fiscal_start`.
_LAST_START_DAY = {
    1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
    7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
}

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def validate_fiscal_start(month: int, day: int) -> tuple[int, int]:
    """A fiscal year start that exists in every year.

    Checked against the month that was chosen, not against a blanket limit:
    plenty of companies run a year from 31 July or 30 April, and refusing those
    turns a real calendar into one nobody can express.

    29 February is refused on purpose. A year starting there has a start date in
    one year out of four and none in the other three, and every way of papering
    over that -- sliding to the 28th, to 1 March, to the nearest weekday -- is
    this system inventing a company's accounting calendar. Inventing calendars
    is the failure the whole design exists to prevent, so a business that really
    does start its year at the end of February is asked to say which day it
    means in a non-leap year.
    """
    if not 1 <= month <= 12:
        raise TimePolicyError("A fiscal year starts in a month between 1 and 12.")
    last = _LAST_START_DAY[month]
    if day < 1 or day > last:
        name = _MONTH_NAMES[month - 1]
        if month == 2 and day == 29:
            raise TimePolicyError(
                "A fiscal year cannot start on 29 February: three years in four "
                "have no such date, and choosing a substitute would be inventing "
                "your calendar. Use 28 February or 1 March, whichever your "
                "accounting calendar means."
            )
        raise TimePolicyError(
            f"{name} has no day {day}: a fiscal year starting in {name} begins "
            f"between the 1st and the {last}."
        )
    return month, day


def default_policy(data_source_id: UUID) -> TimePolicy:
    """The calendar assumed until somebody states otherwise.

    UTC, weeks from Monday, fiscal year matching the calendar year -- and
    marked DEFAULT, so a fiscal question still asks rather than assumes.
    """
    return TimePolicy(data_source_id=data_source_id, status=PolicyStatus.DEFAULT)
