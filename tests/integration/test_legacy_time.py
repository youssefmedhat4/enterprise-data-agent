"""Time intelligence against the real Legacy ERP fixture.

The fixture stores dates as `CHAR(8)` text, which is the case worth testing: a
naive comparison sorts 20240301 before 20240229 correctly by luck and fails on
anything else, and a cast blows up on one malformed row. The declared storage
strategy is what makes this safe.

Every expected value below was computed independently against the fixture --
see the reference statements in each test -- rather than taken from what the
system produced.

The anchor is 2024-12-01 12:00 Cairo, inside the fixture's data, so relative
periods select something. Anchoring at the wall clock would make these tests
return nothing in a year's time and pass vacuously.
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest

from app.config import Settings, get_settings
from app.data.factory import build_database_gateway_for
from app.knowledge.contracts import ApprovalStatus
from app.knowledge.datasources import DataSourceConnectionResolver, DataSourceError
from app.timeintel.clock import FixedClock
from app.timeintel.dimensions import (
    TemporalDimension,
    TemporalRole,
    TemporalStorage,
    timestamp_expression,
)
from app.timeintel.intent import Comparison, Grain, PeriodType, TimeIntent
from app.timeintel.policy import (
    FiscalYearLabel,
    PolicyStatus,
    TimePolicy,
    WeekStart,
)
from app.timeintel.resolver import resolve

pytestmark = pytest.mark.legacy

SOURCE = uuid4()

#: Inside the fixture's data, so relative periods actually select rows.
ANCHOR = datetime(2024, 12, 1, 12, 0, tzinfo=ZoneInfo("Africa/Cairo"))
CLOCK = FixedClock(ANCHOR)


def _legacy_dsn() -> str:
    try:
        return DataSourceConnectionResolver(get_settings()).resolve(
            "LEGACY_DATABASE_URL"
        )
    except DataSourceError:
        pytest.skip("LEGACY_DATABASE_URL is not configured.")


def _policy(*, fiscal_month: int = 1) -> TimePolicy:
    return TimePolicy(
        data_source_id=SOURCE,
        timezone="Africa/Cairo",
        week_start=WeekStart.SUNDAY,
        fiscal_year_start_month=fiscal_month,
        fiscal_year_start_day=1,
        fiscal_year_label=FiscalYearLabel.END_YEAR,
        status=PolicyStatus.CONFIRMED,
    )


def _dimension(
    table: str, column: str, concept: str, entity: str
) -> TemporalDimension:
    """What a reviewer confirmed about one of the fixture's text dates."""
    return TemporalDimension(
        data_source_id=SOURCE,
        semantic_attribute_id=uuid4(),
        role=TemporalRole.EVENT_TIME,
        storage=TemporalStorage.YYYYMMDD_TEXT,
        schema_name="erp",
        table_name=table,
        column_name=column,
        concept_name=concept,
        entity_name=entity,
        is_default_for_entity=True,
        status=ApprovalStatus.CONFIRMED,
    )


INVOICE_DATE = _dimension("ar_inv_hdr", "inv_dt_chr", "Invoice Date", "Invoice")
COST_DATE = _dimension(
    "gl_cost_txn", "txn_dt_chr", "Cost Transaction Date", "Cost Transaction"
)


def _run(coroutine: Any) -> Any:
    if sys.platform == "win32":
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            return runner.run(coroutine)
    return asyncio.run(coroutine)


def _scalar(sql: str, parameters: tuple[Any, ...] = ()) -> Any:
    dsn = _legacy_dsn()
    try:
        connection = psycopg.connect(dsn, connect_timeout=5)
    except psycopg.Error:
        pytest.skip("Legacy ERP fixture is unavailable.")
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            row = cursor.fetchone()
    finally:
        connection.close()
    return row[0] if row else None


async def _execute(sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Run through the datasource's own gateway and read-only role."""
    gateway = build_database_gateway_for(
        Settings(), database_url=_legacy_dsn(), allowed_schemas=("erp",)
    )
    try:
        result = await gateway.execute_readonly(sql, parameters)
        return result.rows
    finally:
        await gateway.close()


def _invoiced_between(dimension: TemporalDimension, intent: TimeIntent, **policy: Any) -> Any:
    """Invoiced revenue over a resolved period, filtered through the mapping.

    The statement is built the way production builds one: the boundaries come
    from the resolver and the column conversion from the declared storage
    strategy, never from a hand-written date expression.
    """
    plan = resolve(intent, _policy(**policy), clock=CLOCK)
    expression = timestamp_expression(dimension, alias="h")
    sql = (
        "SELECT sum(l.qty * l.unit_amt - l.disc_amt) AS invoiced "
        "FROM erp.ar_inv_hdr AS h "
        "JOIN erp.ar_inv_ln AS l ON l.inv_no = h.inv_no "
        f"WHERE h.void_flg = 'N' AND {expression} >= $1 AND {expression} < $2"
    )
    rows = _run(_execute(sql, (plan.primary.start, plan.primary.end)))
    return rows[0]["invoiced"] if rows else None


def test_last_month_selects_only_that_month() -> None:
    """Reference: sum over inv_dt_chr in [20241101, 20241201) = 43,200."""
    reference = _scalar(
        "SELECT sum(l.qty * l.unit_amt - l.disc_amt) FROM erp.ar_inv_hdr h "
        "JOIN erp.ar_inv_ln l ON l.inv_no = h.inv_no "
        "WHERE h.void_flg = 'N' AND h.inv_dt_chr >= '20241101' "
        "AND h.inv_dt_chr < '20241201'"
    )
    assert reference == Decimal("43200.0000")

    resolved = _invoiced_between(
        INVOICE_DATE, TimeIntent(period=PeriodType.LAST_MONTH)
    )

    assert Decimal(str(resolved)) == reference


def test_year_to_date_covers_the_calendar_year_so_far() -> None:
    """Reference: [20240101, 20241201) = 839,700 -- the whole fixture."""
    reference = _scalar(
        "SELECT sum(l.qty * l.unit_amt - l.disc_amt) FROM erp.ar_inv_hdr h "
        "JOIN erp.ar_inv_ln l ON l.inv_no = h.inv_no "
        "WHERE h.void_flg = 'N' AND h.inv_dt_chr >= '20240101' "
        "AND h.inv_dt_chr < '20241201'"
    )
    assert reference == Decimal("839700.0000")

    resolved = _invoiced_between(
        INVOICE_DATE, TimeIntent(period=PeriodType.YEAR_TO_DATE)
    )

    assert Decimal(str(resolved)) == reference


def test_project_costs_year_to_date_use_their_own_date_column() -> None:
    """A different entity, a different confirmed column. Reference: 1,042,500."""
    reference = _scalar(
        "SELECT sum(cost_amt) FROM erp.gl_cost_txn "
        "WHERE posted_flg = 'Y' AND reversal_flg = 'N' "
        "AND txn_dt_chr >= '20240101' AND txn_dt_chr < '20241201'"
    )
    assert reference == Decimal("1042500.00")

    plan = resolve(
        TimeIntent(period=PeriodType.YEAR_TO_DATE), _policy(), clock=CLOCK
    )
    expression = timestamp_expression(COST_DATE, alias="c")
    rows = _run(
        _execute(
            "SELECT sum(c.cost_amt) AS cost FROM erp.gl_cost_txn AS c "
            "WHERE c.posted_flg = 'Y' AND c.reversal_flg = 'N' "
            f"AND {expression} >= $1 AND {expression} < $2",
            (plan.primary.start, plan.primary.end),
        )
    )

    assert Decimal(str(rows[0]["cost"])) == reference


def test_a_fiscal_year_starting_in_july_selects_a_different_period() -> None:
    """The same phrase, a different calendar, a different number.

    Reference: [20240701, 20241201) = 458,250 against 839,700 for the calendar
    year. If the fiscal calendar were ignored these would be equal.
    """
    reference = _scalar(
        "SELECT sum(l.qty * l.unit_amt - l.disc_amt) FROM erp.ar_inv_hdr h "
        "JOIN erp.ar_inv_ln l ON l.inv_no = h.inv_no "
        "WHERE h.void_flg = 'N' AND h.inv_dt_chr >= '20240701' "
        "AND h.inv_dt_chr < '20241201'"
    )
    assert reference == Decimal("458250.0000")

    fiscal = _invoiced_between(
        INVOICE_DATE,
        TimeIntent(period=PeriodType.FISCAL_YEAR_TO_DATE),
        fiscal_month=7,
    )
    calendar = _invoiced_between(
        INVOICE_DATE, TimeIntent(period=PeriodType.YEAR_TO_DATE)
    )

    assert Decimal(str(fiscal)) == reference
    assert Decimal(str(calendar)) != Decimal(str(fiscal))


def test_the_same_period_last_year_selects_the_equivalent_stretch() -> None:
    """The fixture holds nothing in 2023, so the comparison is empty.

    That is the correct answer and worth asserting: a comparison that silently
    fell back to the whole of last year, or to the primary period, would return
    a number instead.
    """
    plan = resolve(
        TimeIntent(
            period=PeriodType.YEAR_TO_DATE,
            comparison=Comparison.SAME_PERIOD_LAST_YEAR,
        ),
        _policy(),
        clock=CLOCK,
    )
    assert plan.comparison is not None
    expression = timestamp_expression(INVOICE_DATE, alias="h")
    rows = _run(
        _execute(
            "SELECT sum(l.qty * l.unit_amt - l.disc_amt) AS invoiced "
            "FROM erp.ar_inv_hdr AS h "
            "JOIN erp.ar_inv_ln AS l ON l.inv_no = h.inv_no "
            f"WHERE h.void_flg = 'N' AND {expression} >= $1 AND {expression} < $2",
            (plan.comparison.start, plan.comparison.end),
        )
    )

    assert rows[0]["invoiced"] is None
    assert plan.comparison.start.astimezone(ZoneInfo("Africa/Cairo")).year == 2023


def test_monthly_grain_matches_an_independent_month_by_month_reference() -> None:
    """Reference computed by grouping the raw text dates, ten months of data."""
    plan = resolve(
        TimeIntent(period=PeriodType.YEAR_TO_DATE, grain=Grain.MONTH),
        _policy(),
        clock=CLOCK,
    )
    expression = timestamp_expression(INVOICE_DATE, alias="h")
    rows = _run(
        _execute(
            f"SELECT date_trunc('month', {expression}) AS month, "
            "sum(l.qty * l.unit_amt - l.disc_amt) AS invoiced "
            "FROM erp.ar_inv_hdr AS h "
            "JOIN erp.ar_inv_ln AS l ON l.inv_no = h.inv_no "
            f"WHERE h.void_flg = 'N' AND {expression} >= $1 AND {expression} < $2 "
            "GROUP BY 1 ORDER BY 1",
            (plan.primary.start, plan.primary.end),
        )
    )

    monthly = {
        str(row["month"])[:7]: Decimal(str(row["invoiced"])) for row in rows
    }
    assert monthly == {
        "2024-02": Decimal("6900.0000"),
        "2024-03": Decimal("48300.0000"),
        "2024-04": Decimal("113050.0000"),
        "2024-05": Decimal("107050.0000"),
        "2024-06": Decimal("106150.0000"),
        "2024-07": Decimal("92950.0000"),
        "2024-08": Decimal("115150.0000"),
        "2024-09": Decimal("115800.0000"),
        "2024-10": Decimal("91150.0000"),
        "2024-11": Decimal("43200.0000"),
    }
    assert sum(monthly.values()) == Decimal("839700.0000")


def test_the_text_date_conversion_handles_every_row_in_the_fixture() -> None:
    """A malformed row must not fail the query, and none must be dropped.

    The guarded conversion is what makes CHAR(8) dates safe; a bare cast would
    raise on one bad value and take the whole answer with it.
    """
    expression = timestamp_expression(INVOICE_DATE, alias="h")
    rows = _run(
        _execute(
            f"SELECT count(*) AS total, count({expression}) AS converted "
            "FROM erp.ar_inv_hdr AS h",
        )
    )

    assert rows[0]["total"] == 102
    assert rows[0]["converted"] == 102
