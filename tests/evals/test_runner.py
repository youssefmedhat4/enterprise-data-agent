from collections import Counter
from pathlib import Path
from typing import cast

import pytest

from app.agent.context import AnalyticalContext
from app.agent.graph import build_graph
from app.data.fake import FakeDatabaseGateway
from app.data.gateway import TableMetadata
from app.evals.deterministic_llm import DeterministicEvaluationLLM
from app.evals.duckdb_gateway import DuckDBEvaluationGateway
from app.evals.loader import load_evaluation_cases
from app.evals.models import EvaluationCase, MetricAggregate
from app.evals.runner import build_fake_database_factory, run_evaluations
from app.llm.gateway import LLMRateLimitError, ResponseModelT, SQLGeneration
from app.security.sql_validation import SQLValidator
from app.semantic.gateway import SemanticContext, SemanticProviderUnavailableError

CASES_PATH = Path(__file__).parents[2] / "evals" / "cases.json"


class RateLimitedLLM:
    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system, user, response_model
        raise LLMRateLimitError("Provider rate limit reached.")


class RecordingEvaluationLLM:
    def __init__(self, cases: list[EvaluationCase]) -> None:
        self.delegate = DeterministicEvaluationLLM(cases)
        self.sql_prompts: list[str] = []

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        if response_model is SQLGeneration:
            self.sql_prompts.append(user)
        return await self.delegate.generate_structured(
            model_alias=model_alias,
            system=system,
            user=user,
            response_model=response_model,
        )


class ContradictoryClarificationLLM:
    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        del model_alias, system, user
        return cast(
            ResponseModelT,
            {
                "action": "clarify",
                "sql": "SELECT id FROM analytics.projects",
                "explanation": "The request is ambiguous, but SQL was also emitted.",
                "clarification_question": "Which performance metric should be used?",
            },
        )


class TaggedWrenSemanticGateway:
    async def retrieve_context(
        self,
        *,
        question: str,
        available_tables: list[TableMetadata],
        prior_context: AnalyticalContext | None,
    ) -> SemanticContext:
        del question, prior_context
        tables = [table for table in available_tables if table.identifier.endswith("departments")]
        return SemanticContext(
            tables=tables,
            provider="wren",
            model_ids=["wren:test:departments"],
            relationship_ids=["wren:test:employees_departments"],
            context_size_chars=123,
            retrieval_latency_ms=4.5,
        )


class UnavailableSemanticGateway:
    async def retrieve_context(
        self,
        *,
        question: str,
        available_tables: list[TableMetadata],
        prior_context: AnalyticalContext | None,
    ) -> SemanticContext:
        del question, available_tables, prior_context
        raise SemanticProviderUnavailableError("test Wren outage")


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


@pytest.mark.asyncio
async def test_sql_evaluation_mode_uses_one_model_call_per_case() -> None:
    cases = load_evaluation_cases(CASES_PATH)

    summary = await run_evaluations(
        cases,
        backend="duckdb",
        database_factory=DuckDBEvaluationGateway,
        llm_gateway=DeterministicEvaluationLLM(cases),
        sql_validator=SQLValidator(),
        retry_count=0,
        evaluation_mode="sql",
    )

    assert summary.failed_cases == 0
    assert summary.evaluation_mode == "sql"
    assert summary.performance.llm_call_count == 53
    assert summary.performance.model_calls == {"sql-reasoner": 53}
    answer_accuracy = summary.answer["answer_accuracy"]
    assert isinstance(answer_accuracy, MetricAggregate)
    assert answer_accuracy.applicable == 0
    assert summary.sql["result_accuracy"].accuracy == 1.0


@pytest.mark.asyncio
async def test_follow_up_evaluation_runs_predecessor_in_same_thread() -> None:
    cases = load_evaluation_cases(CASES_PATH)
    follow_up = next(case for case in cases if case.id == "followup_second_department_payroll")
    llm = RecordingEvaluationLLM(cases)

    summary = await run_evaluations(
        [follow_up],
        backend="duckdb",
        database_factory=DuckDBEvaluationGateway,
        llm_gateway=llm,
        sql_validator=SQLValidator(),
        retry_count=0,
        evaluation_mode="sql",
    )

    assert summary.failed_cases == 0
    assert len(llm.sql_prompts) == 2
    assert "Previous structured analytical context:\nnone" in llm.sql_prompts[0]
    assert (
        '"previous_question":"Which department has the highest active annual payroll?"'
        in llm.sql_prompts[1]
    )


@pytest.mark.asyncio
async def test_rate_limit_is_excluded_from_model_accuracy() -> None:
    case = load_evaluation_cases(CASES_PATH)[0]

    summary = await run_evaluations(
        [case],
        backend="duckdb",
        database_factory=DuckDBEvaluationGateway,
        llm_gateway=RateLimitedLLM(),
        sql_validator=SQLValidator(),
        llm_backend="configured",
        evaluation_mode="sql",
    )

    assert summary.scored_cases == 0
    assert summary.infrastructure_failures == 1
    assert summary.infrastructure_errors == {"rate_limited": 1}
    assert summary.sql["result_accuracy"].applicable == 0


@pytest.mark.asyncio
async def test_evaluation_uses_explicit_semantic_gateway_and_records_context() -> None:
    case = load_evaluation_cases(CASES_PATH)[0]

    summary = await run_evaluations(
        [case],
        backend="duckdb",
        database_factory=DuckDBEvaluationGateway,
        llm_gateway=DeterministicEvaluationLLM([case]),
        sql_validator=SQLValidator(),
        evaluation_mode="sql",
        semantic_gateway=TaggedWrenSemanticGateway(),
        semantic_provider="wren",
    )

    result = summary.results[0]
    assert result.semantic_provider == "wren"
    assert result.semantic_model_ids == ["wren:test:departments"]
    assert result.semantic_relationship_ids == ["wren:test:employees_departments"]
    assert result.semantic_context_size_chars == 123
    assert summary.semantic_provider == "wren"
    assert summary.semantic.average_selected_models == 1


@pytest.mark.asyncio
async def test_semantic_provider_outage_is_infrastructure_failure_without_fallback() -> None:
    case = load_evaluation_cases(CASES_PATH)[0]

    summary = await run_evaluations(
        [case],
        backend="duckdb",
        database_factory=DuckDBEvaluationGateway,
        llm_gateway=DeterministicEvaluationLLM([case]),
        sql_validator=SQLValidator(),
        evaluation_mode="sql",
        semantic_gateway=UnavailableSemanticGateway(),
        semantic_provider="wren",
    )

    result = summary.results[0]
    assert result.failure_type == "infrastructure"
    assert result.infrastructure_error == "semantic_provider_unavailable"
    assert result.semantic_provider == "wren"
    assert result.selected_schema_ids == []
    assert summary.scored_cases == 0


@pytest.mark.asyncio
async def test_clarification_path_does_not_execute_generated_sql() -> None:
    database = FakeDatabaseGateway()
    graph = build_graph(
        db_gateway=database,
        llm_gateway=ContradictoryClarificationLLM(),
        sql_validator=SQLValidator(),
        generate_answer=False,
    )

    with pytest.raises(ValueError, match="clarify action"):
        await graph.ainvoke(
            {
                "request_id": "clarification-runtime-test",
                "trace_id": "clarification-runtime-test",
                "thread_id": None,
                "question": "How is performance?",
            }
        )
    assert database.executed_sql == []


@pytest.mark.asyncio
async def test_generated_sql_cannot_satisfy_clarification_evaluation() -> None:
    case = next(
        case for case in load_evaluation_cases(CASES_PATH) if case.id == "ambiguity_performance_ar"
    )
    database = FakeDatabaseGateway()

    summary = await run_evaluations(
        [case],
        backend="fake",
        database_factory=lambda: database,
        llm_gateway=ContradictoryClarificationLLM(),
        sql_validator=SQLValidator(),
        llm_backend="configured",
        evaluation_mode="sql",
    )

    result = summary.results[0]
    assert database.executed_sql == []
    assert result.generated_sql is None
    assert result.workflow.structured_output_validity is False
    assert result.passed is False
