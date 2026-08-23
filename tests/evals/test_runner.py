from collections import Counter
from pathlib import Path

import pytest

from app.evals.deterministic_llm import DeterministicEvaluationLLM
from app.evals.duckdb_gateway import DuckDBEvaluationGateway
from app.evals.loader import load_evaluation_cases
from app.evals.runner import build_fake_database_factory, run_evaluations
from app.security.sql_validation import SQLValidator

CASES_PATH = Path(__file__).parents[2] / "evals" / "cases.json"


def test_evaluation_dataset_has_required_scale_and_coverage() -> None:
    cases = load_evaluation_cases(CASES_PATH)
    categories = Counter(case.category for case in cases)
    languages = Counter(case.language for case in cases)

    assert len(cases) == 50
    assert set(categories) == {
        "aggregation",
        "ambiguity",
        "comparative_analytics",
        "cte_subquery",
        "follow_up",
        "multi_table_join",
        "security_adversarial",
        "simple_lookup",
        "temporal_reasoning",
        "window_function",
    }
    assert all(languages[language] > 0 for language in ("en", "ar", "mixed"))
    assert all(case.expected_security_behavior for case in cases)


@pytest.mark.asyncio
async def test_fake_evaluation_passes_without_claiming_execution_accuracy() -> None:
    cases = load_evaluation_cases(CASES_PATH)
    validator = SQLValidator()

    summary = await run_evaluations(
        cases,
        backend="fake",
        database_factory=build_fake_database_factory(cases, validator),
        llm_gateway=DeterministicEvaluationLLM(cases),
        sql_validator=validator,
        retry_count=0,
    )

    assert summary.total_cases == 50
    assert summary.failed_cases == 0
    assert summary.sql["execution_success"].applicable == 0
    assert summary.sql["result_accuracy"].applicable == 0
    assert summary.security["blocked_mutation_attempts"].accuracy == 1.0


@pytest.mark.asyncio
async def test_duckdb_evaluation_executes_sql_and_scores_results() -> None:
    cases = load_evaluation_cases(CASES_PATH)

    summary = await run_evaluations(
        cases,
        backend="duckdb",
        database_factory=DuckDBEvaluationGateway,
        llm_gateway=DeterministicEvaluationLLM(cases),
        sql_validator=SQLValidator(),
        retry_count=0,
    )

    assert summary.total_cases == 50
    assert summary.failed_cases == 0
    assert summary.sql["execution_success"].applicable == 45
    assert summary.sql["execution_success"].accuracy == 1.0
    assert summary.sql["result_accuracy"].accuracy == 1.0
