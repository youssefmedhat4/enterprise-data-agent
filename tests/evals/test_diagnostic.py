from collections import Counter
from pathlib import Path

from app.data.gateway import TableMetadata
from app.evals.diagnostic import (
    _load_selected_ids,
    _select_cases,
)
from app.evals.loader import load_evaluation_cases
from app.evals.sql_diagnostics import schema_hallucinations

ROOT = Path(__file__).parents[2]


def test_diagnostic_selection_uses_twelve_existing_cases_with_required_categories() -> None:
    cases = load_evaluation_cases(ROOT / "evals" / "cases.json")
    selected_ids = _load_selected_ids(ROOT / "evals" / "qwen_diagnostic_case_ids.json")
    selected = _select_cases(cases, selected_ids)

    assert len(selected) == 12
    assert Counter(case.category for case in selected) == {
        "aggregation": 2,
        "multi_table_join": 2,
        "cte_subquery": 2,
        "window_function": 2,
        "temporal_reasoning": 2,
        "ambiguity": 1,
        "follow_up": 1,
    }


def test_schema_hallucination_diagnostic_detects_invalid_qualified_columns() -> None:
    schema = [
        TableMetadata(
            schema_name="analytics",
            table_name="projects",
            columns=["id", "project_code", "name"],
            description="Projects",
        ),
        TableMetadata(
            schema_name="analytics",
            table_name="project_costs",
            columns=["id", "project_id", "amount"],
            description="Project costs",
        ),
    ]

    tables, columns = schema_hallucinations(
        """
        SELECT p.project_code, SUM(pc.amount) AS total_cost
        FROM analytics.projects p
        JOIN analytics.project_costs pc ON p.project_id = pc.project_id
        GROUP BY p.project_code
        """,
        schema,
    )

    assert tables == []
    assert columns == ["projects.project_id"]


def test_schema_hallucination_diagnostic_detects_unknown_tables() -> None:
    schema = [
        TableMetadata(
            schema_name="analytics",
            table_name="employees",
            columns=["id", "salary"],
            description="Employees",
        )
    ]

    tables, _ = schema_hallucinations(
        "SELECT x.amount FROM analytics.made_up_costs x",
        schema,
    )

    assert tables == ["made_up_costs"]
