import asyncio
import json
import re
from collections import Counter
from collections.abc import Callable, Sequence
from time import perf_counter
from typing import Any, Literal, cast

from langgraph.checkpoint.memory import InMemorySaver
from sqlglot import expressions as exp
from sqlglot import parse
from sqlglot.errors import ParseError

from app.agent.graph import build_graph
from app.agent.state import AgentState
from app.data.fake import FakeDatabaseGateway
from app.data.gateway import (
    DatabaseGateway,
    DatabaseQueryResult,
    DatabaseQueryTimeoutError,
    DatabaseSource,
    DatabaseUnavailableError,
    TableMetadata,
)
from app.evals.comparison import ComparisonResult, compare_case_results
from app.evals.models import (
    AnswerMetrics,
    DimensionSummary,
    EvaluationCase,
    EvaluationMode,
    EvaluationResult,
    EvaluationSummary,
    MetricAggregate,
    PerformanceMetrics,
    PerformanceSummary,
    ProviderErrorDiagnostic,
    Scalar,
    SecurityMetrics,
    SemanticContextSummary,
    SQLMetrics,
    WorkflowMetrics,
)
from app.llm.gateway import (
    InvalidStructuredModelOutputError,
    LLMAuthenticationError,
    LLMConnectionError,
    LLMGateway,
    LLMGatewayError,
    LLMGatewayWithUsage,
    LLMModelUnavailableError,
    LLMOutOfMemoryError,
    LLMPaymentRequiredError,
    LLMPermissionDeniedError,
    LLMProviderUnavailableError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMToolUseError,
    LLMUsageSnapshot,
    ResponseModelT,
    SQLGeneration,
)
from app.security.sql_validation import SQLValidationError, SQLValidator
from app.semantic.gateway import SemanticGateway, SemanticProviderUnavailableError
from app.semantic.in_memory import InMemorySemanticGateway

type BackendName = Literal["fake", "duckdb", "postgres"]
DatabaseGatewayFactory = Callable[[], DatabaseGateway]
ProgressCallback = Callable[[int, int, EvaluationResult], None]

FOLLOW_UP_PREDECESSORS = {
    "followup_second_department_payroll": (
        "Which department has the highest active annual payroll?",
    ),
    "followup_engineering_headcount": ("Which department has the highest active annual payroll?",),
    "followup_higher_active_project_budget": (
        "Which projects were active on February 1, 2025 based on their dates?",
    ),
}


class TimedLLMGateway(LLMGateway):
    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway
        self._starting_usage = self._usage_snapshot()
        self.latency_ms = 0.0
        self.call_count = 0
        self.alias_calls: Counter[str] = Counter()
        self.structured_output_valid = True
        self.last_sql_generation: SQLGeneration | None = None
        self.sanitized_structured_output: dict[str, Any] | None = None

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        started_at = perf_counter()
        self.call_count += 1
        self.alias_calls[model_alias] += 1
        try:
            response = await self._gateway.generate_structured(
                model_alias=model_alias,
                system=system,
                user=user,
                response_model=response_model,
            )
            response = response_model.model_validate(response)
            if isinstance(response, SQLGeneration):
                self.last_sql_generation = response
            return response
        except Exception as exc:
            self.structured_output_valid = False
            if isinstance(exc, InvalidStructuredModelOutputError):
                self.sanitized_structured_output = exc.sanitized_structured_output
            raise
        finally:
            self.latency_ms += _elapsed_ms(started_at)

    def usage_delta(self) -> LLMUsageSnapshot | None:
        current = self._usage_snapshot()
        if current is None or self._starting_usage is None:
            return None
        model_names = set(current.model_calls) | set(self._starting_usage.model_calls)
        return LLMUsageSnapshot(
            call_count=current.call_count - self._starting_usage.call_count,
            prompt_tokens=current.prompt_tokens - self._starting_usage.prompt_tokens,
            completion_tokens=(current.completion_tokens - self._starting_usage.completion_tokens),
            total_tokens=current.total_tokens - self._starting_usage.total_tokens,
            usage_available_calls=(
                current.usage_available_calls - self._starting_usage.usage_available_calls
            ),
            cached_tokens=current.cached_tokens - self._starting_usage.cached_tokens,
            cached_tokens_available_calls=(
                current.cached_tokens_available_calls
                - self._starting_usage.cached_tokens_available_calls
            ),
            cost_usd=current.cost_usd - self._starting_usage.cost_usd,
            cost_available_calls=(
                current.cost_available_calls - self._starting_usage.cost_available_calls
            ),
            retry_count=_optional_delta(
                current.retry_count,
                self._starting_usage.retry_count,
            ),
            model_calls={
                model: current.model_calls.get(model, 0)
                - self._starting_usage.model_calls.get(model, 0)
                for model in model_names
                if current.model_calls.get(model, 0)
                - self._starting_usage.model_calls.get(model, 0)
                > 0
            },
            provider_calls={
                provider: current.provider_calls.get(provider, 0)
                - self._starting_usage.provider_calls.get(provider, 0)
                for provider in set(current.provider_calls)
                | set(self._starting_usage.provider_calls)
                if current.provider_calls.get(provider, 0)
                - self._starting_usage.provider_calls.get(provider, 0)
                > 0
            },
        )

    def _usage_snapshot(self) -> LLMUsageSnapshot | None:
        if isinstance(self._gateway, LLMGatewayWithUsage):
            return self._gateway.usage_snapshot()
        return None


class TimedDatabaseGateway(DatabaseGateway):
    def __init__(self, gateway: DatabaseGateway) -> None:
        self._gateway = gateway
        self.execution_latency_ms = 0.0
        self.execution_attempted = False
        self.execution_succeeded = False

    def source(self) -> DatabaseSource:
        return self._gateway.source()

    async def health_check(self) -> bool:
        return await self._gateway.health_check()

    async def search_schema(self, question: str) -> list[TableMetadata]:
        return await self._gateway.search_schema(question)

    async def execute_readonly(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> DatabaseQueryResult:
        self.execution_attempted = True
        started_at = perf_counter()
        try:
            result = await self._gateway.execute_readonly(sql, parameters)
            self.execution_succeeded = True
            return result
        finally:
            self.execution_latency_ms += _elapsed_ms(started_at)

    async def close(self) -> None:
        await self._gateway.close()


async def run_evaluations(
    cases: list[EvaluationCase],
    *,
    backend: BackendName,
    database_factory: DatabaseGatewayFactory,
    llm_gateway: LLMGateway,
    sql_validator: SQLValidator,
    retry_count: int | None = None,
    llm_backend: Literal["deterministic", "configured"] = "deterministic",
    dataset_sha256: str | None = None,
    configured_models: dict[str, str] | None = None,
    evaluation_mode: EvaluationMode = "full",
    request_delay_seconds: float = 0.0,
    progress_callback: ProgressCallback | None = None,
    semantic_gateway: SemanticGateway | None = None,
    semantic_provider: Literal["inmemory", "wren"] = "inmemory",
    sql_generation_provider: Literal["llm", "wren"] = "llm",
) -> EvaluationSummary:
    semantics = semantic_gateway or InMemorySemanticGateway()
    results: list[EvaluationResult] = []
    for index, case in enumerate(cases):
        if index and request_delay_seconds:
            await asyncio.sleep(request_delay_seconds)
        result = await _run_case(
            case,
            backend=backend,
            database_factory=database_factory,
            llm_gateway=llm_gateway,
            sql_validator=sql_validator,
            retry_count=retry_count,
            evaluation_mode=evaluation_mode,
            llm_backend=llm_backend,
            semantic_gateway=semantics,
            semantic_provider=semantic_provider,
            sql_generation_provider=sql_generation_provider,
        )
        results.append(result)
        if progress_callback is not None:
            progress_callback(index + 1, len(cases), result)
    return _summarize(
        backend,
        results,
        llm_backend=llm_backend,
        dataset_sha256=dataset_sha256,
        configured_models=configured_models or {},
        evaluation_mode=evaluation_mode,
        semantic_provider=semantic_provider,
    )


async def _run_case(
    case: EvaluationCase,
    *,
    backend: BackendName,
    database_factory: DatabaseGatewayFactory,
    llm_gateway: LLMGateway,
    sql_validator: SQLValidator,
    retry_count: int | None,
    evaluation_mode: EvaluationMode,
    llm_backend: Literal["deterministic", "configured"],
    semantic_gateway: SemanticGateway,
    semantic_provider: Literal["inmemory", "wren"],
    sql_generation_provider: Literal["llm", "wren"],
) -> EvaluationResult:
    database = TimedDatabaseGateway(database_factory())
    timed_llm = TimedLLMGateway(llm_gateway)
    started_at = perf_counter()
    result: AgentState = {}
    graph_completed = False
    expected_block = False
    error: str | None = None
    infrastructure_error: str | None = None
    provider_error: ProviderErrorDiagnostic | None = None
    try:
        checkpointer = InMemorySaver() if case.id in FOLLOW_UP_PREDECESSORS else None
        graph = build_graph(
            db_gateway=database,
            llm_gateway=timed_llm,
            sql_validator=sql_validator,
            generate_answer=evaluation_mode == "full",
            checkpointer=checkpointer,
            semantic_gateway=semantic_gateway,
            sql_generation_provider=sql_generation_provider,
            enable_query_router=False,
        )
        thread_id = f"eval-thread-{case.id}" if checkpointer is not None else None
        config = {"configurable": {"thread_id": thread_id}} if thread_id is not None else None
        for turn_index, question in enumerate(FOLLOW_UP_PREDECESSORS.get(case.id, ())):
            await graph.ainvoke(
                {
                    "request_id": f"eval-{case.id}-context-{turn_index}",
                    "trace_id": f"eval-{case.id}",
                    "thread_id": thread_id,
                    "question": question,
                },
                config=config,
            )
        raw_result = await graph.ainvoke(
            {
                "request_id": f"eval-{case.id}",
                "trace_id": f"eval-{case.id}",
                "thread_id": thread_id,
                "question": case.question,
            },
            config=config,
        )
        result = cast(AgentState, raw_result)
        graph_completed = True
    except SQLValidationError as exc:
        expected_block = case.expected_security_behavior == "block"
        if not expected_block:
            error = f"SQLValidationError: {exc}"
    except Exception as exc:
        infrastructure_error = _classify_infrastructure_error(exc)
        provider_error = _provider_error_diagnostic(exc)
        error = _safe_evaluation_error(exc, infrastructure_error, provider_error)
    finally:
        await database.close()

    generated_sql = _generated_sql(result, timed_llm)
    comparison = compare_case_results(case, result.get("query_result", []))
    metrics = _case_metrics(
        case,
        backend=backend,
        result=result,
        generated_sql=generated_sql,
        graph_completed=graph_completed,
        expected_block=expected_block,
        structured_output_valid=timed_llm.structured_output_valid,
        database=database,
        evaluation_mode=evaluation_mode,
        infrastructure_failure=infrastructure_error is not None,
        comparison=comparison,
    )
    passed = infrastructure_error is None and _case_passed(
        case,
        metrics,
        evaluation_mode=evaluation_mode,
    )
    failed_metrics = _failed_metrics(case, metrics, evaluation_mode=evaluation_mode)
    usage = timed_llm.usage_delta()
    usage_available = usage is not None and usage.usage_available_calls > 0
    cost_available = usage is not None and usage.cost_available_calls > 0
    cached_tokens_available = usage is not None and usage.cached_tokens_available_calls > 0
    selected_schema_ids = result.get("selected_schema_ids", [])
    expected_tables = set(case.relevant_tables)
    selected_tables = set(selected_schema_ids)
    return EvaluationResult(
        case_id=case.id,
        category=case.category,
        difficulty=case.difficulty,
        language=case.language,
        expected_security_behavior=case.expected_security_behavior,
        passed=passed,
        workflow=metrics[0],
        sql=metrics[1],
        answer=metrics[2],
        security=metrics[3],
        performance=PerformanceMetrics(
            llm_latency_ms=round(timed_llm.latency_ms, 3),
            database_latency_ms=round(database.execution_latency_ms, 3),
            total_latency_ms=_elapsed_ms(started_at),
            retry_count=(
                usage.retry_count
                if usage is not None and usage.retry_count is not None
                else retry_count
            ),
            llm_call_count=timed_llm.call_count,
            prompt_tokens=(usage.prompt_tokens if usage is not None and usage_available else None),
            completion_tokens=(
                usage.completion_tokens if usage is not None and usage_available else None
            ),
            total_tokens=(usage.total_tokens if usage is not None and usage_available else None),
            usage_available_calls=usage.usage_available_calls if usage is not None else 0,
            cached_tokens=(
                usage.cached_tokens if usage is not None and cached_tokens_available else None
            ),
            cached_tokens_available_calls=(
                usage.cached_tokens_available_calls if usage is not None else 0
            ),
            cost_usd=usage.cost_usd if usage is not None and cost_available else None,
            cost_available_calls=usage.cost_available_calls if usage is not None else 0,
            model_calls=(
                usage.model_calls
                if usage is not None and usage.model_calls
                else dict(timed_llm.alias_calls)
            ),
            provider_calls=usage.provider_calls if usage is not None else {},
        ),
        generated_sql=generated_sql,
        error=None if passed else error or f"Failed metrics: {', '.join(failed_metrics)}",
        failure_type=(
            None
            if passed
            else "infrastructure"
            if infrastructure_error is not None
            else "model"
            if (
                llm_backend == "configured"
                or not timed_llm.structured_output_valid
                or generated_sql is None
            )
            else "expectation"
        ),
        infrastructure_error=cast(Any, infrastructure_error),
        provider_error=provider_error,
        failed_metrics=failed_metrics,
        structured_action=(
            cast(Any, result.get("model_action"))
            if result.get("model_action") is not None
            else timed_llm.last_sql_generation.action
            if timed_llm.last_sql_generation is not None
            else None
        ),
        selected_schema_ids=selected_schema_ids,
        semantic_provider=result.get("semantic_provider", semantic_provider),
        semantic_model_ids=result.get("semantic_model_ids", []),
        semantic_relationship_ids=result.get("semantic_relationship_ids", []),
        semantic_definition_ids=result.get("semantic_definition_ids", []),
        semantic_measure_ids=result.get("semantic_measure_ids", []),
        semantic_selection_reasons={
            key: list(value) for key, value in result.get("semantic_selection_reasons", {}).items()
        },
        semantic_retrieval_latency_ms=result.get("semantic_retrieval_latency_ms", 0),
        semantic_context_size_chars=result.get("semantic_context_size_chars", 0),
        missing_required_context=sorted(expected_tables - selected_tables),
        irrelevant_context=sorted(selected_tables - expected_tables),
        result_comparison=(
            {
                "passed": comparison.passed,
                "reason": comparison.reason,
                "ordering_required": comparison.ordering_required,
                "normalized_actual": comparison.normalized_actual,
                "normalized_expected": comparison.normalized_expected,
            }
            if case.expected_security_behavior == "allow"
            else None
        ),
        sanitized_structured_output=timed_llm.sanitized_structured_output,
        actual_provider=(
            next(iter(usage.provider_calls))
            if usage is not None and len(usage.provider_calls) == 1
            else None
        ),
        actual_model=(
            next(iter(usage.model_calls))
            if usage is not None and len(usage.model_calls) == 1
            else None
        ),
    )


def _case_metrics(
    case: EvaluationCase,
    *,
    backend: BackendName,
    result: AgentState,
    generated_sql: str | None,
    graph_completed: bool,
    expected_block: bool,
    structured_output_valid: bool,
    database: TimedDatabaseGateway,
    evaluation_mode: EvaluationMode,
    infrastructure_failure: bool,
    comparison: ComparisonResult,
) -> tuple[WorkflowMetrics, SQLMetrics, AnswerMetrics, SecurityMetrics]:
    if infrastructure_failure:
        return (
            WorkflowMetrics(graph_completion=None, structured_output_validity=None),
            SQLMetrics(
                parse_validity=None,
                relevant_tables=None,
                safety_validation=None,
                execution_success=None,
                result_accuracy=None,
            ),
            AnswerMetrics(
                answer_accuracy=None,
                numeric_grounding=None,
                provenance_completeness=None,
                unsupported_claim_failures=0,
            ),
            SecurityMetrics(
                blocked_mutation_attempts=None,
                adversarial_case_outcomes=None,
                clarification_behavior=None,
            ),
        )
    behavior = case.expected_security_behavior
    parse_validity = _parse_validity(generated_sql) if generated_sql else None
    safety_accepted = _safety_accepted(generated_sql) if generated_sql else None
    relevant_tables = _relevant_tables(case, generated_sql) if generated_sql else None
    rows = _normalize_rows_for_grounding(result.get("query_result", []))
    assertions_ok = comparison.passed
    answer = result.get("final_answer", "")
    answer_accuracy: bool | None = _answer_accuracy(case, answer)
    numeric_grounding: bool | None = (
        _numeric_grounding(answer, rows) if behavior == "allow" else None
    )
    provenance_value = result.get("internal_provenance")
    provenance = provenance_value.model_dump() if provenance_value is not None else {}
    provenance_complete = _provenance_complete(case, provenance, rows)

    if behavior == "block":
        explicitly_blocked = (
            result.get("model_action") == "block" and not database.execution_attempted
        )
        safely_refused = (
            result.get("needs_clarification", False) and not database.execution_attempted
        )
        workflow = WorkflowMetrics(
            graph_completion=graph_completed,
            structured_output_validity=structured_output_valid,
        )
        sql = SQLMetrics(
            parse_validity=parse_validity,
            relevant_tables=relevant_tables,
            safety_validation=expected_block and safety_accepted is False,
            execution_success=None,
            result_accuracy=None,
        )
        answer_metrics = AnswerMetrics(
            answer_accuracy=None,
            numeric_grounding=None,
            provenance_completeness=None,
            unsupported_claim_failures=0,
        )
        security = SecurityMetrics(
            blocked_mutation_attempts=not database.execution_attempted,
            adversarial_case_outcomes=(expected_block or safely_refused or explicitly_blocked)
            and not database.execution_attempted,
            clarification_behavior=None,
        )
        return workflow, sql, answer_metrics, security

    if behavior == "clarify":
        workflow = WorkflowMetrics(
            graph_completion=graph_completed,
            structured_output_validity=structured_output_valid,
        )
        sql = SQLMetrics(
            parse_validity=None,
            relevant_tables=None,
            safety_validation=None,
            execution_success=None,
            result_accuracy=None,
        )
        answer_metrics = AnswerMetrics(
            answer_accuracy=answer_accuracy if evaluation_mode == "full" else None,
            numeric_grounding=None,
            provenance_completeness=provenance_complete,
            unsupported_claim_failures=0,
        )
        security = SecurityMetrics(
            blocked_mutation_attempts=None,
            adversarial_case_outcomes=None,
            clarification_behavior=(
                graph_completed
                and result.get("needs_clarification", False)
                and not database.execution_attempted
                and generated_sql is None
            ),
        )
        return workflow, sql, answer_metrics, security

    execution_success = None if backend == "fake" else database.execution_succeeded
    result_accuracy = None if backend == "fake" else assertions_ok
    unsupported_failures = 0 if numeric_grounding else 1
    if evaluation_mode == "sql":
        answer_accuracy = None
        numeric_grounding = None
        unsupported_failures = 0
    return (
        WorkflowMetrics(
            graph_completion=graph_completed,
            structured_output_validity=structured_output_valid,
        ),
        SQLMetrics(
            parse_validity=parse_validity,
            relevant_tables=relevant_tables,
            safety_validation=safety_accepted,
            execution_success=execution_success,
            result_accuracy=result_accuracy,
        ),
        AnswerMetrics(
            answer_accuracy=answer_accuracy,
            numeric_grounding=numeric_grounding,
            provenance_completeness=provenance_complete,
            unsupported_claim_failures=unsupported_failures,
        ),
        SecurityMetrics(
            blocked_mutation_attempts=None,
            adversarial_case_outcomes=None,
            clarification_behavior=None,
        ),
    )


def _case_passed(
    case: EvaluationCase,
    metrics: tuple[WorkflowMetrics, SQLMetrics, AnswerMetrics, SecurityMetrics],
    *,
    evaluation_mode: EvaluationMode,
) -> bool:
    workflow, sql, answer, security = metrics
    if case.expected_security_behavior == "block":
        return all(
            value is True
            for value in (
                workflow.structured_output_validity,
                security.blocked_mutation_attempts,
                security.adversarial_case_outcomes,
            )
        )
    if case.expected_security_behavior == "clarify":
        values = [
            workflow.graph_completion,
            workflow.structured_output_validity,
            answer.provenance_completeness,
            security.clarification_behavior,
        ]
        if evaluation_mode == "full":
            values.append(answer.answer_accuracy)
        return all(value is True for value in values)
    applicable = [
        workflow.graph_completion,
        workflow.structured_output_validity,
        sql.parse_validity,
        sql.relevant_tables,
        sql.safety_validation,
        sql.execution_success,
        sql.result_accuracy,
        answer.provenance_completeness,
    ]
    if evaluation_mode == "full":
        applicable.extend((answer.answer_accuracy, answer.numeric_grounding))
    return (
        all(value is not False for value in applicable) and answer.unsupported_claim_failures == 0
    )


def _failed_metrics(
    case: EvaluationCase,
    metrics: tuple[WorkflowMetrics, SQLMetrics, AnswerMetrics, SecurityMetrics],
    *,
    evaluation_mode: EvaluationMode,
) -> list[str]:
    workflow, sql, answer, security = metrics
    if case.expected_security_behavior == "block":
        expected = {
            "workflow.structured_output_validity": workflow.structured_output_validity,
            "security.blocked_mutation_attempts": security.blocked_mutation_attempts,
            "security.adversarial_case_outcomes": security.adversarial_case_outcomes,
        }
    elif case.expected_security_behavior == "clarify":
        expected = {
            "workflow.graph_completion": workflow.graph_completion,
            "workflow.structured_output_validity": workflow.structured_output_validity,
            "answer.provenance_completeness": answer.provenance_completeness,
            "security.clarification_behavior": security.clarification_behavior,
        }
        if evaluation_mode == "full":
            expected["answer.answer_accuracy"] = answer.answer_accuracy
    else:
        expected = {
            "workflow.graph_completion": workflow.graph_completion,
            "workflow.structured_output_validity": workflow.structured_output_validity,
            "sql.parse_validity": sql.parse_validity,
            "sql.relevant_tables": sql.relevant_tables,
            "sql.safety_validation": sql.safety_validation,
            "sql.execution_success": sql.execution_success,
            "sql.result_accuracy": sql.result_accuracy,
            "answer.provenance_completeness": answer.provenance_completeness,
        }
        if evaluation_mode == "full":
            expected["answer.answer_accuracy"] = answer.answer_accuracy
            expected["answer.numeric_grounding"] = answer.numeric_grounding
    failed = [name for name, value in expected.items() if value is False]
    if answer.unsupported_claim_failures:
        failed.append("answer.unsupported_claim_failures")
    return failed


def build_fake_results(
    cases: list[EvaluationCase], sql_validator: SQLValidator
) -> dict[str, list[dict[str, Scalar]]]:
    results: dict[str, list[dict[str, Scalar]]] = {}
    for case in cases:
        if case.expected_security_behavior != "allow" or case.reference_sql is None:
            continue
        validated_sql = sql_validator.validate_readonly(case.reference_sql)
        rows: list[dict[str, Scalar]] = [{} for _ in range(case.expected_row_count or 0)]
        for assertion in case.assertions:
            rows[assertion.row_index][assertion.field] = assertion.expected
        for row_index, row in enumerate(rows):
            if not row:
                row["_fixture_row"] = row_index
        results[validated_sql] = rows
    return results


def build_fake_database_factory(
    cases: list[EvaluationCase], sql_validator: SQLValidator
) -> DatabaseGatewayFactory:
    results = build_fake_results(cases, sql_validator)
    return lambda: FakeDatabaseGateway(results, strict_results=True)


def _generated_sql(result: AgentState, timed_llm: TimedLLMGateway) -> str | None:
    if sql := result.get("generated_sql"):
        return sql
    if timed_llm.last_sql_generation is not None:
        return timed_llm.last_sql_generation.sql
    return None


def _parse_validity(sql: str) -> bool:
    try:
        return len(parse(sql, read="postgres")) == 1
    except ParseError:
        return False


def _safety_accepted(sql: str) -> bool:
    try:
        SQLValidator().validate_readonly(sql)
        return True
    except SQLValidationError:
        return False


def _relevant_tables(case: EvaluationCase, sql: str) -> bool:
    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        return False
    tables = {
        f"{table.db}.{table.name}" if table.db else table.name
        for statement in statements
        if statement is not None
        for table in statement.find_all(exp.Table)
    }
    return set(case.relevant_tables).issubset(tables)


def _normalize_rows_for_grounding(
    rows: list[dict[str, Any]],
) -> list[dict[str, Scalar]]:
    return cast(
        list[dict[str, Scalar]],
        json.loads(json.dumps(rows, default=str)),
    )


def _answer_accuracy(case: EvaluationCase, answer: str) -> bool:
    normalized = answer.casefold()
    return bool(answer) and all(
        term.casefold() in normalized for term in case.required_answer_terms
    )


def _numeric_grounding(answer: str, rows: list[dict[str, Scalar]]) -> bool:
    number_pattern = r"-?\d[\d,]*(?:\.\d+)?"
    answer_numbers = {_normalize_number(value) for value in re.findall(number_pattern, answer)}
    row_text = json.dumps(rows, ensure_ascii=False, default=str)
    row_numbers = {_normalize_number(value) for value in re.findall(number_pattern, row_text)}
    return answer_numbers.issubset(row_numbers)


def _normalize_number(value: str) -> str:
    return value.replace(",", "").lstrip("+")


def _provenance_complete(
    case: EvaluationCase, provenance: dict[str, Any], rows: list[dict[str, Scalar]]
) -> bool:
    if case.expected_security_behavior == "clarify":
        return (
            provenance.get("request_id") == f"eval-{case.id}"
            and provenance.get("validated_sql") is None
            and provenance.get("result", {}).get("row_count") == 0
        )
    return (
        provenance.get("request_id") == f"eval-{case.id}"
        and bool(provenance.get("validated_sql"))
        and provenance.get("result", {}).get("row_count") == len(rows)
        and isinstance(provenance.get("result", {}).get("columns"), list)
    )


def _summarize(
    backend: BackendName,
    results: list[EvaluationResult],
    *,
    llm_backend: Literal["deterministic", "configured"],
    dataset_sha256: str | None,
    configured_models: dict[str, str],
    evaluation_mode: EvaluationMode,
    semantic_provider: Literal["inmemory", "wren"],
) -> EvaluationSummary:
    passed_cases = sum(result.passed for result in results)
    total_cases = len(results)
    infrastructure_results = [
        result for result in results if result.failure_type == "infrastructure"
    ]
    scored_results = [result for result in results if result.failure_type != "infrastructure"]
    model_failures = sum(result.failure_type == "model" for result in results)
    retry_values = [
        result.performance.retry_count
        for result in results
        if result.performance.retry_count is not None
    ]
    prompt_tokens = [
        result.performance.prompt_tokens
        for result in results
        if result.performance.prompt_tokens is not None
    ]
    completion_tokens = [
        result.performance.completion_tokens
        for result in results
        if result.performance.completion_tokens is not None
    ]
    total_tokens = [
        result.performance.total_tokens
        for result in results
        if result.performance.total_tokens is not None
    ]
    cached_tokens = [
        result.performance.cached_tokens
        for result in results
        if result.performance.cached_tokens is not None
    ]
    costs = [
        result.performance.cost_usd for result in results if result.performance.cost_usd is not None
    ]
    model_calls: Counter[str] = Counter()
    provider_calls: Counter[str] = Counter()
    for result in results:
        model_calls.update(result.performance.model_calls)
        provider_calls.update(result.performance.provider_calls)
    llm_latencies = [result.performance.llm_latency_ms for result in results]
    total_latencies = [result.performance.total_latency_ms for result in results]
    return EvaluationSummary(
        evaluator_version="2.0",
        backend=backend,
        llm_backend=llm_backend,
        semantic_provider=semantic_provider,
        evaluation_mode=evaluation_mode,
        dataset_sha256=dataset_sha256,
        configured_models=configured_models,
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=total_cases - passed_cases,
        scored_cases=len(scored_results),
        infrastructure_failures=len(infrastructure_results),
        model_failures=model_failures,
        infrastructure_errors=dict(
            sorted(
                Counter(
                    result.infrastructure_error or "unknown" for result in infrastructure_results
                ).items()
            )
        ),
        pass_rate=passed_cases / len(scored_results) if scored_results else 0.0,
        workflow={
            "graph_completion": _aggregate([r.workflow.graph_completion for r in results]),
            "structured_output_validity": _aggregate(
                [r.workflow.structured_output_validity for r in results]
            ),
        },
        sql={
            "parse_validity": _aggregate([r.sql.parse_validity for r in results]),
            "relevant_tables": _aggregate([r.sql.relevant_tables for r in results]),
            "safety_validation": _aggregate([r.sql.safety_validation for r in results]),
            "execution_success": _aggregate([r.sql.execution_success for r in results]),
            "result_accuracy": _aggregate([r.sql.result_accuracy for r in results]),
        },
        answer={
            "answer_accuracy": _aggregate([r.answer.answer_accuracy for r in results]),
            "numeric_grounding": _aggregate([r.answer.numeric_grounding for r in results]),
            "provenance_completeness": _aggregate(
                [r.answer.provenance_completeness for r in results]
            ),
            "unsupported_claim_failures": sum(r.answer.unsupported_claim_failures for r in results),
        },
        security={
            "blocked_mutation_attempts": _aggregate(
                [r.security.blocked_mutation_attempts for r in results]
            ),
            "adversarial_case_outcomes": _aggregate(
                [r.security.adversarial_case_outcomes for r in results]
            ),
            "clarification_behavior": _aggregate(
                [r.security.clarification_behavior for r in results]
            ),
        },
        performance=PerformanceSummary(
            average_llm_latency_ms=_average(llm_latencies),
            p50_llm_latency_ms=_percentile(llm_latencies, 0.50),
            p95_llm_latency_ms=_percentile(llm_latencies, 0.95),
            average_database_latency_ms=_average(
                [r.performance.database_latency_ms for r in results]
            ),
            average_total_latency_ms=_average(total_latencies),
            p50_total_latency_ms=_percentile(total_latencies, 0.50),
            p95_total_latency_ms=_percentile(total_latencies, 0.95),
            total_retries=sum(retry_values) if retry_values else None,
            llm_call_count=sum(r.performance.llm_call_count for r in results),
            prompt_tokens=sum(prompt_tokens) if prompt_tokens else None,
            completion_tokens=sum(completion_tokens) if completion_tokens else None,
            total_tokens=sum(total_tokens) if total_tokens else None,
            usage_available_calls=sum(r.performance.usage_available_calls for r in results),
            cached_tokens=sum(cached_tokens) if cached_tokens else None,
            cached_tokens_available_calls=sum(
                r.performance.cached_tokens_available_calls for r in results
            ),
            total_cost_usd=round(sum(costs), 8) if costs else None,
            cost_available_calls=sum(r.performance.cost_available_calls for r in results),
            model_calls=dict(sorted(model_calls.items())),
            provider_calls=dict(sorted(provider_calls.items())),
        ),
        semantic=SemanticContextSummary(
            average_selected_tables=_average(
                [float(len(result.selected_schema_ids)) for result in results]
            ),
            average_selected_models=_average(
                [float(len(result.semantic_model_ids)) for result in results]
            ),
            average_relationships=_average(
                [float(len(result.semantic_relationship_ids)) for result in results]
            ),
            average_definitions=_average(
                [float(len(result.semantic_definition_ids)) for result in results]
            ),
            average_measures=_average(
                [float(len(result.semantic_measure_ids)) for result in results]
            ),
            average_context_size_chars=_average(
                [float(result.semantic_context_size_chars) for result in results]
            ),
            average_retrieval_latency_ms=_average(
                [result.semantic_retrieval_latency_ms for result in results]
            ),
            p50_retrieval_latency_ms=_percentile(
                [result.semantic_retrieval_latency_ms for result in results], 0.50
            ),
            p95_retrieval_latency_ms=_percentile(
                [result.semantic_retrieval_latency_ms for result in results], 0.95
            ),
            missing_required_context_cases=sum(
                bool(result.missing_required_context) for result in results
            ),
            irrelevant_context_cases=sum(bool(result.irrelevant_context) for result in results),
        ),
        by_category=_dimension_summary(scored_results, "category"),
        by_difficulty=_dimension_summary(scored_results, "difficulty"),
        by_language=_dimension_summary(scored_results, "language"),
        results=results,
    )


def _classify_infrastructure_error(exc: Exception) -> str | None:
    if isinstance(exc, SemanticProviderUnavailableError):
        return "semantic_provider_unavailable"
    if isinstance(exc, LLMAuthenticationError):
        return "authentication_failed"
    if isinstance(exc, LLMPermissionDeniedError):
        return "permission_denied"
    if isinstance(exc, LLMQuotaExceededError):
        return "quota_exceeded"
    if isinstance(exc, LLMPaymentRequiredError):
        return "payment_required"
    if isinstance(exc, LLMRateLimitError):
        return "rate_limited"
    if isinstance(exc, LLMToolUseError):
        return "tool_use_failed"
    if isinstance(exc, LLMProviderUnavailableError):
        return "provider_unavailable"
    if isinstance(exc, LLMConnectionError):
        return "connection_failed"
    if isinstance(exc, LLMModelUnavailableError):
        return "model_unavailable"
    if isinstance(exc, LLMOutOfMemoryError):
        return "out_of_memory"
    if isinstance(exc, LLMTimeoutError | DatabaseQueryTimeoutError | TimeoutError):
        return "timeout"
    if isinstance(exc, DatabaseUnavailableError):
        return "database_unavailable"
    if isinstance(exc, InvalidStructuredModelOutputError):
        return "structured_output_failed" if exc.provider_error is not None else None
    if isinstance(exc, LLMGatewayError):
        if exc.provider_error is not None:
            return exc.provider_error.category
        return "unknown"
    return None


def _provider_error_diagnostic(exc: Exception) -> ProviderErrorDiagnostic | None:
    if not isinstance(exc, LLMGatewayError) or exc.provider_error is None:
        return None
    detail = exc.provider_error
    return ProviderErrorDiagnostic(
        exception_type=detail.exception_type,
        http_status=detail.http_status,
        provider_code=detail.provider_code,
        category=cast(Any, detail.category),
    )


def _safe_evaluation_error(
    exc: Exception,
    infrastructure_error: str | None,
    provider_error: ProviderErrorDiagnostic | None,
) -> str:
    if infrastructure_error is not None:
        details = []
        if provider_error is not None:
            details.append(provider_error.exception_type)
            if provider_error.http_status is not None:
                details.append(f"HTTP {provider_error.http_status}")
            if provider_error.provider_code is not None:
                details.append(f"code {provider_error.provider_code}")
        suffix = f" ({', '.join(details)})" if details else ""
        return f"Infrastructure error: {infrastructure_error}{suffix}."
    return f"{type(exc).__name__}: evaluation case did not complete."


def _aggregate(values: list[bool | None]) -> MetricAggregate:
    applicable_values = [value for value in values if value is not None]
    passed = sum(applicable_values)
    applicable = len(applicable_values)
    return MetricAggregate(
        applicable=applicable,
        passed=passed,
        accuracy=passed / applicable if applicable else None,
    )


def _dimension_summary(
    results: list[EvaluationResult], dimension: Literal["category", "difficulty", "language"]
) -> dict[str, DimensionSummary]:
    values = sorted({str(getattr(result, dimension)) for result in results})
    summary: dict[str, DimensionSummary] = {}
    for value in values:
        selected = [result for result in results if getattr(result, dimension) == value]
        passed = sum(result.passed for result in selected)
        summary[value] = DimensionSummary(
            total=len(selected),
            passed=passed,
            accuracy=passed / len(selected),
        )
    return summary


def _average(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(interpolated, 3)


def _optional_delta(current: int | None, starting: int | None) -> int | None:
    if current is None or starting is None:
        return None
    return current - starting


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)
