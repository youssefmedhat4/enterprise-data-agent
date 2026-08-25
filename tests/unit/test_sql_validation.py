import pytest

from app.security.sql_validation import SQLValidationError, SQLValidator


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
