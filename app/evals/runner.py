import json
import re
from collections.abc import Callable
from time import perf_counter
from typing import Any, Literal, cast

from pydantic import TypeAdapter
from sqlglot import expressions as exp
from sqlglot import parse
from sqlglot.errors import ParseError

from app.agent.graph import build_graph
from app.agent.state import AgentState
from app.data.fake import FakeDatabaseGateway
from app.data.gateway import DatabaseGateway, TableMetadata
from app.evals.models import (
    AnswerMetrics,
    DimensionSummary,
    EvaluationCase,
    EvaluationResult,
    EvaluationSummary,
    MetricAggregate,
    PerformanceMetrics,
    PerformanceSummary,
    Scalar,
    SecurityMetrics,
    SQLMetrics,
    WorkflowMetrics,
)
from app.llm.gateway import LLMGateway, ResponseModelT, SQLGeneration
from app.security.sql_validation import SQLValidationError, SQLValidator

type BackendName = Literal["fake", "duckdb", "postgres"]
DatabaseGatewayFactory = Callable[[], DatabaseGateway]


class TimedLLMGateway(LLMGateway):
    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway
        self.latency_ms = 0.0
        self.structured_output_valid = True
        self.last_sql_generation: SQLGeneration | None = None

    async def generate_structured(
        self,
        *,
        model_alias: str,
        system: str,
        user: str,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        started_at = perf_counter()
        try:
            response = await self._gateway.generate_structured(
                model_alias=model_alias,
                system=system,
                user=user,
                response_model=response_model,
            )
            if isinstance(response, SQLGeneration):
                self.last_sql_generation = response
            return response
        except Exception:
            self.structured_output_valid = False
            raise
        finally:
            self.latency_ms += _elapsed_ms(started_at)


class TimedDatabaseGateway(DatabaseGateway):
    def __init__(self, gateway: DatabaseGateway) -> None:
        self._gateway = gateway
        self.execution_latency_ms = 0.0
        self.execution_attempted = False
        self.execution_succeeded = False

    async def health_check(self) -> bool:
        return await self._gateway.health_check()

    async def search_schema(self, question: str) -> list[TableMetadata]:
        return await self._gateway.search_schema(question)

    async def execute_readonly(self, sql: str) -> list[dict[str, Any]]:
        self.execution_attempted = True
        started_at = perf_counter()
        try:
            rows = await self._gateway.execute_readonly(sql)
            self.execution_succeeded = True
            return rows
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
) -> EvaluationSummary:
    results = [
        await _run_case(
            case,
            backend=backend,
            database_factory=database_factory,
            llm_gateway=llm_gateway,
            sql_validator=sql_validator,
            retry_count=retry_count,
        )
        for case in cases
    ]
    return _summarize(backend, results)


async def _run_case(
    case: EvaluationCase,
    *,
    backend: BackendName,
    database_factory: DatabaseGatewayFactory,
    llm_gateway: LLMGateway,
    sql_validator: SQLValidator,
    retry_count: int | None,
) -> EvaluationResult:
    database = TimedDatabaseGateway(database_factory())
    timed_llm = TimedLLMGateway(llm_gateway)
    started_at = perf_counter()
    result: AgentState = {}
    graph_completed = False
    expected_block = False
    error: str | None = None
    try:
        graph = build_graph(
            db_gateway=database,
            llm_gateway=timed_llm,
            sql_validator=sql_validator,
        )
        raw_result = await graph.ainvoke(
            {
                "request_id": f"eval-{case.id}",
                "trace_id": f"eval-{case.id}",
                "thread_id": None,
                "question": case.question,
            }
        )
        result = cast(AgentState, raw_result)
        graph_completed = True
    except SQLValidationError as exc:
        expected_block = case.expected_security_behavior == "block"
        if not expected_block:
            error = f"SQLValidationError: {exc}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        await database.close()

    generated_sql = _generated_sql(result, timed_llm)
    metrics = _case_metrics(
        case,
        backend=backend,
        result=result,
        generated_sql=generated_sql,
        graph_completed=graph_completed,
        expected_block=expected_block,
        structured_output_valid=timed_llm.structured_output_valid,
        database=database,
    )
    passed = _case_passed(case, metrics)
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
            retry_count=retry_count,
        ),
        generated_sql=generated_sql,
        error=None if passed else error or "One or more expected metrics failed.",
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
) -> tuple[WorkflowMetrics, SQLMetrics, AnswerMetrics, SecurityMetrics]:
    behavior = case.expected_security_behavior
    parse_validity = _parse_validity(generated_sql) if generated_sql else None
    safety_accepted = _safety_accepted(generated_sql) if generated_sql else None
    relevant_tables = _relevant_tables(case, generated_sql) if generated_sql else None
    rows = _normalize_rows(result.get("query_result", []))
    assertions_ok = _assertions_pass(case, rows)
    answer = result.get("final_answer", "")
    answer_accuracy = _answer_accuracy(case, answer)
    numeric_grounding = _numeric_grounding(answer, rows) if behavior == "allow" else None
    provenance = result.get("provenance", {})
    provenance_complete = _provenance_complete(case, provenance, rows)

    if behavior == "block":
        workflow = WorkflowMetrics(
            graph_completion=None,
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
            blocked_mutation_attempts=expected_block and not database.execution_attempted,
            adversarial_case_outcomes=expected_block and not database.execution_attempted,
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
            answer_accuracy=answer_accuracy,
            numeric_grounding=None,
            provenance_completeness=provenance_complete,
            unsupported_claim_failures=0,
        )
        security = SecurityMetrics(
            blocked_mutation_attempts=None,
            adversarial_case_outcomes=None,
        )
        return workflow, sql, answer_metrics, security

    execution_success = None if backend == "fake" else database.execution_succeeded
    result_accuracy = None if backend == "fake" else assertions_ok
    unsupported_failures = 0 if numeric_grounding else 1
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
        ),
    )


def _case_passed(
    case: EvaluationCase,
    metrics: tuple[WorkflowMetrics, SQLMetrics, AnswerMetrics, SecurityMetrics],
) -> bool:
    workflow, sql, answer, security = metrics
    if case.expected_security_behavior == "block":
        return all(
            value is True
            for value in (
                workflow.structured_output_validity,
                sql.safety_validation,
                security.blocked_mutation_attempts,
                security.adversarial_case_outcomes,
            )
        )
    if case.expected_security_behavior == "clarify":
        return all(
            value is True
            for value in (
                workflow.graph_completion,
                workflow.structured_output_validity,
                answer.answer_accuracy,
                answer.provenance_completeness,
            )
        )
    applicable = (
        workflow.graph_completion,
        workflow.structured_output_validity,
        sql.parse_validity,
        sql.relevant_tables,
        sql.safety_validation,
        sql.execution_success,
        sql.result_accuracy,
        answer.answer_accuracy,
        answer.numeric_grounding,
        answer.provenance_completeness,
    )
    return (
        all(value is not False for value in applicable)
        and answer.unsupported_claim_failures == 0
    )


def build_fake_results(
    cases: list[EvaluationCase], sql_validator: SQLValidator
) -> dict[str, list[dict[str, Scalar]]]:
    results: dict[str, list[dict[str, Scalar]]] = {}
    for case in cases:
        if case.expected_security_behavior != "allow" or case.reference_sql is None:
            continue
        validated_sql = sql_validator.validate_readonly(case.reference_sql)
        rows: list[dict[str, Scalar]] = [
            {} for _ in range(case.expected_row_count or 0)
        ]
        for assertion in case.assertions:
            rows[assertion.row_index][assertion.field] = assertion.expected
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


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Scalar]]:
    serialized = json.loads(json.dumps(rows, default=str))
    return TypeAdapter(list[dict[str, Scalar]]).validate_python(serialized)


def _assertions_pass(case: EvaluationCase, rows: list[dict[str, Scalar]]) -> bool:
    if case.expected_row_count != len(rows):
        return False
    for assertion in case.assertions:
        if (
            assertion.row_index >= len(rows)
            or assertion.field not in rows[assertion.row_index]
        ):
            return False
        actual = rows[assertion.row_index][assertion.field]
        if assertion.operator == "contains":
            if str(assertion.expected).casefold() not in str(actual).casefold():
                return False
        elif assertion.operator == "approx":
            try:
                if abs(float(cast(Any, actual)) - float(cast(Any, assertion.expected))) > (
                    case.numeric_tolerance
                ):
                    return False
            except (TypeError, ValueError):
                return False
        elif actual != assertion.expected:
            return False
    return True


def _answer_accuracy(case: EvaluationCase, answer: str) -> bool:
    normalized = answer.casefold()
    return bool(answer) and all(
        term.casefold() in normalized for term in case.required_answer_terms
    )


def _numeric_grounding(answer: str, rows: list[dict[str, Scalar]]) -> bool:
    number_pattern = r"-?\d[\d,]*(?:\.\d+)?"
    answer_numbers = {
        _normalize_number(value) for value in re.findall(number_pattern, answer)
    }
    row_text = json.dumps(rows, ensure_ascii=False, default=str)
    row_numbers = {
        _normalize_number(value) for value in re.findall(number_pattern, row_text)
    }
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
            and provenance.get("row_count") == 0
        )
    return (
        provenance.get("request_id") == f"eval-{case.id}"
        and bool(provenance.get("validated_sql"))
        and provenance.get("row_count") == len(rows)
        and isinstance(provenance.get("result_fields"), list)
    )


def _summarize(backend: BackendName, results: list[EvaluationResult]) -> EvaluationSummary:
    passed_cases = sum(result.passed for result in results)
    total_cases = len(results)
    retry_values = [
        result.performance.retry_count
        for result in results
        if result.performance.retry_count is not None
    ]
    return EvaluationSummary(
        backend=backend,
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=total_cases - passed_cases,
        pass_rate=passed_cases / total_cases if total_cases else 0.0,
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
            "unsupported_claim_failures": sum(
                r.answer.unsupported_claim_failures for r in results
            ),
        },
        security={
            "blocked_mutation_attempts": _aggregate(
                [r.security.blocked_mutation_attempts for r in results]
            ),
            "adversarial_case_outcomes": _aggregate(
                [r.security.adversarial_case_outcomes for r in results]
            ),
        },
        performance=PerformanceSummary(
            average_llm_latency_ms=_average([r.performance.llm_latency_ms for r in results]),
            average_database_latency_ms=_average(
                [r.performance.database_latency_ms for r in results]
            ),
            average_total_latency_ms=_average([r.performance.total_latency_ms for r in results]),
            total_retries=sum(retry_values) if retry_values else None,
        ),
        by_category=_dimension_summary(results, "category"),
        by_difficulty=_dimension_summary(results, "difficulty"),
        by_language=_dimension_summary(results, "language"),
        results=results,
    )


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


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)
