"""Quality assertions: what they measure, and what they may say about it.

The failure this guards against is a confident wrong answer over data that
stopped arriving. The second failure is a page full of warnings nobody reads,
which is why relevance is tested as carefully as the checks themselves.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.knowledge.quality import (
    AssertionType,
    QualityAssertion,
    QualityCheckResult,
    QualityError,
    QualityStatus,
    build_check_sql,
    interpret,
    relevant_to,
    validate_configuration,
)

SOURCE = uuid4()


def _assertion(
    assertion_type: AssertionType,
    configuration: dict[str, Any],
    *,
    table: str = "sales",
    column: str | None = "loaded_at",
) -> QualityAssertion:
    return QualityAssertion(
        data_source_id=SOURCE,
        name=f"{assertion_type.value} on {table}",
        assertion_type=assertion_type,
        schema_name="analytics",
        table_name=table,
        column_name=column,
        configuration=configuration,
    )


# --- freshness ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_minutes", "status"),
    [
        (30.0, QualityStatus.HEALTHY),
        (120.0, QualityStatus.HEALTHY),
        (150.0, QualityStatus.WARNING),
        (600.0, QualityStatus.STALE),
    ],
)
def test_freshness_has_a_warning_band_before_it_calls_data_stale(
    age_minutes: float, status: QualityStatus
) -> None:
    """A table drifting toward its limit is visible before it crosses one."""
    assertion = _assertion(AssertionType.FRESHNESS, {"max_age_minutes": 120})

    assert interpret(assertion, age_minutes).status is status


def test_a_table_with_no_timestamp_is_unknown_rather_than_healthy() -> None:
    """Not every database records when a row arrived; saying so is honest."""
    assertion = _assertion(AssertionType.FRESHNESS, {"max_age_minutes": 120})

    result = interpret(assertion, None)

    assert result.status is QualityStatus.UNKNOWN
    assert not result.is_concerning


# --- the other assertions ----------------------------------------------------


@pytest.mark.parametrize(
    ("assertion_type", "configuration", "column", "observed", "status"),
    [
        (AssertionType.ROW_COUNT, {"min_rows": 1}, None, 0.0, QualityStatus.FAILING),
        (AssertionType.ROW_COUNT, {"min_rows": 1}, None, 500.0, QualityStatus.HEALTHY),
        (AssertionType.NULL_RATE, {"max_ratio": 0.05}, "amount", 0.01, QualityStatus.HEALTHY),
        (AssertionType.NULL_RATE, {"max_ratio": 0.05}, "amount", 0.2, QualityStatus.FAILING),
        (AssertionType.UNIQUE, {}, "id", 0.0, QualityStatus.HEALTHY),
        (AssertionType.UNIQUE, {}, "id", 3.0, QualityStatus.FAILING),
        (AssertionType.ACCEPTED_VALUES, {"values": ["A"]}, "status", 0.0, QualityStatus.HEALTHY),
        (AssertionType.ACCEPTED_VALUES, {"values": ["A"]}, "status", 7.0, QualityStatus.FAILING),
    ],
)
def test_each_assertion_reads_its_measurement_the_way_it_should(
    assertion_type: AssertionType,
    configuration: dict[str, Any],
    column: str | None,
    observed: float,
    status: QualityStatus,
) -> None:
    assertion = _assertion(assertion_type, configuration, column=column)

    assert interpret(assertion, observed).status is status


def test_a_custom_assertion_is_bounded_at_both_ends_if_configured() -> None:
    assertion = _assertion(
        AssertionType.CUSTOM_SAFE_SQL,
        {"sql": "SELECT 1", "min_value": 10, "max_value": 20},
        column=None,
    )

    assert interpret(assertion, 15.0).status is QualityStatus.HEALTHY
    assert interpret(assertion, 5.0).status is QualityStatus.FAILING
    assert interpret(assertion, 25.0).status is QualityStatus.FAILING


# --- configuration -----------------------------------------------------------


def test_a_configuration_that_could_not_produce_a_verdict_is_refused() -> None:
    with pytest.raises(QualityError):
        validate_configuration(AssertionType.FRESHNESS, None, {"max_age_minutes": 60})
    with pytest.raises(QualityError):
        validate_configuration(
            AssertionType.FRESHNESS, "loaded_at", {"max_age_minutes": 0}
        )
    with pytest.raises(QualityError):
        validate_configuration(AssertionType.NULL_RATE, "amount", {"max_ratio": 2})
    with pytest.raises(QualityError):
        validate_configuration(AssertionType.ACCEPTED_VALUES, "status", {"values": []})
    with pytest.raises(QualityError, match="min_value"):
        # Without a bound the number it measures means nothing.
        validate_configuration(
            AssertionType.CUSTOM_SAFE_SQL, None, {"sql": "SELECT count(*) FROM t"}
        )


# --- generated SQL -----------------------------------------------------------


def test_check_sql_quotes_identifiers_and_binds_values() -> None:
    """The statement is built by trusted code; a model writes none of it."""
    accepted = _assertion(
        AssertionType.ACCEPTED_VALUES, {"values": ["A", "T"]}, column="status"
    )

    sql, parameters = build_check_sql(accepted)

    assert '"analytics"."sales"' in sql
    assert '"status"' in sql
    assert parameters == ("A", "T"), "values were inlined instead of bound"
    assert "'A'" not in sql


def test_a_custom_statement_is_passed_through_for_validation_unchanged() -> None:
    """It has to face SQLGlot and schema authorization like any other query."""
    assertion = _assertion(
        AssertionType.CUSTOM_SAFE_SQL,
        {"sql": "SELECT count(*) AS observed FROM analytics.sales", "min_value": 1},
        column=None,
    )

    sql, parameters = build_check_sql(assertion)

    assert sql == "SELECT count(*) AS observed FROM analytics.sales"
    assert parameters == ()


# --- relevance ---------------------------------------------------------------


def test_only_warnings_about_tables_the_answer_read_are_attached() -> None:
    """A payroll answer must not carry an invoice freshness warning."""
    payroll = _assertion(
        AssertionType.FRESHNESS, {"max_age_minutes": 60}, table="emp_comp_hist"
    )
    invoices = _assertion(
        AssertionType.FRESHNESS, {"max_age_minutes": 60}, table="ar_inv_hdr"
    )
    results = {
        payroll.id: QualityCheckResult(
            assertion_id=payroll.id,
            data_source_id=SOURCE,
            status=QualityStatus.STALE,
            detail="Latest data is 3.0 days old.",
        ),
        invoices.id: QualityCheckResult(
            assertion_id=invoices.id,
            data_source_id=SOURCE,
            status=QualityStatus.STALE,
            detail="Latest data is 9.0 days old.",
        ),
    }

    attached = relevant_to(
        [payroll, invoices], results, {"analytics.emp_comp_hist"}
    )

    assert [assertion.table_name for assertion, _ in attached] == ["emp_comp_hist"]


def test_a_healthy_or_unknown_check_attaches_nothing() -> None:
    """Only a concern is worth interrupting an answer for."""
    assertion = _assertion(AssertionType.FRESHNESS, {"max_age_minutes": 60})
    for status in (QualityStatus.HEALTHY, QualityStatus.WARNING, QualityStatus.UNKNOWN):
        results = {
            assertion.id: QualityCheckResult(
                assertion_id=assertion.id,
                data_source_id=SOURCE,
                status=status,
            )
        }

        assert relevant_to([assertion], results, {"analytics.sales"}) == []


def test_an_assertion_never_checked_attaches_nothing() -> None:
    assertion = _assertion(AssertionType.FRESHNESS, {"max_age_minutes": 60})

    assert relevant_to([assertion], {}, {"analytics.sales"}) == []
