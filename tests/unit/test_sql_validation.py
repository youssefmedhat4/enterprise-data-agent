import pytest

from app.data.gateway import ColumnMetadata, TableMetadata
from app.security.sql_validation import (
    SQLValidationCode,
    SQLValidationError,
    SQLValidator,
)


def test_validator_accepts_cte_and_preserves_safe_limit() -> None:
    sql = """
        WITH active AS (
            SELECT department_id, salary
            FROM analytics.employees
            WHERE status = 'active'
        )
        SELECT department_id, SUM(salary) AS payroll
        FROM active
        GROUP BY department_id
        LIMIT 20
    """

    validated = SQLValidator(max_rows=100).validate_readonly(sql)

    assert "WITH active AS" in validated
    assert "LIMIT 20" in validated


def test_validator_adds_limit() -> None:
    validated = SQLValidator(max_rows=25).validate_readonly("SELECT id FROM analytics.departments")

    assert validated.endswith("LIMIT 25")


def test_validator_clamps_excessive_limit() -> None:
    validated = SQLValidator(max_rows=25).validate_readonly(
        "SELECT id FROM analytics.departments LIMIT 500"
    )

    assert validated.endswith("LIMIT 25")


def test_validator_rejects_disallowed_schema() -> None:
    with pytest.raises(SQLValidationError, match="not allowed"):
        SQLValidator().validate_readonly("SELECT * FROM public.users")


def test_validator_rejects_disallowed_table_in_allowed_schema() -> None:
    with pytest.raises(SQLValidationError, match="Table 'secrets' is not allowed"):
        SQLValidator().validate_readonly("SELECT * FROM analytics.secrets")


def test_validator_accepts_a_dynamically_discovered_relation() -> None:
    validated = SQLValidator(
        allowed_schemas=frozenset({"reporting"}),
        allowed_tables=frozenset(),
    ).validate_readonly(
        "SELECT region FROM reporting.sales_summary",
        allowed_relations=frozenset({("reporting", "sales_summary")}),
    )

    assert validated.endswith("LIMIT 100")


def test_validator_rejects_an_undiscovered_relation() -> None:
    with pytest.raises(SQLValidationError, match="was not discovered"):
        SQLValidator(
            allowed_schemas=frozenset({"reporting"}),
            allowed_tables=frozenset(),
        ).validate_readonly(
            "SELECT secret FROM reporting.hidden_table",
            allowed_relations=frozenset({("reporting", "sales_summary")}),
        )


def _erp_schema() -> list[TableMetadata]:
    """A schema whose dates are text, as older databases routinely are."""
    return [
        TableMetadata(
            schema_name="erp",
            table_name="prj_hdr",
            columns=["prj_no", "prj_nm"],
            description="",
            column_metadata=[
                ColumnMetadata(name="prj_no", data_type="integer", nullable=False),
                ColumnMetadata(name="prj_nm", data_type="varchar", nullable=False),
            ],
        ),
        TableMetadata(
            schema_name="erp",
            table_name="gl_cost_txn",
            columns=["txn_no", "prj_no", "posted_flg", "txn_dt_chr"],
            description="",
            column_metadata=[
                ColumnMetadata(name="txn_no", data_type="integer", nullable=False),
                ColumnMetadata(name="prj_no", data_type="integer", nullable=False),
                ColumnMetadata(name="posted_flg", data_type="char", nullable=False),
                ColumnMetadata(name="txn_dt_chr", data_type="char", nullable=False),
            ],
        ),
    ]


def _erp_validator() -> SQLValidator:
    return SQLValidator(max_rows=1000, allowed_schemas=frozenset({"erp"}))


def test_an_anti_join_predicate_is_allowed() -> None:
    """"Which projects have no posted cost" could not be written at all.

    EXISTS is a read-only subquery predicate, not a call, but it was rejected
    as a forbidden function -- so the natural formulation of every anti-join
    question was refused as unsafe on every schema.
    """
    result = _erp_validator().validate(
        "SELECT p.prj_no FROM erp.prj_hdr AS p WHERE NOT EXISTS ("
        "SELECT 1 FROM erp.gl_cost_txn AS g"
        " WHERE g.prj_no = p.prj_no AND g.posted_flg = 'Y')",
        allowed_schema=_erp_schema(),
    )

    assert result.is_valid, result.error_details


def test_a_text_date_can_be_converted() -> None:
    result = _erp_validator().validate(
        "SELECT to_date(txn_dt_chr, 'YYYYMMDD') AS posted_on FROM erp.gl_cost_txn",
        allowed_schema=_erp_schema(),
    )

    assert result.is_valid, result.error_details


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_read_file('/etc/passwd') AS leaked FROM erp.prj_hdr",
        "SELECT nextval('erp.s') AS n FROM erp.prj_hdr",
        "SELECT dblink('a', 'b') AS remote FROM erp.prj_hdr",
        # Caller-supplied patterns are a cost the timeout does not bound.
        "SELECT regexp_replace(prj_nm, 'a', 'b') AS x FROM erp.prj_hdr",
    ],
)
def test_widening_the_allowlist_did_not_admit_anything_unsafe(sql: str) -> None:
    result = _erp_validator().validate(sql, allowed_schema=_erp_schema())

    assert not result.is_valid
    assert result.error_code is SQLValidationCode.FORBIDDEN_FUNCTION
