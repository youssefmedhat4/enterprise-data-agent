"""What period a question is asking about, before anyone works out the dates.

Separating intent from resolution is the whole design. A model is good at
noticing that "fiscal YTD versus last year" is a fiscal year-to-date with a
year-on-year comparison; it is unreliable at knowing that this company's fiscal
year starts on 1 July, that the datasource runs on Africa/Cairo, and that the
range therefore ends at 09:00 UTC. So intent may come from language, and
boundaries come from code.

Every field is a bounded enum or a number. There is deliberately no field able
to carry a date expression, a column name, or SQL: the only physical thing a
model may choose is *which* confirmed temporal attribute to use, from a list the
backend supplies.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: A rolling window longer than this is not a business question, and letting one
#: through means scanning a table with no bound anybody chose.
MAX_ROLLING = {
    "DAY": 3650,
    "WEEK": 520,
    "MONTH": 120,
    "QUARTER": 40,
    "YEAR": 10,
}


class PeriodType(StrEnum):
    NONE = "NONE"
    TODAY = "TODAY"
    YESTERDAY = "YESTERDAY"
    WEEK_TO_DATE = "WEEK_TO_DATE"
    LAST_WEEK = "LAST_WEEK"
    MONTH_TO_DATE = "MONTH_TO_DATE"
    LAST_MONTH = "LAST_MONTH"
    QUARTER_TO_DATE = "QUARTER_TO_DATE"
    LAST_QUARTER = "LAST_QUARTER"
    YEAR_TO_DATE = "YEAR_TO_DATE"
    LAST_YEAR = "LAST_YEAR"
    FISCAL_YEAR_TO_DATE = "FISCAL_YEAR_TO_DATE"
    FISCAL_YEAR = "FISCAL_YEAR"
    LAST_FISCAL_YEAR = "LAST_FISCAL_YEAR"
    FISCAL_QUARTER_TO_DATE = "FISCAL_QUARTER_TO_DATE"
    LAST_FISCAL_QUARTER = "LAST_FISCAL_QUARTER"
    #: A year or quarter the user named: FY2026, Q2 2026.
    NAMED_FISCAL_YEAR = "NAMED_FISCAL_YEAR"
    NAMED_QUARTER = "NAMED_QUARTER"
    ROLLING = "ROLLING"
    EXPLICIT_RANGE = "EXPLICIT_RANGE"


class Comparison(StrEnum):
    NONE = "NONE"
    PREVIOUS_PERIOD = "PREVIOUS_PERIOD"
    SAME_PERIOD_LAST_YEAR = "SAME_PERIOD_LAST_YEAR"


class Grain(StrEnum):
    NONE = "NONE"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    YEAR = "YEAR"


class RollingUnit(StrEnum):
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    YEAR = "YEAR"


class TimeIntentError(ValueError):
    """Raised when a time intent could not describe a real period."""


class TimeIntent(BaseModel):
    """A period a question asks about, in business terms only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    period: PeriodType = PeriodType.NONE
    comparison: Comparison = Comparison.NONE
    grain: Grain = Grain.NONE
    rolling_value: int | None = Field(default=None, ge=1)
    rolling_unit: RollingUnit | None = None
    #: For NAMED_FISCAL_YEAR and NAMED_QUARTER: the label the user said.
    named_year: int | None = Field(default=None, ge=1900, le=2999)
    named_quarter: int | None = Field(default=None, ge=1, le=4)
    #: True when the named quarter is a fiscal one rather than a calendar one.
    fiscal: bool = False
    #: ISO dates for EXPLICIT_RANGE, interpreted in the datasource timezone.
    explicit_start: str | None = None
    explicit_end: str | None = None
    #: A point in time the user asked to see the world as of. Only meaningful
    #: where the data can actually be reconstructed for that moment.
    as_of: str | None = None
    #: Chosen by the planner from confirmed candidates the backend supplied.
    #: Never a column name -- an identifier the backend already trusts.
    time_dimension_id: UUID | None = None
    #: The user's own words, kept for provenance so a reader can see what was
    #: interpreted rather than only what it became.
    phrase: str = ""

    @property
    def is_temporal(self) -> bool:
        return self.period is not PeriodType.NONE or self.comparison is not Comparison.NONE

    @property
    def needs_fiscal_calendar(self) -> bool:
        return self.fiscal or self.period in _FISCAL_PERIODS

    @model_validator(mode="after")
    def _describes_a_real_period(self) -> TimeIntent:
        if self.period is PeriodType.ROLLING:
            if self.rolling_value is None or self.rolling_unit is None:
                raise TimeIntentError("A rolling period needs a length and a unit.")
            limit = MAX_ROLLING[self.rolling_unit.value]
            if self.rolling_value > limit:
                raise TimeIntentError(
                    f"A rolling window of {self.rolling_value} "
                    f"{self.rolling_unit.value.lower()}s is beyond the "
                    f"supported limit of {limit}."
                )
        if self.period is PeriodType.EXPLICIT_RANGE and not (
            self.explicit_start and self.explicit_end
        ):
            raise TimeIntentError("An explicit range needs both a start and an end.")
        if self.period is PeriodType.NAMED_FISCAL_YEAR and self.named_year is None:
            raise TimeIntentError("A named fiscal year needs its year.")
        if self.period is PeriodType.NAMED_QUARTER and (
            self.named_quarter is None or self.named_year is None
        ):
            raise TimeIntentError("A named quarter needs its quarter and year.")
        return self


_FISCAL_PERIODS = frozenset(
    {
        PeriodType.FISCAL_YEAR_TO_DATE,
        PeriodType.FISCAL_YEAR,
        PeriodType.LAST_FISCAL_YEAR,
        PeriodType.FISCAL_QUARTER_TO_DATE,
        PeriodType.LAST_FISCAL_QUARTER,
        PeriodType.NAMED_FISCAL_YEAR,
    }
)
