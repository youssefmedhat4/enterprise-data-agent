import pytest

from app.security.sql_validation import SQLValidationError, SQLValidator


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO analytics.departments (name) VALUES ('x')",
        "UPDATE analytics.employees SET salary = 0",
        "DELETE FROM analytics.employees",
        "DROP TABLE analytics.employees",
        "ALTER TABLE analytics.employees ADD COLUMN secret text",
        "TRUNCATE TABLE analytics.employees",
        "CREATE TABLE analytics.stolen (id integer)",
        "GRANT ALL ON analytics.employees TO public",
        "REVOKE SELECT ON analytics.employees FROM public",
        "CALL analytics.refresh_payroll()",
        "COPY analytics.employees TO '/tmp/employees.csv'",
        "SELECT * INTO analytics.employee_copy FROM analytics.employees",
        "MERGE INTO analytics.employees USING analytics.departments ON false "
        "WHEN NOT MATCHED THEN INSERT DEFAULT VALUES",
    ],
)
def test_validator_rejects_mutation_and_commands(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        SQLValidator().validate_readonly(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM analytics.employees; DELETE FROM analytics.employees",
        "SELECT 1; SELECT 2",
        "SELECT * FROM analytics.employees; DROP TABLE analytics.departments; --",
    ],
)
def test_validator_rejects_multiple_statements(sql: str) -> None:
    with pytest.raises(SQLValidationError, match="exactly one statement"):
        SQLValidator().validate_readonly(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pg_user",
        "SELECT * FROM pg_catalog.pg_user",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT nextval('analytics.some_sequence')",
        "SELECT pg_advisory_lock(12345)",
    ],
)
def test_validator_rejects_catalog_access_and_unsafe_functions(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        SQLValidator().validate_readonly(sql)
