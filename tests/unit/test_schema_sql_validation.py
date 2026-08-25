from dataclasses import replace
from pathlib import Path

from app.data.schema_metadata import synthetic_enterprise_metadata
from app.errors import ErrorCode, normalize_error
from app.security.evaluation import evaluate_validation_cases, load_validation_cases
from app.security.sql_validation import (
    SQLRepairFailedError,
    SQLSchemaValidationError,
    SQLValidationCode,
    SQLValidationResult,
    SQLValidator,
)

DATASET = Path("evals/sql_validation_cases.json")


def test_schema_validation_dataset_is_unique_and_passes() -> None:
    cases = load_validation_cases(DATASET)
    report = evaluate_validation_cases(DATASET)

    assert len(cases) == 25
    assert len({case.id for case in cases}) == len(cases)
    assert report["category_counts"] == {
        "non_repairable": 8,
        "repairable": 8,
        "valid": 9,
    }
    assert report["passed"] == 25
    assert report["failed_case_ids"] == []


def test_cte_subquery_and_window_references_are_resolved() -> None:
    sql = """
        WITH ranked AS (
            SELECT
                e.department_id,
                e.full_name,
                ROW_NUMBER() OVER (
                    PARTITION BY e.department_id ORDER BY e.salary DESC
                ) AS salary_rank
            FROM analytics.employees e
            WHERE e.id IN (
                SELECT a.employee_id
                FROM analytics.employee_project_assignments a
            )
        )
        SELECT r.department_id, r.full_name
        FROM ranked r
        WHERE r.salary_rank = 1
    """

    result = SQLValidator().validate(sql, allowed_schema=synthetic_enterprise_metadata())

    assert result.is_valid
    assert result.error_code is None
    assert result.validated_sql is not None
    assert set(result.referenced_tables) == {
        "analytics.employee_project_assignments",
        "analytics.employees",
    }
    assert result.resolved_aliases["e"] == "analytics.employees"


def test_allowed_column_snapshot_is_the_validation_boundary() -> None:
    metadata = synthetic_enterprise_metadata()
    employees = next(table for table in metadata if table.table_name == "employees")
    restricted_employees = replace(
        employees,
        columns=[name for name in employees.columns if name != "salary"],
        column_metadata=[
            column for column in employees.column_metadata if column.name != "salary"
        ],
    )

    result = SQLValidator().validate(
        "SELECT e.salary FROM analytics.employees e",
        allowed_schema=[restricted_employees],
    )

    assert not result.is_valid
    assert result.error_code == SQLValidationCode.UNKNOWN_COLUMN
    assert result.repairable
    assert result.unknown_columns == ("salary",)


def test_star_policy_rejects_projection_but_allows_count_star() -> None:
    metadata = synthetic_enterprise_metadata()
    validator = SQLValidator()

    projection = validator.validate(
        "SELECT e.* FROM analytics.employees e",
        allowed_schema=metadata,
    )
    count = validator.validate(
        "SELECT COUNT(*) AS employee_count FROM analytics.employees e",
        allowed_schema=metadata,
    )

    assert projection.error_code == SQLValidationCode.RESTRICTED_STAR
    assert not projection.repairable
    assert count.is_valid


def test_unknown_function_and_system_catalog_are_not_repairable() -> None:
    validator = SQLValidator()
    metadata = synthetic_enterprise_metadata()

    function_result = validator.validate(
        "SELECT made_up_function(e.salary) FROM analytics.employees e",
        allowed_schema=metadata,
    )
    catalog_result = validator.validate(
        "SELECT p.usename FROM pg_catalog.pg_user p",
        allowed_schema=metadata,
    )

    assert function_result.error_code == SQLValidationCode.FORBIDDEN_FUNCTION
    assert not function_result.repairable
    assert catalog_result.error_code == SQLValidationCode.FORBIDDEN_SYSTEM_ACCESS
    assert not catalog_result.repairable


def test_schema_and_repair_errors_have_sanitized_api_contracts() -> None:
    validation = SQLValidationResult(
        is_valid=False,
        error_code=SQLValidationCode.UNKNOWN_COLUMN,
        error_details="Secret column payroll_private_token does not exist.",
        repairable=True,
    )

    schema_error = normalize_error(
        SQLSchemaValidationError("Secret SQL", result=validation),
        request_id="schema-request",
    )
    repair_error = normalize_error(
        SQLRepairFailedError("Secret SQL", result=validation),
        request_id="repair-request",
    )

    assert schema_error.code == ErrorCode.SQL_SCHEMA_VALIDATION_FAILED
    assert repair_error.code == ErrorCode.SQL_REPAIR_FAILED
    assert "payroll_private_token" not in schema_error.safe_message
    assert "Secret SQL" not in repair_error.safe_message
