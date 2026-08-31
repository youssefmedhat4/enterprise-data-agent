"""Focused live checks for the fixed Legacy ERP fixture.

This is intentionally separate from model tests.  It proves fixture integrity
and the independent canonical values used to assess generated SQL; it makes no
LLM call and does not treat a plausible answer as a passing result.
"""

from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal

import psycopg
import pytest

from app.config import get_settings
from app.knowledge.datasources import DataSourceConnectionResolver, DataSourceError

pytestmark = pytest.mark.legacy


@pytest.fixture
def legacy_connection() -> Generator[psycopg.Connection[tuple[object, ...]]]:
    try:
        dsn = DataSourceConnectionResolver(get_settings()).resolve(
            "LEGACY_DATABASE_URL"
        )
    except DataSourceError:
        pytest.skip("LEGACY_DATABASE_URL is not configured.")
    try:
        connection = psycopg.connect(dsn, connect_timeout=5)
    except psycopg.Error:
        pytest.skip("Legacy ERP fixture is unavailable.")
    try:
        yield connection
    finally:
        connection.close()


def _scalar(connection: psycopg.Connection[tuple[object, ...]], sql: str) -> object:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    assert row is not None
    return row[0]


def test_legacy_fixture_counts_and_decisive_business_values(
    legacy_connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.org_unit_lkp") == 10
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.emp_mst") == 60
    assert _scalar(
        legacy_connection, "SELECT count(*) FROM erp.emp_comp_hist"
    ) == 159
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.cust_mst") == 22
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.prj_hdr") == 40
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.ar_inv_hdr") == 102
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.ar_inv_ln") == 408
    assert _scalar(legacy_connection, "SELECT count(*) FROM erp.gl_cost_txn") == 350
    assert _scalar(
        legacy_connection, "SELECT count(*) FROM erp.emp_mst WHERE stat_cd = 'A'"
    ) == 42
    assert _scalar(
        legacy_connection,
        "SELECT sum(ann_sal_amt) FROM erp.emp_comp_hist WHERE curr_flg = 'Y'",
    ) == Decimal("6345000.00")
    assert _scalar(
        legacy_connection,
        "SELECT sum(l.qty * l.unit_amt - l.disc_amt) "
        "FROM erp.ar_inv_hdr h JOIN erp.ar_inv_ln l ON l.inv_no = h.inv_no "
        "WHERE h.void_flg = 'N'",
    ) == Decimal("839700.00")
    assert _scalar(
        legacy_connection,
        "SELECT sum(cost_amt) FROM erp.gl_cost_txn "
        "WHERE posted_flg = 'Y' AND reversal_flg = 'N'",
    ) == Decimal("1042500.00")


def test_legacy_identity_ambiguity_and_large_entity_targets(
    legacy_connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    with legacy_connection.cursor() as cursor:
        cursor.execute(
            "SELECT org_cd, org_nm FROM erp.org_unit_lkp "
            "WHERE org_nm = %s ORDER BY org_cd",
            ("Operations",),
        )
        operations = cursor.fetchall()
        cursor.execute(
            "SELECT cust_cd FROM erp.cust_mst WHERE cust_cd = %s", ("C0022",)
        )
        customer = cursor.fetchone()
        cursor.execute(
            "SELECT prj_no FROM erp.prj_hdr WHERE prj_no = %s", (5040,)
        )
        project = cursor.fetchone()
    assert operations == [("OU2100", "Operations"), ("OU2200", "Operations")]
    assert customer == ("C0022",)
    assert project == (5040,)
