from decimal import Decimal

import duckdb
import pytest

from app.agent.graph import _sql_system_prompt
from app.evals.duckdb_gateway import DEFAULT_FIXTURE_PATH

DEPARTMENT_REFERENCE_SQL = """
WITH employee_metrics AS (
    SELECT d.id AS department_id,
           COUNT(*) FILTER (WHERE LOWER(e.status) = 'active') AS active_employee_count,
           SUM(e.salary) AS annual_base_payroll,
           AVG(e.salary) AS average_employee_salary
    FROM analytics.departments d
    LEFT JOIN analytics.employees e ON e.department_id = d.id
    GROUP BY d.id
),
project_cost_metrics AS (
    SELECT p.owning_department_id AS department_id,
           COALESCE(SUM(pc.amount), 0) AS project_cost
    FROM analytics.projects p
    LEFT JOIN analytics.project_costs pc ON pc.project_id = p.id
    GROUP BY p.owning_department_id
),
invoice_metrics AS (
    SELECT p.owning_department_id AS department_id,
           COALESCE(SUM(il.quantity * il.unit_price), 0) AS invoiced_amount
    FROM analytics.projects p
    LEFT JOIN analytics.invoices i ON i.project_id = p.id
    LEFT JOIN analytics.invoice_lines il ON il.invoice_id = i.id
    GROUP BY p.owning_department_id
),
department_metrics AS (
    SELECT d.name AS department,
           em.active_employee_count,
           em.annual_base_payroll,
           em.average_employee_salary,
           COALESCE(pc.project_cost, 0) AS project_cost,
           COALESCE(inv.invoiced_amount, 0) AS invoiced_amount,
           COALESCE(inv.invoiced_amount, 0) - COALESCE(pc.project_cost, 0) AS project_margin,
           em.annual_base_payroll / NULLIF(em.active_employee_count, 0)
               AS payroll_per_active_employee
    FROM analytics.departments d
    JOIN employee_metrics em ON em.department_id = d.id
    LEFT JOIN project_cost_metrics pc ON pc.department_id = d.id
    LEFT JOIN invoice_metrics inv ON inv.department_id = d.id
)
SELECT *, RANK() OVER (ORDER BY project_margin DESC) AS margin_rank
FROM department_metrics
ORDER BY margin_rank, department
"""


def _connection() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute(DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8"))
    return connection


def test_sql_prompt_requires_final_grain_and_independent_filter_scopes() -> None:
    prompt = _sql_system_prompt()

    assert "requested final result grain" in prompt
    assert "aggregate each source to the requested final grain" in prompt
    assert "intermediate grouping dimensions" in prompt
    assert "SUM(DISTINCT ...)" in prompt
    assert "different populations or filter scopes" in prompt


def test_multi_fact_reference_is_one_exact_row_per_department() -> None:
    connection = _connection()
    try:
        cursor = connection.execute(DEPARTMENT_REFERENCE_SQL)
        columns = [description[0] for description in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()

    assert [row["department"] for row in rows] == [
        "Engineering",
        "Finance",
        "People Operations",
        "Sales",
    ]
    assert rows[0] == {
        "department": "Engineering",
        "active_employee_count": 4,
        "annual_base_payroll": Decimal("710000.00"),
        "average_employee_salary": Decimal("142000.0"),
        "project_cost": Decimal("88700.00"),
        "invoiced_amount": Decimal("205000.0000"),
        "project_margin": Decimal("116300.0000"),
        "payroll_per_active_employee": 177500.0,
        "margin_rank": 1,
    }
    assert {(row["department"], row["margin_rank"]) for row in rows[1:]} == {
        ("Finance", 2),
        ("People Operations", 2),
        ("Sales", 2),
    }


def test_active_filter_does_not_contaminate_roster_payroll() -> None:
    connection = _connection()
    try:
        row = connection.execute(
            """
            SELECT COUNT(*) FILTER (WHERE LOWER(status) = 'active'),
                   SUM(salary), AVG(salary)
            FROM analytics.employees
            WHERE department_id = 1
            """
        ).fetchone()
    finally:
        connection.close()

    assert row == (4, Decimal("710000.00"), 142000.0)


def test_duplicate_salary_values_are_not_deduplicated() -> None:
    connection = _connection()
    try:
        connection.execute(
            """
            INSERT INTO analytics.employees VALUES
            (99, 'E-1099', 1, 1, 'Synthetic Duplicate Salary', 'اختبار',
             'Engineer', 'active', '2025-01-01', NULL, 120000, 'USD')
            """
        )
        total = connection.execute(
            "SELECT SUM(salary) FROM analytics.employees WHERE department_id = 1"
        ).fetchone()
    finally:
        connection.close()

    assert total == (Decimal("830000.00"),)


def test_department_project_grain_legitimately_repeats_departments() -> None:
    connection = _connection()
    try:
        rows = connection.execute(
            """
            SELECT d.name AS department, p.name AS project
            FROM analytics.departments d
            JOIN analytics.projects p ON p.owning_department_id = d.id
            ORDER BY department, project
            """
        ).fetchall()
    finally:
        connection.close()

    engineering = [row for row in rows if row[0] == "Engineering"]
    assert len(engineering) == 3
    assert len(set(engineering)) == 3


@pytest.mark.parametrize("forbidden", ["SELECT DISTINCT", "SUM(DISTINCT"])
def test_reference_strategy_does_not_hide_fanout_with_distinct(forbidden: str) -> None:
    assert forbidden not in DEPARTMENT_REFERENCE_SQL.upper()
